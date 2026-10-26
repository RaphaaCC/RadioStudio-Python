import threading
import time
import unittest

from pypresence import ActivityType, StatusDisplayType

from radiostudio.config import DISCORD_APPLICATION_ID
from radiostudio.i18n import current_language, set_language
from radiostudio.services.discord_presence import (
    DiscordPresenceService, make_activity, valid_discord_app_id,
    playback_start_timestamp, unknown_presence_placeholders, validate_public_button_url,
    validate_public_cover_url,
    validate_public_radio_url,
)


class FakeRPC:
    def __init__(self, client_id):
        self.client_id = client_id
        self.updates = []
        self.clears = 0
        self.closed = False
        self.rpc_call_times = []
        self.activity_updated = threading.Event()
        self.activity_cleared = threading.Event()
        self.fail_clear = False

    def connect(self):
        return None

    def update(self, **activity):
        self.rpc_call_times.append(time.monotonic())
        self.updates.append(activity)
        self.activity_updated.set()

    def clear(self):
        self.rpc_call_times.append(time.monotonic())
        if self.fail_clear:
            raise ConnectionError("Discord IPC closed")
        self.clears += 1
        self.activity_cleared.set()

    def close(self):
        self.closed = True


class QuickDiscordPresenceService(DiscordPresenceService):
    MIN_UPDATE_INTERVAL = 0.01
    MIN_RPC_CALL_INTERVAL = 0.01
    RETRY_INTERVAL = 0.02


class SlowUpdateDiscordPresenceService(DiscordPresenceService):
    MIN_UPDATE_INTERVAL = 5.0
    MIN_RPC_CALL_INTERVAL = 0.05
    RETRY_INTERVAL = 0.02


class SlowRetryDiscordPresenceService(DiscordPresenceService):
    MIN_UPDATE_INTERVAL = 5.0
    RETRY_INTERVAL = 5.0


