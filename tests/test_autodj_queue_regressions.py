import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radiostudio.services.autodj import choose_track, reserve_track
from radiostudio.ui.autodj_controller import AutoDJControllerMixin


class _AutoDJQueueHarness(AutoDJControllerMixin):
    def __init__(self):
        self.current_index = 0
        self.tracks = ["current.mp3", "generated.mp3", "manual.mp3"]
        self.track_sources = ["manual", "autodj", "manual"]
        self.mix_target_index = 1
        self.pending_transition_kind = None
        self.cancelled = False

    def cancel_pending_transition(self):
        self.cancelled = True
        self.mix_target_index = -1
        self.pending_transition_kind = None

    def refresh_playlist(self):
        pass

    def save_state(self):
        pass


class AutoDJQueueRegressionTests(unittest.TestCase):
    def test_autodj_does_not_requeue_a_track_with_different_path_slashes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queued.mp3"
            path.write_bytes(b"audio")
            track = {"path": str(path), "artist": "Artist"}
            queue_path = str(path).replace("\\", "/")

            chosen, note = choose_track(
                [track], ({}, {}), excluded_paths=[queue_path], now=1000)

        self.assertIsNone(chosen)
        self.assertIn("inéditas", note)

    def test_bypassing_fill_throttle_does_not_enable_disabled_autodj(self):
        class Library:
            def libraries(self):
                raise AssertionError("AutoDJ desligado não deve consultar bibliotecas")

        class Panel:
            def set_status(self, _status):
                pass

        player = AutoDJControllerMixin()
        player.music_library = Library()
        player.autodj_panel = Panel()
        player.autodj_config = {"enabled": False}
        player.autodj_programs = []
        player.autodj_last_signature = None
        player.autodj_last_fill = 100.0
        player.current_index = -1
        player.tracks = []
        player.track_sources = []

        with patch("radiostudio.ui.autodj_controller.time.monotonic", return_value=100.1):
            AutoDJControllerMixin.fill_autodj_queue(player, bypass_throttle=True)

        self.assertEqual(player.tracks, [])

    def test_reserved_queue_artist_obeys_artist_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            a1 = Path(folder) / "a1.mp3"
            a2 = Path(folder) / "a2.mp3"
            b1 = Path(folder) / "b1.mp3"
            for path in (a1, a2, b1):
                path.write_bytes(b"audio")
            first = {"path": str(a1), "artist": "Artist A", "title": "One"}
            same_artist = {"path": str(a2), "artist": "Artist A", "title": "Two"}
            other_artist = {"path": str(b1), "artist": "Artist B", "title": "Three"}
            history = ({}, {})

            reserve_track(history, first, now=1000)
            chosen, note = choose_track(
                [same_artist, other_artist], history,
                artist_cooldown_minutes=60, now=1000,
            )

            self.assertEqual(chosen, other_artist)
            self.assertEqual(note, "")

    def test_never_played_track_is_not_recent_when_clock_is_small(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "new.mp3"
            path.write_bytes(b"audio")
            track = {"path": str(path), "artist": "New Artist"}

            chosen, note = choose_track(
                [track], ({}, {}), artist_cooldown_minutes=60,
                track_cooldown_minutes=60, now=1000,
            )

            self.assertEqual(chosen, track)
            self.assertEqual(note, "")

    def test_program_change_cancels_mix_before_removing_its_target(self):
        player = _AutoDJQueueHarness()

        AutoDJControllerMixin._clear_generated_autodj_tracks(player)

        self.assertTrue(player.cancelled)
        self.assertEqual(player.mix_target_index, -1)
        self.assertEqual(player.tracks, ["current.mp3", "manual.mp3"])
        self.assertEqual(player.track_sources, ["manual", "manual"])

    def test_program_change_cancels_advance_fade_when_its_target_is_removed(self):
        player = _AutoDJQueueHarness()
        player.mix_target_index = -1
        player.pending_transition_kind = "advance"

        AutoDJControllerMixin._clear_generated_autodj_tracks(player)

        self.assertTrue(player.cancelled)
        self.assertIsNone(player.pending_transition_kind)

    def test_program_change_cancels_mix_when_removed_rows_shift_its_index(self):
        player = _AutoDJQueueHarness()
        player.tracks.append("mix-target.mp3")
        player.track_sources.append("manual")
        player.mix_target_index = 2

        AutoDJControllerMixin._clear_generated_autodj_tracks(player)

        self.assertTrue(player.cancelled)
        self.assertEqual(player.tracks, ["current.mp3", "manual.mp3", "mix-target.mp3"])


if __name__ == "__main__":
    unittest.main()
