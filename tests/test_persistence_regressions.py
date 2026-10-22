import unittest
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.ui.persistence import PersistenceMixin, _state_bool
import radiostudio.ui.persistence as persistence


class _Player:
    def __init__(self, path, position, state):
        self.path = path
        self._position = position
        self._state = state

    def playbackState(self):
        return self._state

    def position(self):
        return self._position

    def source(self):
        return QUrl.fromLocalFile(self.path)


class _PlaybackCaptureHarness(PersistenceMixin):
    def __init__(self, mix_state):
        self.player = _Player("outgoing.mp3", 179000, QMediaPlayer.PausedState)
        self.mix_player = _Player("incoming.mp3", 42000, mix_state)
        self.mix_target_index = 1
        self.mix_gain = 0.9
        self.main_gain = 0.1
        self.tracks = ["outgoing.mp3", "incoming.mp3"]
        self.decoded_audio_sources = {}
        self.last_playback = {}

    def original_media_path(self, player):
        return player.source().toLocalFile()


class PersistenceRegressionTests(unittest.TestCase):
    def test_saved_boolean_rejects_truthy_text_and_accepts_legacy_zero_one(self):
        self.assertFalse(_state_bool({"enabled": "false"}, "enabled", True))
        self.assertFalse(_state_bool({"enabled": 0}, "enabled", True))
        self.assertTrue(_state_bool({"enabled": 1}, "enabled", False))
        self.assertTrue(_state_bool({"enabled": True}, "enabled", False))
        self.assertFalse(_state_bool({"enabled": None}, "enabled", True))
        self.assertTrue(_state_bool({}, "enabled", True))

    def test_control_api_state_persists_options_but_never_credentials(self):
        class Harness(PersistenceMixin):
            def __init__(self):
                self.is_quitting = True
                self._skip_state_save = False
                self.tracks = []
                self.track_sources = []
                self.events = []
                self.recording_config = {
                    "folder": "C:/RadioStudio Recordings", "format": "wav",
                    "bitrate": 256, "auto_on_broadcast": True,
                }
                self.recording_schedules = [{
                    "id": "weekly", "type": "weekly", "date": "",
                    "weekdays": [0, 4], "start": "23:00", "end": "01:00",
                    "enabled": True,
                }]
                self.carts = []
                self.autodj_config = {}
                self.autodj_programs = []
                self.autodj_last_signature = None
                self.main_device_key = self.preview_device_key = ""
                self.input_device_keys = {}
                self.mic_music_level_percent = 100
                self.dynamic_mic_duck = False
                self.speech_music_level_percent = 50
                self.speech_threshold_db = -36
                self.local_master_enabled = True
                self.monitor_volume = self.master_live_volume = 100
                self.minimize_to_tray = self.resume_on_startup = True
                self.auto_start_broadcast = False
                self.process_priority = "normal"
                self.backup_directory = ""
                self.backup_periodic_enabled = False
                self.backup_interval_seconds = 86400
                self.backup_retention = 10
                self.backup_last_run = 0
                self.control_api_config = {
                    "enabled": True, "port": 8091, "auth_mode": "basic",
                    "allowed_origins": ["http://localhost:3000"],
                }
                self.language_preference = "system"
                self.broadcast_was_running = False
                self.discord_enabled = self.discord_share_track = False
                self.discord_use_track_cover = False
                self.discord_cover_url = self.discord_public_url = ""
                self.discord_activity_name = self.discord_large_text = "RadioStudio"
                self.discord_activity_type = self.discord_status_display_type = 0
                self.discord_details_template = self.discord_state_template = ""
                self.discord_button1_label = self.discord_button2_label = ""
                self.discord_button2_url = ""
                self.fade_in_ms = self.fade_out_ms = self.mix_ms = 0
                self.auto_transitions_enabled = self.trim_silence = False
                self.silence_threshold_db = -42
                self.silence_hold_ms = 1200
                self.player_controls_position = "bottom"
                self.waveform_style = "bars"
                self.waveform_visualizers_enabled = True
                self.last_playback = {}
                self.station_metadata = {}
                self.metadata_update_mode = "current"
                self.metadata_update_interval_seconds = 30
                self.explorer_folder = ""
                self.broadcast_panel = SimpleNamespace(
                    profiles=[], local_config=lambda: {})

            def capture_playback(self):
                raise AssertionError("quitting state must not capture playback")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "state.json"
            with patch.object(persistence, "APP_DIR", root), \
                    patch.object(persistence, "STATE_FILE", state_file):
                PersistenceMixin.save_state(Harness())
            state_text = state_file.read_text(encoding="utf-8")
            saved = json.loads(state_text)

        self.assertEqual(saved["control_api"], {
            "enabled": True, "port": 8091, "auth_mode": "basic",
            "allowed_origins": ["http://localhost:3000"],
        })
        self.assertNotIn("username", saved["control_api"])
        self.assertNotIn("password", saved["control_api"])
        self.assertNotIn("token", saved["control_api"])
        self.assertNotIn("super-secret-api-credential", state_text)
        self.assertEqual(saved["recording"], {
            "folder": "C:/RadioStudio Recordings", "format": "wav",
            "bitrate": 256, "auto_on_broadcast": True,
        })
        self.assertEqual(saved["recording_schedules"][0]["weekdays"], [0, 4])
        self.assertEqual(saved["recording_schedules"][0]["end"], "01:00")

    def test_invalid_state_is_preserved_before_startup_continues(self):
        class Harness(PersistenceMixin):
            pass

        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            invalid_state = b'{"tracks": '
            state_file.write_bytes(invalid_state)
            with patch.object(persistence, "STATE_FILE", state_file), \
                    patch.object(persistence, "show_warning") as warning, \
                    self.assertLogs("radiostudio", level="ERROR") as captured:
                PersistenceMixin.load_state(Harness())

            recovery_files = list(Path(directory).glob("state.recovery-*.json"))
            self.assertEqual(len(recovery_files), 1)
            self.assertEqual(recovery_files[0].read_bytes(), invalid_state)
            self.assertIn(str(recovery_files[0]), warning.call_args.args[2])
            self.assertIn(str(state_file), captured.output[0])

    def test_resume_snapshot_uses_incoming_deck_when_crossfade_is_paused(self):
        player = _PlaybackCaptureHarness(QMediaPlayer.PausedState)

        PersistenceMixin.capture_playback(player)

        self.assertEqual(player.last_playback, {
            "path": "incoming.mp3", "index": 1,
            "position_ms": 42000, "was_playing": False,
        })

    def test_resume_restores_the_exact_occurrence_of_a_duplicate_track(self):
        class Harness(PersistenceMixin):
            def __init__(self, path):
                self.resume_on_startup = True
                self.last_playback = {"path": str(path), "index": 2,
                                      "position_ms": 5000, "was_playing": True}
                self.tracks = [str(path), "between.mp3", str(path), "after.mp3"]
                self.track_sources = ["manual"] * len(self.tracks)
                self.started = None

            def play_index(self, index, position):
                self.started = (index, position)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.mp3"
            path.write_bytes(b"audio")
            harness = Harness(path)

            PersistenceMixin.restore_playback(harness)

        self.assertEqual(harness.started, (2, 5000))


if __name__ == "__main__":
    unittest.main()