class HeartbeatDiscordPresenceService(DiscordPresenceService):
    MIN_UPDATE_INTERVAL = 0.01
    MIN_RPC_CALL_INTERVAL = 0.01
    HEARTBEAT_INTERVAL = 0.05
    RETRY_INTERVAL = 0.01


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
        self.assertEqual(validate_public_radio_url("https://münchen.example/live"),
                         "https://münchen.example/live")
        self.assertEqual(validate_public_radio_url(""), "")
        for url in ("file:///music.mp3", "http://localhost:8090/live.mp3",
                    "http://192.168.1.4:8090/live.mp3", "https://radio.example/live?token=x",
                    "https://user:password@radio.example/live",
                    "https://bad_domain.example/live", "https://-bad.example/live",
                    "https://bad-.example/live", "https://bad domain.example/live",
                    "https://radio..example/live", "https://999.1.1.1/live",
                    "https://0x7f.0.0.1/live",
                    "https://radio.example:/live", "https://radio.example/live path"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_public_radio_url(url)

    def test_discord_button_url_obeys_the_512_character_contract(self):
        valid = "https://radio.example/" + "x" * (512 - len("https://radio.example/"))
        too_long = valid + "x"
        self.assertEqual(len(validate_public_button_url(valid)), 512)
        with self.assertRaisesRegex(ValueError, "512"):
            validate_public_button_url(too_long)

        activity = make_activity(
            title="Track", playing=True, public_url=too_long,
            button2_label="Station site", button2_url="https://radio.example")
        self.assertEqual(activity["buttons"], [
            {"label": "Station site", "url": "https://radio.example"},
        ])

    def test_invalid_optional_cover_does_not_suppress_valid_activity_or_button(self):
        activity = make_activity(
            title="Track", playing=True, public_url="https://radio.example/live",
            cover_url="http://localhost/cover", cover_version="a1b2c3")
        self.assertEqual(activity["large_image"], "radiostudio")
        self.assertEqual(activity["buttons"], [{
            "label": "Ouvir a rádio", "url": "https://radio.example/live",
        }])

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
        self.assertNotIn("start", activity)
        self.assertNotIn("end", activity)
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

        too_long_cache_key = make_activity(
            title="Track", playing=True, share_track=True,
            cover_url="https://radio.example/" + "x" * 208,
            cover_version="a" * 32)
        self.assertEqual(too_long_cache_key["large_image"], "radiostudio")

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

        blank_label = make_activity(
            title="Track", playing=True, button1_label="   ",
            public_url="https://radio.example/live")
        self.assertEqual(blank_label["buttons"], [{
            "label": "Ouvir a rádio", "url": "https://radio.example/live",
        }])

    def test_default_presence_status_and_button_follow_the_ui_language(self):
        previous_language = current_language()
        try:
            set_language("en_US")
            activity = make_activity(
                title="Track", broadcasting=True, playing=True,
                details_template="%title", state_template="%status",
                button1_label="", public_url="https://radio.example/live")
            self.assertEqual(activity["state"], "On air")
            self.assertEqual(activity["buttons"][0]["label"], "Listen to the radio")
        finally:
            set_language(previous_language)

    def test_discord_rejecting_short_presence_fields_use_safe_fallbacks(self):
        activity = make_activity(
            title="Track", playing=True, details_template="♫", state_template="1",
            large_text="x")
        self.assertEqual(activity["details"], "🎵 Track")
        self.assertNotIn("state", activity)
        self.assertNotIn("large_text", activity)

        two_character_text = make_activity(
            title="Track", playing=True, details_template="♫♪",
            state_template="DJ", large_text="FM")
        self.assertEqual(two_character_text["details"], "♫♪")
        self.assertEqual(two_character_text["state"], "DJ")
        self.assertEqual(two_character_text["large_text"], "FM")

    def test_activity_text_collapses_whitespace_from_track_metadata(self):
        activity = make_activity(
            title="  Track\x00\nwith\todd   spacing\x7f  ", playing=True,
            details_template="%title", state_template="%artist")
        self.assertEqual(activity["details"], "Track with odd spacing")
        self.assertNotRegex(activity["details"], r"[\x00-\x1f\x7f-\x9f]")

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

    def test_playback_start_timestamp_tracks_resume_and_seek_positions(self):
        initial = playback_start_timestamp(0, now=1000.5)
        self.assertEqual(initial, 1000)
        self.assertEqual(playback_start_timestamp(
            1000, now=1001.5, previous_start=initial), initial)
        resumed = playback_start_timestamp(
            30_000, now=1105.0, previous_start=initial, force_reset=True)
        self.assertEqual(resumed, 1075)
        seeked = playback_start_timestamp(
            15_000, now=1200.0, previous_start=resumed)
        self.assertEqual(seeked, 1185)

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
        service.configure("123456789012345678", enabled=False, verify=True)
        deadline = time.monotonic() + 2
        while not service.available and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(service.available, service.status)
        self.assertFalse(service.connected)
        self.assertIn("verificada", service.status)
        self.assertIn("desativada", service.status)
        rpc = instances[0]
        self.assertEqual(rpc.updates, [])
        self.assertEqual(rpc.clears, 0)

        service.configure("123456789012345678", enabled=True)
        service.set_activity({"details": "🎵 Faixa de teste"})
        deadline = time.monotonic() + 1
        while ((len(instances) < 2 or not instances[-1].updates)
               and time.monotonic() < deadline):
            time.sleep(0.01)
        self.assertGreaterEqual(len(instances), 2)
        self.assertTrue(service.connected)
        rpc = instances[-1]
        self.assertTrue(rpc.activity_updated.is_set())
        self.assertEqual(rpc.updates[-1], {"details": "🎵 Faixa de teste"})
        service.shutdown()
        self.assertFalse(service.connected)
        self.assertTrue(all(instance.closed for instance in instances))
        self.assertGreaterEqual(sum(instance.clears for instance in instances), 1)

    def test_disabled_presence_does_not_connect_until_requested_verification(self):
        instances = []

        def factory(client_id):
            rpc = FakeRPC(client_id)
            instances.append(rpc)
            return rpc

        service = QuickDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.configure("123456789012345678", enabled=False)
        time.sleep(0.05)
        self.assertEqual(instances, [])

        service.configure("123456789012345678", enabled=False, verify=True)
        deadline = time.monotonic() + 1
        while not service.available and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(service.available, service.status)
        self.assertEqual(len(instances), 1)
        self.assertTrue(instances[0].closed)
        self.assertFalse(service.connected)
        self.assertEqual(instances[0].updates, [])

    def test_verifying_an_enabled_service_keeps_presence_enabled_and_connected(self):
        instances = []

        def factory(client_id):
            rpc = FakeRPC(client_id)
            instances.append(rpc)
            return rpc

        service = QuickDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.configure(DISCORD_APPLICATION_ID, enabled=True, verify=True)
        deadline = time.monotonic() + 1
        while not service.connected and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertTrue(service.enabled)
        self.assertTrue(service.connected)
        self.assertEqual(len(instances), 1)
        self.assertFalse(instances[0].closed)
        self.assertFalse(service._verification_requested)

    def test_explicit_verification_bypasses_automatic_retry_backoff(self):
        attempts = []

        def factory(client_id):
            attempts.append(client_id)
            if len(attempts) == 1:
                raise OSError("temporary IPC failure")
            return FakeRPC(client_id)

        service = SlowRetryDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.configure(DISCORD_APPLICATION_ID, enabled=True)
        deadline = time.monotonic() + 1
        while (not attempts or "Falha na comunicação" not in service.status) \
                and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(len(attempts), 1)

        service.configure(DISCORD_APPLICATION_ID, enabled=True, verify=True)
        deadline = time.monotonic() + 1
        while not service.connected and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertTrue(service.connected)
        self.assertEqual(len(attempts), 2)

    def test_failed_manual_verification_returns_to_normal_retry_backoff(self):
        attempts = []

        def factory(_client_id):
            attempts.append(time.monotonic())
            raise OSError("Discord is still unavailable")

        service = SlowRetryDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.configure(DISCORD_APPLICATION_ID, enabled=True, verify=True)
        deadline = time.monotonic() + 1
        while (not attempts or "Falha na comunicação" not in service.status) \
                and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(len(attempts), 1)
        time.sleep(0.1)
        self.assertEqual(len(attempts), 1)

    def test_idle_connection_does_not_publish_a_fake_activity(self):
        instances = []

        def factory(client_id):
            rpc = FakeRPC(client_id)
            instances.append(rpc)
            return rpc

        service = SlowUpdateDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.configure("123456789012345678", enabled=True)
        deadline = time.monotonic() + 1
        while (not service.available or not instances) and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(service.available, service.status)
        self.assertTrue(service.connected)
        self.assertEqual(instances[0].updates, [])
        self.assertIn("aguardando reprodução", service.status)

        service.set_activity({"details": "🎵 Faixa de teste"})
        deadline = time.monotonic() + 0.5
        while not instances[0].updates and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(instances[0].updates[-1], {"details": "🎵 Faixa de teste"})
        deadline = time.monotonic() + 0.5
        while "registrada" not in service.status and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertIn("registrada", service.status)

        service.set_activity({})
        deadline = time.monotonic() + 0.5
        while not instances[0].clears and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertGreaterEqual(instances[0].clears, 1)
        deadline = time.monotonic() + 0.5
        while "aguardando reprodução" not in service.status and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertIn("aguardando reprodução", service.status)

    def test_idle_connection_heartbeat_detects_a_closed_discord_pipe(self):
        instances = []

        def factory(client_id):
            rpc = FakeRPC(client_id)
            instances.append(rpc)
            return rpc

        service = HeartbeatDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.configure("123456789012345678", enabled=True)
        deadline = time.monotonic() + 1
        while (not instances or not instances[0].activity_cleared.is_set()) \
                and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(instances and instances[0].activity_cleared.is_set())
        instances[0].fail_clear = True

        deadline = time.monotonic() + 1
        while len(instances) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertGreaterEqual(len(instances), 2)
        self.assertTrue(service.connected)
        self.assertTrue(instances[0].closed)
        self.assertIn("aguardando reprodução", service.status)

    def test_activity_changed_during_handshake_is_not_sent_from_old_snapshot(self):
        instances = []
        service_holder = {}

        class ChangeActivityDuringConnectRPC(FakeRPC):
            def connect(self):
                service_holder["service"].set_activity(
                    {"details": "📻 RadioStudio", "state": "No ar"})

        def factory(client_id):
            rpc = ChangeActivityDuringConnectRPC(client_id)
            instances.append(rpc)
            return rpc

        service = QuickDiscordPresenceService(rpc_factory=factory)
        service_holder["service"] = service
        self.addCleanup(service.shutdown)
        service.set_activity({"details": "🎵 Faixa compartilhada"})
        service.configure(DISCORD_APPLICATION_ID, enabled=True)

        deadline = time.monotonic() + 1
        while (not service.available or not instances or not instances[0].updates) \
                and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertTrue(service.available, service.status)
        self.assertEqual(instances[0].updates[0],
                         {"details": "📻 RadioStudio", "state": "No ar"})
        self.assertNotIn("Faixa compartilhada", str(instances[0].updates))

    def test_new_activity_after_clear_does_not_wait_for_old_rate_limit(self):
        instances = []

        def factory(client_id):
            rpc = FakeRPC(client_id)
            instances.append(rpc)
            return rpc

        service = SlowUpdateDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.configure("123456789012345678", enabled=True)
        service.set_activity({"details": "Primeira faixa"})
        deadline = time.monotonic() + 1
        while (not service.available or not instances or not instances[0].updates) \
                and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(service.available, service.status)
        self.assertEqual(instances[0].updates[0], {"details": "Primeira faixa"})

        service.set_activity({})
        deadline = time.monotonic() + 0.5
        while not instances[0].clears and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertGreaterEqual(instances[0].clears, 1)
        service.set_activity({"details": "Segunda faixa"})
        deadline = time.monotonic() + 0.5
        while len(instances[0].updates) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertGreaterEqual(len(instances[0].updates), 2)
        self.assertEqual(instances[0].updates[-1], {"details": "Segunda faixa"})

    def test_urgent_activity_update_obeys_rpc_rate_limit_and_hides_track_metadata(self):
        instances = []

        def factory(client_id):
            rpc = FakeRPC(client_id)
            instances.append(rpc)
            return rpc

        service = SlowUpdateDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.set_activity({"details": "🎵 Faixa privada"})
        service.configure(DISCORD_APPLICATION_ID, enabled=True)
        deadline = time.monotonic() + 1
        while (not service.available or not instances or not instances[0].updates) \
                and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(service.available, service.status)

        safe_activity = {"details": "📻 RadioStudio Python"}
        service.set_activity(safe_activity, urgent=True)
        latest_safe_activity = {"details": "📻 RadioStudio"}
        service.set_activity(latest_safe_activity, urgent=True)
        deadline = time.monotonic() + 0.5
        while len(instances[0].updates) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(instances[0].updates[-1], latest_safe_activity)
        self.assertGreaterEqual(len(instances[0].updates), 2)
        self.assertTrue(all(later - earlier >= 0.045 for earlier, later in zip(
            instances[0].rpc_call_times, instances[0].rpc_call_times[1:])))

    def test_heartbeat_reconnects_after_discord_closes_its_ipc_channel(self):
        instances = []

        class DisconnectOnHeartbeatRPC(FakeRPC):
            fail_next_update = False

            def update(self, **activity):
                if self.fail_next_update:
                    self.fail_next_update = False
                    raise OSError("Discord IPC disconnected")
                super().update(**activity)

        def factory(client_id):
            rpc = DisconnectOnHeartbeatRPC(client_id)
            instances.append(rpc)
            return rpc

        service = HeartbeatDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.set_activity({"details": "🎵 Faixa de teste"})
        service.configure(DISCORD_APPLICATION_ID, enabled=True)
        deadline = time.monotonic() + 1
        while (not service.available or not instances or not instances[0].updates) \
                and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(service.available, service.status)
        instances[0].fail_next_update = True

        deadline = time.monotonic() + 1
        while len(instances) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertGreaterEqual(len(instances), 2)
        self.assertTrue(instances[1].updates)
        self.assertEqual(instances[1].updates[0], {"details": "🎵 Faixa de teste"})
        self.assertTrue(service.available, service.status)

    def test_failed_rpc_does_not_report_available(self):
        class BrokenRPC(FakeRPC):
            def connect(self):
                raise OSError("Discord indisponível")

        instances = []

        def factory(client_id):
            rpc = BrokenRPC(client_id)
            instances.append(rpc)
            return rpc

        service = QuickDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.configure("123456789012345678", verify=True)
        time.sleep(0.05)
        self.assertFalse(service.available)
        self.assertIn("Falha na comunicação", service.status)
        self.assertEqual(len(instances), 1)

    def test_failed_initial_registration_clears_possible_stale_activity(self):
        class AcknowledgementFailureRPC(FakeRPC):
            def update(self, **activity):
                self.updates.append(activity)
                raise TimeoutError("acknowledgement timed out")

        instances = []

        def factory(client_id):
            rpc = AcknowledgementFailureRPC(client_id)
            instances.append(rpc)
            return rpc

        service = SlowRetryDiscordPresenceService(rpc_factory=factory)
        self.addCleanup(service.shutdown)
        service.set_activity({"details": "Track"})
        service.configure(DISCORD_APPLICATION_ID, enabled=True)

        deadline = time.monotonic() + 1
        while (not instances or not instances[0].closed) and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].clears, 1)
        self.assertTrue(instances[0].closed)
        self.assertFalse(service.available)

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
        service.configure("123456789012345678", verify=True)
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
