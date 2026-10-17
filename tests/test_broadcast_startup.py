import unittest
import threading
from unittest.mock import patch

from radiostudio.services.broadcast import (
    BroadcastManager, EncoderOutput, LocalStreamServer, RemoteSink,
)


class _FragmentedSocket:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = []
        self.closed = False

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


class BroadcastStartupTests(unittest.TestCase):
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
