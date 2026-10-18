import json
import math
import struct
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from radiostudio.services.broadcast import (
    FRAME_MS, PCM_BYTES_PER_FRAME, SAMPLE_RATE, BroadcastManager, EncoderOutput,
    LocalStreamServer, RemoteSink,
)


class _FragmentedSocket:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = []
        self.closed = False
        self.shutdown_called = False

    def settimeout(self, _timeout):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        data = bytes(self.response[:size])
        del self.response[:size]
        return data

    def close(self):
        self.closed = True

    def shutdown(self, _how):
        self.shutdown_called = True


class _BlockingSocket(_FragmentedSocket):
    def __init__(self):
        super().__init__(b"")
        self.send_started = threading.Event()
        self.release_send = threading.Event()

    def sendall(self, data):
        self.sent.append(data)
        self.send_started.set()
        self.release_send.wait(1)

    def shutdown(self, how):
        super().shutdown(how)
        self.release_send.set()


class BroadcastStartupTests(unittest.TestCase):
    def test_embedded_stream_delivers_live_mp3_before_source_ends(self):
        server = LocalStreamServer("127.0.0.1", 0, 2, "Synthetic")
        server.start()
        output = EncoderOutput("Servidor embutido", 192, server)
        output.start()
        response = None
        feeder_stop = threading.Event()
        feeder = None
        try:
            deadline = time.monotonic() + 5
            while output.status != "Ativo" and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(output.status, "Ativo", output.error)
            port = server.server.server_address[1]
            response = urlopen(f"http://127.0.0.1:{port}/live.mp3", timeout=4)

            def feed_synthetic_pcm():
                samples_per_frame = PCM_BYTES_PER_FRAME // 4
                for frame in range(200):
                    if feeder_stop.is_set():
                        return
                    first_sample = frame * samples_per_frame
                    samples = (int(5000 * math.sin(
                        2 * math.pi * 440 * (first_sample + index) / SAMPLE_RATE))
                        for index in range(samples_per_frame))
                    pcm = b"".join(struct.pack("<hh", sample, sample) for sample in samples)
                    output.feed(pcm)
                    time.sleep(FRAME_MS / 1000)

            feeder = threading.Thread(target=feed_synthetic_pcm, daemon=True)
            feeder.start()
            started = time.monotonic()
            encoded_audio = response.read(1024)
            elapsed = time.monotonic() - started

            self.assertEqual(response.status, 200)
            self.assertTrue(encoded_audio.startswith(b"\xff"))
            self.assertLess(elapsed, 3)
            self.assertEqual(output.dropped_encoded_chunks, 0)
        finally:
            feeder_stop.set()
            if response:
                response.close()
            if feeder:
                feeder.join(timeout=1)
            output.stop()
            if output.thread:
                output.thread.join(timeout=3)
            server.stop()

    def test_embedded_status_endpoint_returns_audio_diagnostics(self):
        server = LocalStreamServer("127.0.0.1", 0, 10, "Test")
        server.diagnostics_provider = lambda: {"mixer": {"overruns": 0}}
        server.start()
        try:
            port = server.server.server_address[1]
            with urlopen(f"http://127.0.0.1:{port}/status.json", timeout=2) as response:
                data = json.load(response)
            self.assertEqual(data["audio_diagnostics"]["mixer"]["overruns"], 0)
        finally:
            server.stop()

    def test_embedded_server_serves_cover_only_when_enabled(self):
        cover = b"test-image-bytes"
        server = LocalStreamServer("127.0.0.1", 0, 10, "Test", send_cover_art=True)
        server.set_cover_art(cover, "image/png")
        server.start()
        try:
            base_url = f"http://127.0.0.1:{server.server.server_address[1]}"
            with urlopen(base_url + "/status.json", timeout=2) as response:
                status = json.load(response)
            self.assertEqual(status["cover_url"], "/cover")
            self.assertEqual(status["cover_mime_type"], "image/png")
            with urlopen(base_url + status["cover_url"], timeout=2) as response:
                self.assertEqual(response.headers.get_content_type(), "image/png")
                self.assertEqual(response.read(), cover)
            cache_busted_cover = base_url + status["cover_url"] + "?rs_cover=cache-key"
            with urlopen(cache_busted_cover, timeout=2) as response:
                self.assertEqual(response.read(), cover)
        finally:
            server.stop()

    def test_embedded_server_hides_cover_when_disabled(self):
        server = LocalStreamServer("127.0.0.1", 0, 10, "Test", send_cover_art=False)
        server.set_cover_art(b"must-not-leak", "image/jpeg")
        server.start()
        try:
            base_url = f"http://127.0.0.1:{server.server.server_address[1]}"
            with urlopen(base_url + "/status.json", timeout=2) as response:
                status = json.load(response)
            self.assertIsNone(status["cover_url"])
            self.assertEqual(status["cover_mime_type"], "")
            with self.assertRaises(HTTPError) as error:
                urlopen(base_url + "/cover", timeout=2)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        finally:
            server.stop()

    def test_cover_delivery_can_be_toggled_without_losing_current_art(self):
        server = LocalStreamServer("127.0.0.1", 0, 10, "Test", send_cover_art=True)
        manager = BroadcastManager()
        manager.local_server = server
        manager.cover_art_data = b"current-cover"
        manager.cover_art_mime_type = "image/jpeg"
        manager.set_cover_art(manager.cover_art_data, manager.cover_art_mime_type)

        manager.set_cover_art_enabled(False)
        self.assertFalse(server.send_cover_art)
        self.assertIsNone(server.cover_art_data)
        manager.set_cover_art_enabled(True)
        self.assertTrue(server.send_cover_art)
        self.assertEqual(server.cover_art_data, b"current-cover")
        self.assertEqual(server.cover_art_mime_type, "image/jpeg")

    def test_shoutcast_authentication_accepts_fragmented_status_line(self):
        sock = _FragmentedSocket(b"OK2\n")
        sink = RemoteSink({"kind": "shoutcast", "host": "localhost", "port": 8000,
                           "stream_id": 1, "station": "Test", "genre": "", "url": "",
                           "bitrate": 128}, "secret")
        with patch("radiostudio.services.broadcast.socket.create_connection", return_value=sock):
            sink.connect()

        self.assertIs(sink.sock, sock)
        self.assertEqual(sock.sent[0], b"secret\n")
        self.assertIn(b"icy-name:Test", sock.sent[1])

    def test_icecast_uses_raw_put_body_with_expect_continue(self):
        sock = _FragmentedSocket(b"HTTP/1.1 100 Continue\r\n\r\n")
        sink = RemoteSink({"kind": "icecast", "host": "localhost", "port": 8000,
                           "mount": "/live.mp3", "station": "Test", "genre": "",
                           "url": "", "description": ""}, "secret")
        with patch("radiostudio.services.broadcast.socket.create_connection", return_value=sock):
            sink.connect()
        try:
            sink.publish(b"mp3-frame")
            request = sock.sent[0]
            self.assertTrue(request.startswith(b"PUT /live.mp3 HTTP/1.1"))
            self.assertIn(b"Expect: 100-continue", request)
            self.assertNotIn(b"Transfer-Encoding: chunked", request)
            self.assertEqual(sock.sent[1], b"mp3-frame")
        finally:
            sink.close()

    def test_icecast_accepts_success_without_continue_response(self):
        sock = _FragmentedSocket(b"HTTP/1.0 200 OK\r\n\r\n")
        sink = RemoteSink({"kind": "icecast", "host": "localhost", "port": 8000,
                           "mount": "/live.mp3"}, "secret")
        with patch("radiostudio.services.broadcast.socket.create_connection", return_value=sock):
            sink.connect()
        self.assertIs(sink.sock, sock)
        sink.close()

    def test_abort_remote_icecast_does_not_write_chunk_terminator(self):
        sock = _FragmentedSocket(b"")
        sink = RemoteSink({"kind": "icecast", "id": "icecast-1"}, "secret")
        sink.sock = sock

        sink.close(graceful=False)

        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.shutdown_called)
        self.assertTrue(sock.closed)

    def test_close_interrupts_blocked_writer_before_stream_shutdown(self):
        sock = _BlockingSocket()
        sink = RemoteSink({"kind": "icecast", "id": "icecast-2"}, "secret")
        sink.sock = sock
        output = EncoderOutput("Icecast", 128, sink)
        writer = threading.Thread(target=sink.publish, args=(b"audio",))
        output.writer_thread = writer
        writer.start()
        self.assertTrue(sock.send_started.wait(1))

        output._close_connection()

        self.assertFalse(writer.is_alive())
        self.assertTrue(sock.shutdown_called)
        self.assertEqual(sock.sent, [b"audio"])
        self.assertTrue(sock.closed)

    def test_graceful_icecast_close_finishes_unframed_request(self):
        sock = _FragmentedSocket(b"")
        sink = RemoteSink({"kind": "icecast"}, "secret")
        sink.sock = sock

        sink.close()

        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.shutdown_called)
        self.assertTrue(sock.closed)

    def test_publish_after_close_reports_connection_error(self):
        sink = RemoteSink({"kind": "shoutcast"}, "secret")

        with self.assertRaisesRegex(ConnectionError, "Conexão de transmissão encerrada"):
            sink.publish(b"audio")

    def test_missing_remote_password_does_not_block_embedded_output(self):
        manager = BroadcastManager()
        profile = {"id": "profile-1", "enabled": True, "name": "Icecast",
                   "kind": "icecast", "host": "example.invalid", "port": 8000,
                   "mount": "/live.mp3", "bitrate": 128}
        with patch("radiostudio.services.broadcast.get_password", return_value=None), \
                patch.object(LocalStreamServer, "start"), \
                patch.object(EncoderOutput, "start"):
            manager.start([profile], {"enabled": True, "port": 8090, "bitrate": 128})
            try:
                self.assertEqual(len(manager.outputs), 1)
                self.assertEqual(manager.outputs[0].name, "Servidor embutido")
                self.assertIn("profile-1", manager.startup_errors)
            finally:
                manager.stop()

    def test_credential_store_failure_does_not_block_embedded_output(self):
        manager = BroadcastManager()
        profile = {"id": "profile-1", "enabled": True, "name": "Icecast",
                   "kind": "icecast", "host": "example.invalid", "port": 8000,
                   "mount": "/live.mp3", "bitrate": 128}
        with patch("radiostudio.services.broadcast.get_password",
                   side_effect=OSError("credential store unavailable")), \
                patch.object(LocalStreamServer, "start"), \
                patch.object(EncoderOutput, "start"):
            manager.start([profile], {"enabled": True, "port": 8090, "bitrate": 128})
            try:
                self.assertEqual([output.name for output in manager.outputs], ["Servidor embutido"])
                self.assertIn("Credencial indisponível", manager.startup_errors["profile-1"])
            finally:
                manager.stop()

    def test_reports_missing_credential_when_it_is_the_only_destination(self):
        manager = BroadcastManager()
        profile = {"id": "profile-1", "enabled": True, "name": "Icecast",
                   "kind": "icecast", "host": "example.invalid", "port": 8000,
                   "mount": "/live.mp3", "bitrate": 128}
        with patch("radiostudio.services.broadcast.get_password", return_value=None):
            with self.assertRaisesRegex(ValueError, "Icecast.*Senha ausente"):
                manager.start([profile], {"enabled": False})

    def test_embedded_startup_failure_does_not_block_remote_output(self):
        manager = BroadcastManager()
        profile = {"id": "profile-1", "enabled": True, "name": "Icecast",
                   "kind": "icecast", "host": "example.invalid", "port": 8000,
                   "mount": "/live.mp3", "bitrate": 128}
        with patch("radiostudio.services.broadcast.get_password", return_value="secret"), \
                patch.object(LocalStreamServer, "start", side_effect=OSError("bind failed")), \
                patch.object(LocalStreamServer, "stop") as stop_local, \
                patch.object(EncoderOutput, "start"):
            manager.start([profile], {"enabled": True, "port": 8090, "bitrate": 128})
            try:
                self.assertEqual([output.name for output in manager.outputs], ["Icecast"])
                self.assertEqual(manager.startup_errors["local"], "bind failed")
                stop_local.assert_called_once()
            finally:
                manager.stop()

    def test_embedded_listener_closes_socket_if_serve_thread_cannot_start(self):
        server = LocalStreamServer("127.0.0.1", 0, 10, "Test")
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("thread unavailable")):
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                server.start()
        self.assertIsNone(server.server)
        self.assertIsNone(server.thread)


if __name__ == "__main__":
    unittest.main()
