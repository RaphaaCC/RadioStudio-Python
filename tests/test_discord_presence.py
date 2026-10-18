import threading
import time
import unittest

from pypresence import ActivityType, StatusDisplayType

from radiostudio.config import DISCORD_APPLICATION_ID
from radiostudio.services.discord_presence import (
    DiscordPresenceService, make_activity, valid_discord_app_id,
    unknown_presence_placeholders, validate_public_cover_url,
    validate_public_radio_url,
)


class FakeRPC:
    def __init__(self, client_id):
        self.client_id = client_id
        self.updates = []
        self.clears = 0
        self.closed = False
        self.activity_updated = threading.Event()

    def connect(self):
        return None

    def update(self, **activity):
        self.updates.append(activity)
        self.activity_updated.set()

    def clear(self):
        self.clears += 1

    def close(self):
        self.closed = True


class QuickDiscordPresenceService(DiscordPresenceService):
    MIN_UPDATE_INTERVAL = 0.01
    RETRY_INTERVAL = 0.02


class DiscordPresenceTests(unittest.TestCase):
    def test_radio_studio_uses_its_registered_discord_application(self):
        self.assertEqual(DISCORD_APPLICATION_ID, "1561176476290908250")

    def test_app_id_requires_numeric_discord_snowflake(self):
        self.assertTrue(valid_discord_app_id("123456789012345678"))
        self.assertFalse(valid_discord_app_id("not-an-id"))
        self.assertFalse(valid_discord_app_id("1234"))

    def test_radio_link_must_be_public_and_without_credentials_or_tokens(self):
        self.assertEqual(validate_public_radio_url("https://radio.example/live"),
                         "https://radio.example/live")
        self.assertEqual(validate_public_radio_url(""), "")
        for url in ("file:///music.mp3", "http://localhost:8090/live.mp3",
                    "http://192.168.1.4:8090/live.mp3", "https://radio.example/live?token=x",
                    "https://user:password@radio.example/live"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_public_radio_url(url)

    def test_cover_url_must_be_public_and_leave_space_for_cache_key(self):
        self.assertEqual(validate_public_cover_url("https://radio.example/cover"),
                         "https://radio.example/cover")
        for url in ("http://localhost:8090/cover", "http://192.168.1.5/cover",
                    "https://radio.example/cover?token=secret",
                    "https://user:password@radio.example/cover",
                    "https://radio.example/" + "x" * 230):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_public_cover_url(url)

    def test_activity_shares_track_and_optional_public_radio_button(self):
        activity = make_activity(
            title="Shape of You", artist="Ed Sheeran", station="Raemi FM",
            broadcasting=True, playing=True, share_track=True,
            public_url="https://radio.example/live", started_at=100, ends_at=220)
        self.assertEqual(activity["details"], "🎵 Shape of You")
        self.assertEqual(activity["name"], "RadioStudio Python Sirius")
        self.assertEqual(activity["activity_type"], 2)
        self.assertEqual(activity["state"], "Ed Sheeran · Raemi FM · No ar")
        self.assertEqual((activity["start"], activity["end"]), (100000, 220000))
        self.assertEqual(activity["buttons"], [{"label": "Ouvir a rádio",
                                                 "url": "https://radio.example/live"}])
        self.assertEqual(activity["large_image"], "radiostudio")

    def test_activity_respects_track_privacy_and_stops_when_idle(self):
        activity = make_activity(title="Private song", artist="Private artist",
                                 station="My station", broadcasting=True,
                                 playing=True, share_track=False)
        self.assertNotIn("Private song", str(activity))
        self.assertNotIn("Private artist", str(activity))
        self.assertEqual(activity["details"], "📻 RadioStudio Python")
        self.assertEqual(make_activity(title="Track", playing=False), {})

    def test_activity_uses_public_current_cover_with_cache_busting(self):
        activity = make_activity(
            title="Track", playing=True, share_track=True,
            cover_url="https://radio.example/cover", cover_version="a1b2c3d4e5f60718")
        self.assertEqual(activity["large_image"],
                         "https://radio.example/cover?rs_cover=a1b2c3d4e5f60718")
        missing_art = make_activity(
            title="Track", playing=True, share_track=True,
            cover_url="https://radio.example/cover", cover_version="not-a-hash")
        self.assertEqual(missing_art["large_image"], "radiostudio")

        private = make_activity(
            title="Track", playing=True, share_track=False,
            cover_url="https://radio.example/cover", cover_version="a1b2c3d4e5f60718")
        self.assertEqual(private["large_image"], "radiostudio")
        self.assertNotIn("radio.example/cover", str(private))

    def test_activity_supports_custom_type_templates_tooltip_and_two_buttons(self):
        activity = make_activity(
            title="Shape of You", artist="Ed Sheeran", album="Divide", station="Raemi FM",
            playing=True, share_track=True, activity_name="Raemi FM",
            activity_type=0, status_display_type=2,
            details_template="🎧 %title — %album",
            state_template="%artist · %station · %status", large_text="Ouça Raemi FM",
            button1_label="Ouvir ao vivo", public_url="https://radio.example/live",
            button2_label="Site da rádio", button2_url="https://radio.example")
        self.assertEqual(activity["name"], "Raemi FM")
        self.assertEqual(activity["activity_type"], 0)
        self.assertEqual(activity["status_display_type"], 2)
        self.assertEqual(activity["details"], "🎧 Shape of You — Divide")
        self.assertEqual(activity["state"], "Ed Sheeran · Raemi FM · Tocando")
        self.assertEqual(activity["large_text"], "Ouça Raemi FM")
        self.assertEqual(activity["buttons"], [
            {"label": "Ouvir ao vivo", "url": "https://radio.example/live"},
            {"label": "Site da rádio", "url": "https://radio.example"},
        ])

    def test_custom_templates_do_not_leak_track_metadata_when_private(self):
        activity = make_activity(
            title="Private track", artist="Private artist", album="Private album",
            station="Public station", broadcasting=True, playing=True, share_track=False,
            details_template="%title — %album", state_template="%artist · %station · %status")
        self.assertEqual(activity["details"], "📻 RadioStudio Python")
        self.assertEqual(activity["state"], "Public station · No ar")
        self.assertNotIn("Private", str(activity))

    def test_presence_templates_report_unknown_tokens(self):
        self.assertEqual(unknown_presence_placeholders("%title · %foo · %bar · %foo"),
                         ["foo", "bar"])
        self.assertEqual(unknown_presence_placeholders("%title / %artist / %album"), [])

    def test_rpc_converts_status_display_type_to_pypresence_enum(self):
        rpc = FakeRPC("test")
        DiscordPresenceService._update_rpc(rpc, {
            "activity_type": 2, "status_display_type": 1, "details": "Track"})
        self.assertIs(rpc.updates[0]["activity_type"], ActivityType.LISTENING)
        self.assertIs(rpc.updates[0]["status_display_type"], StatusDisplayType.STATE)

    def test_rpc_registration_gates_availability_and_shutdown_clears_activity(self):
        instances = []

        def factory(client_id):
            rpc = FakeRPC(client_id)
            instances.append(rpc)
            return rpc

        service = QuickDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.configure("123456789012345678", enabled=False)
        deadline = time.monotonic() + 2
        while not service.available and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(service.available, service.status)
        self.assertIn("registrada", service.status)
        rpc = instances[0]
        self.assertEqual(rpc.updates[0]["state"], "Conexão verificada")
        self.assertIs(rpc.updates[0]["activity_type"], ActivityType.LISTENING)
        self.assertGreaterEqual(rpc.clears, 1)

        service.configure("123456789012345678", enabled=True)
        service.set_activity({"details": "🎵 Faixa de teste"})
        self.assertTrue(rpc.activity_updated.wait(1))
        deadline = time.monotonic() + 1
        while (len(rpc.updates) < 2 and time.monotonic() < deadline):
            time.sleep(0.01)
        self.assertEqual(rpc.updates[-1], {"details": "🎵 Faixa de teste"})
        service.shutdown()
        self.assertTrue(rpc.closed)
        self.assertGreaterEqual(rpc.clears, 2)

    def test_failed_rpc_does_not_report_available(self):
        class BrokenRPC(FakeRPC):
            def connect(self):
                raise OSError("Discord indisponível")

        service = QuickDiscordPresenceService(rpc_factory=BrokenRPC)
        self.addCleanup(service.shutdown)
        service.configure("123456789012345678")
        time.sleep(0.05)
        self.assertFalse(service.available)
        self.assertIn("Falha na comunicação", service.status)

    def test_shutdown_during_handshake_does_not_register_late_presence(self):
        connecting = threading.Event()
        release_connect = threading.Event()
        instances = []

        class BlockingRPC(FakeRPC):
            def connect(self):
                connecting.set()
                release_connect.wait(2)

        def factory(client_id):
            rpc = BlockingRPC(client_id)
            instances.append(rpc)
            return rpc

        service = QuickDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.configure("123456789012345678")
        self.assertTrue(connecting.wait(1), "RPC handshake did not start")

        stopper = threading.Thread(target=service.shutdown)
        stopper.start()
        deadline = time.monotonic() + 1
        try:
            while not service._stopping and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertTrue(service._stopping)
        finally:
            release_connect.set()
        stopper.join(1)
        self.assertFalse(stopper.is_alive())
        self.assertTrue(instances[0].closed)
        self.assertEqual(instances[0].updates, [])
        self.assertFalse(service.available)

    def test_handshake_timeout_reports_that_discord_did_not_respond(self):
        self.assertIn("não respondeu", DiscordPresenceService._connection_error_status(
            TimeoutError()))


if __name__ == "__main__":
    unittest.main()
