import base64
import http.client
import json
import socket
import threading
import time
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.services.control_api import (
    API_PREFIX, APIError, APICommandBridge, ControlAPIServer, _DispatchRequest,
    _ControlHTTPServer,
    get_api_basic_credentials, openapi_document, parse_allowed_origins,
    set_api_basic_credentials,
)
from radiostudio.ui.main_window import RadioStudio
from radiostudio.ui.playlist import PlaylistMixin


class ControlAPIServerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.server = ControlAPIServer(self.dispatch)
        self.server.start(
            host="127.0.0.1", port=0, auth_mode="token", token="test-api-token",
            allowed_origins=["http://localhost:3000"],
        )
        self.port = self.server.port

    def tearDown(self):
        self.server.stop()

    def dispatch(self, operation, payload):
        self.calls.append((operation, payload))
        if operation == "status":
            return {"playback_state": "stopped", "position_ms": 0,
                    "duration_ms": 0, "current_track": None,
                    "queue_length": 0, "queue_revision": 7}
        if operation == "queue":
            return {"revision": 7, "items": []}
        if operation == "playback":
            return {"action": payload["action"]}
        if operation == "reorder_queue":
            if payload["revision"] != 7:
                raise APIError(409, "stale_queue_revision", "Queue changed.")
            if payload["order"] != [1, 0]:
                raise APIError(409, "invalid_order", "Order is not a permutation.")
            return {"revision": 8, "items": []}
        raise AssertionError(operation)

    def request(self, method, path, *, body=None, authorization="Bearer test-api-token",
                origin=None, content_type="application/json"):
        headers = {}
        if authorization:
            headers["Authorization"] = authorization
        if origin:
            headers["Origin"] = origin
        if body is not None:
            headers["Content-Type"] = content_type
            if not isinstance(body, bytes):
                body = json.dumps(body).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            decoded = json.loads(payload) if payload else None
            return response.status, dict(response.getheaders()), decoded
        finally:
            connection.close()

    def test_bearer_auth_status_and_queue_do_not_expose_local_paths(self):
        status, headers, body = self.request("GET", f"{API_PREFIX}/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["playback_state"], "stopped")
        self.assertNotIn("path", body)
        self.assertEqual(headers["Cache-Control"], "no-store")

        status, _headers, body = self.request("GET", f"{API_PREFIX}/queue")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"revision": 7, "items": []})

    def test_authentication_is_required_and_token_is_compared(self):
        for authorization in (None, "Bearer wrong"):
            with self.subTest(authorization=authorization):
                status, headers, body = self.request(
                    "GET", f"{API_PREFIX}/status", authorization=authorization)
                self.assertEqual(status, 401)
                self.assertEqual(headers["WWW-Authenticate"], "Bearer")
                self.assertEqual(body["error"]["code"], "unauthorized")

        self.assertEqual(self.request("DELETE", f"{API_PREFIX}/status",
                                      authorization=None)[0], 401)
        self.assertEqual(self.request("DELETE", f"{API_PREFIX}/status")[0], 405)

    def test_basic_authentication_mode(self):
        self.server.stop()
        self.server.start(host="127.0.0.1", port=0, auth_mode="basic",
                          username="studio", password="s3cret")
        self.port = self.server.port
        good = "Basic " + base64.b64encode(b"studio:s3cret").decode("ascii")
        bad = "Basic " + base64.b64encode(b"studio:wrong").decode("ascii")
        self.assertEqual(self.request("GET", f"{API_PREFIX}/status",
                                      authorization=good)[0], 200)
        self.assertEqual(self.request("GET", f"{API_PREFIX}/status",
                                      authorization=bad)[0], 401)
        status, _headers, document = self.request(
            "GET", f"{API_PREFIX}/openapi.json", authorization=good)
        self.assertEqual(status, 200)
        self.assertEqual(document["paths"]["/status"]["get"]["security"],
                         [{"BasicAuth": []}])

    def test_playback_json_and_invalid_request_handling(self):
        status, _headers, body = self.request(
            "POST", f"{API_PREFIX}/playback", body={"action": "next"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"action": "next"})
        self.assertEqual(self.calls[-1], ("playback", {"action": "next"}))

        status, _headers, body = self.request(
            "POST", f"{API_PREFIX}/playback", body={"action": "launch"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_action")

        status, _headers, body = self.request(
            "POST", f"{API_PREFIX}/playback", body={"action": []})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_action")

        status, _headers, body = self.request(
            "POST", f"{API_PREFIX}/playback", body=b"{")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_json")

    def test_early_http_error_closes_connection_before_unread_body_can_leak(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(
                "POST", f"{API_PREFIX}/playback", body=b'{"action":"next"}',
                headers={"Authorization": "Bearer test-api-token",
                         "Content-Type": "text/plain"})
            response = connection.getresponse()
            self.assertEqual(response.status, 415)
            self.assertEqual(response.getheader("Connection"), "close")
            response.read()

            # http.client reconnects after Connection: close; the unread body
            # from the rejected POST cannot be mistaken for a new request line.
            connection.request(
                "GET", f"{API_PREFIX}/status",
                headers={"Authorization": "Bearer test-api-token"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        finally:
            connection.close()

    def test_cors_allowlist_and_preflight(self):
        status, headers, _body = self.request(
            "OPTIONS", f"{API_PREFIX}/playback", authorization=None,
            origin="http://localhost:3000")
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])

        status, _headers, body = self.request(
            "GET", f"{API_PREFIX}/status", origin="http://evil.example")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "origin_not_allowed")

        status, headers, _body = self.request(
            "GET", f"{API_PREFIX}/status", origin="http://localhost:3000")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:3000")

    def test_queue_order_conflicts_are_returned_as_409(self):
        status, _headers, body = self.request(
            "PUT", f"{API_PREFIX}/queue/order", body={"revision": 7, "order": [1, 0]})
        self.assertEqual(status, 200)
        self.assertEqual(body["revision"], 8)

        status, _headers, body = self.request(
            "PUT", f"{API_PREFIX}/queue/order", body={"revision": 6, "order": [1, 0]})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "stale_queue_revision")

        status, _headers, body = self.request(
            "PUT", f"{API_PREFIX}/queue/order", body={"revision": 7, "order": [1, 1]})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "invalid_order")

    def test_cors_validation_requires_an_explicit_valid_origin(self):
        self.assertEqual(parse_allowed_origins([
            "HTTPS://Example.COM:443/", "http://[::1]:8080"]),
            ("https://example.com", "http://[::1]:8080"))
        for value in ("*", "https://example.com/path", "http://bad host:3000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_allowed_origins([value])

    def test_openapi_document_is_authenticated_and_serializable(self):
        encoded = json.dumps(openapi_document())
        self.assertIn("/queue/order", encoded)
        checked_in = json.loads((Path(__file__).resolve().parents[1]
                                 / "docs" / "rest_api.openapi.json").read_text(encoding="utf-8"))
        self.assertEqual(checked_in, openapi_document())
        status, _headers, body = self.request("GET", f"{API_PREFIX}/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(body, openapi_document("token"))
        self.assertEqual(body["paths"]["/status"]["get"]["security"],
                         [{"BearerAuth": []}])

    def test_occupied_port_fails_and_stop_releases_listener(self):
        duplicate = ControlAPIServer(lambda *_args: {})
        try:
            with self.assertRaises(OSError):
                duplicate.start(host="127.0.0.1", port=self.port,
                                auth_mode="token", token="another-token")
        finally:
            duplicate.stop()

        self.server.stop()
        self.assertFalse(self.server.running)
        self.server.start(host="127.0.0.1", port=self.port,
                          auth_mode="token", token="test-api-token")
        self.assertTrue(self.server.running)

    def test_server_closes_excess_connections_and_releases_client_slots(self):
        original_limit = _ControlHTTPServer.MAX_CONCURRENT_CLIENTS
        _ControlHTTPServer.MAX_CONCURRENT_CLIENTS = 1
        server = ControlAPIServer(lambda *_args: {})
        first = second = None
        try:
            server.start(host="127.0.0.1", port=0,
                          auth_mode="token", token="test-token")
            first = socket.create_connection(("127.0.0.1", server.port), timeout=2)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if not server._server._request_slots.acquire(blocking=False):
                    break
                server._server._request_slots.release()
                time.sleep(0.01)
            else:
                self.fail("The first slow connection did not occupy a request slot")

            second = socket.create_connection(("127.0.0.1", server.port), timeout=2)
            second.settimeout(2)
            self.assertEqual(second.recv(1), b"")

            first.close()
            first = None
            self.assertTrue(server._server._request_slots.acquire(timeout=2))
            server._server._request_slots.release()
        finally:
            if first is not None:
                first.close()
            if second is not None:
                second.close()
            server.stop()
            _ControlHTTPServer.MAX_CONCURRENT_CLIENTS = original_limit

    def test_credential_helpers_reject_malformed_and_ambiguous_values(self):
        with patch("radiostudio.services.control_api.keyring.get_password", return_value="[]"):
            self.assertIsNone(get_api_basic_credentials())
        with self.assertRaises(ValueError):
            set_api_basic_credentials("bad:name", "secret")


class ControlAPIApplicationBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_bridge_runs_application_callback_on_qt_thread(self):
        gui_thread = threading.get_ident()
        seen = []
        bridge = APICommandBridge(
            lambda operation, payload: seen.append((operation, payload, threading.get_ident()))
            or {"ok": True})
        result = []
        worker = threading.Thread(
            target=lambda: result.append(bridge.dispatch("status", {"x": 1})))
        worker.start()
        deadline = time.monotonic() + 3
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            worker.join(0.01)
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [{"ok": True}])
        self.assertEqual(seen, [("status", {"x": 1}, gui_thread)])

    def test_queued_command_that_times_out_is_not_executed_later(self):
        seen = []
        bridge = APICommandBridge(lambda operation, payload: seen.append((operation, payload))
                                  or {"ok": True}, timeout=0.1)
        result = []

        def dispatch():
            try:
                bridge.dispatch("playback", {"action": "next"})
            except Exception as exc:
                result.append(exc)

        worker = threading.Thread(target=dispatch)
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], APIError)
        self.assertEqual(result[0].status, 504)
        self.app.processEvents()
        self.assertEqual(seen, [])

    def test_started_command_waits_for_result_instead_of_timing_out_after_mutation(self):
        entered = threading.Event()
        release = threading.Event()
        seen = []

        def handler(operation, payload):
            entered.set()
            release.wait(timeout=2)
            seen.append((operation, payload))
            return {"ok": True}

        bridge = APICommandBridge(handler, timeout=0.1)
        result = []
        worker = threading.Thread(target=lambda: result.append(
            bridge.dispatch("playback", {"action": "next"})))
        worker.start()
        releaser = threading.Thread(target=lambda: (entered.wait(timeout=1),
                                                     time.sleep(0.2), release.set()))
        releaser.start()
        deadline = time.monotonic() + 2
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            worker.join(0.01)
        worker.join(timeout=1)
        releaser.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [{"ok": True}])
        self.assertEqual(seen, [("playback", {"action": "next"})])

    def test_reconfiguration_rejects_already_queued_requests(self):
        seen = []
        bridge = APICommandBridge(lambda operation, payload: seen.append((operation, payload))
                                  or {"ok": True})
        request = _DispatchRequest("playback", {"action": "next"},
                                   generation=bridge._generation)
        bridge.requested.emit(request)
        bridge.set_active(False)
        self.app.processEvents()
        self.assertTrue(request.done.is_set())
        self.assertIsInstance(request.error, APIError)
        self.assertEqual(request.error.status, 503)
        self.assertEqual(seen, [])

    def test_main_window_configuration_starts_separately_authenticated_api(self):
        fake = SimpleNamespace(
            control_api=ControlAPIServer(lambda operation, _payload: {"operation": operation}),
            control_api_config={"enabled": True, "port": 0, "auth_mode": "token",
                                "allowed_origins": []},
        )
        with patch("radiostudio.ui.main_window.get_api_token", return_value="integration-token"):
            started, error = RadioStudio._configure_control_api_server(fake)
        try:
            self.assertTrue(started, error)
            connection = http.client.HTTPConnection("127.0.0.1", fake.control_api.port, timeout=3)
            connection.request("GET", f"{API_PREFIX}/status",
                               headers={"Authorization": "Bearer integration-token"})
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(body, {"operation": "status"})
        finally:
            fake.control_api.stop()

    def test_playback_actions_call_only_the_matching_main_window_control(self):
        harness = SimpleNamespace(
            is_quitting=False,
            player=SimpleNamespace(playbackState=lambda: QMediaPlayer.PausedState),
            pause=Mock(), stop=Mock(), next_track=Mock(), play_index=Mock(),
            current_index=1, tracks=["a.mp3", "b.mp3"],
            _control_api_status=Mock(return_value={"playback_state": "paused"}),
        )
        dispatch = MethodType(RadioStudio._handle_control_api_request, harness)

        dispatch("playback", {"action": "play"})
        harness.pause.assert_called_once_with()
        harness.player.playbackState = lambda: QMediaPlayer.PlayingState
        dispatch("playback", {"action": "pause"})
        self.assertEqual(harness.pause.call_count, 2)
        harness.stop.assert_not_called()

        dispatch("playback", {"action": "stop"})
        dispatch("playback", {"action": "next"})
        harness.stop.assert_called_once_with()
        harness.next_track.assert_called_once_with()

        harness.player.playbackState = lambda: QMediaPlayer.StoppedState
        dispatch("playback", {"action": "play"})
        harness.play_index.assert_called_once_with(1)

    def make_queue_harness(self):
        class Table:
            def item(self, row, column):
                values = (("A", "Artist A", "Album A"),
                          ("B", "Artist B", "Album B"),
                          ("A", "Artist A", "Album A"))
                return SimpleNamespace(text=lambda: values[row][column])

        harness = SimpleNamespace(
            is_quitting=False, tracks=["a.mp3", "b.mp3", "a.mp3"],
            track_sources=["manual", "manual", "manual"], current_index=0,
            playlist=Table(), _api_queue_signature=None, _api_queue_revision=0,
            cancel_pending_transition=Mock(), refresh_playlist=Mock(), save_state=Mock(),
        )
        harness._control_api_queue_item = MethodType(RadioStudio._control_api_queue_item, harness)
        harness._control_api_queue_revision_value = MethodType(
            RadioStudio._control_api_queue_revision_value, harness)
        harness._control_api_queue = MethodType(RadioStudio._control_api_queue, harness)
        harness.commit_playlist_order = MethodType(PlaylistMixin.commit_playlist_order, harness)
        return harness

    def test_queue_revision_changes_when_current_occurrence_moves_among_duplicate_paths(self):
        harness = self.make_queue_harness()
        dispatch = MethodType(RadioStudio._handle_control_api_request, harness)
        before = dispatch("queue", {})
        self.assertTrue(before["items"][0]["is_current"])
        self.assertEqual(before["current_track"]["position"], 0)
        self.assertTrue(all("path" not in item for item in before["items"]))

        after = dispatch("reorder_queue", {"revision": before["revision"], "order": [2, 1, 0]})
        self.assertEqual(harness.current_index, 2)
        self.assertGreater(after["revision"], before["revision"])
        self.assertTrue(after["items"][2]["is_current"])
        self.assertEqual(after["current_track"]["position"], 2)

    def test_metadata_refresh_does_not_change_queue_order_revision(self):
        harness = self.make_queue_harness()
        revision = harness._control_api_queue_revision_value()
        harness.playlist.item = lambda _row, _column: SimpleNamespace(
            text=lambda: "Updated metadata")
        self.assertEqual(harness._control_api_queue_revision_value(), revision)

    def test_old_revision_and_invalid_permutation_are_conflicts(self):
        harness = self.make_queue_harness()
        dispatch = MethodType(RadioStudio._handle_control_api_request, harness)
        revision = dispatch("queue", {})["revision"]
        harness.current_index = 1
        with self.assertRaises(APIError) as stale:
            dispatch("reorder_queue", {"revision": revision, "order": [0, 1, 2]})
        self.assertEqual(stale.exception.status, 409)

        current_revision = harness._control_api_queue_revision_value()
        for order in ([0, 0, 2], [0, "1", 2]):
            with self.subTest(order=order), self.assertRaises(APIError) as invalid:
                dispatch("reorder_queue", {"revision": current_revision, "order": order})
            self.assertEqual(invalid.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
