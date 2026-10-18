import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from radiostudio.services.autodj import choose_track, program_signature, reserve_track
from radiostudio.ui.autodj_controller import AutoDJControllerMixin
from radiostudio.ui.persistence import _tuple_tree


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
    def test_saved_autodj_signature_round_trips_with_legacy_library_id_types(self):
        signature = (program_signature(None), (1, "2"))
        restored = _tuple_tree(json.loads(json.dumps(signature)))

        self.assertEqual(restored, signature)

    def test_first_startup_fill_preserves_saved_generated_tracks(self):
        class Library:
            @staticmethod
            def libraries():
                return [{"id": 7, "enabled": True}]

            @staticmethod
            def tracks_for_libraries(_library_ids):
                return [{"path": "candidate.mp3", "artist": "Artist"}]

            @staticmethod
            def latest_track_plays(**_kwargs):
                return {}, {}

        class Panel:
            @staticmethod
            def set_status(_status):
                pass

        player = _AutoDJQueueHarness()
        player.tracks = ["current.mp3", "saved-next.mp3"]
        player.track_sources = ["manual", "autodj"]
        player.autodj_config = {"enabled": True, "queue_ahead": 1}
        player.autodj_programs = []
        player.autodj_last_signature = (("previous-block",), (7,))
        player.autodj_last_fill = 0.0
        player.autodj_startup_pending = True
        player.music_library = Library()
        player.autodj_panel = Panel()
        player.refresh_playlist = lambda: None
        player.save_state = lambda: None

        with patch("radiostudio.ui.autodj_controller.time.monotonic", return_value=100):
            AutoDJControllerMixin.fill_autodj_queue(player, force=True)

        self.assertEqual(player.tracks, ["current.mp3", "saved-next.mp3"])
        self.assertEqual(player.track_sources, ["manual", "autodj"])

    def test_unavailable_program_library_reports_status(self):
        class Library:
            @staticmethod
            def libraries():
                return [{"id": 7, "enabled": True}]

        class Panel:
            status = ""

            def set_status(self, status):
                self.status = status

        player = AutoDJControllerMixin()
        player.music_library = Library()
        player.autodj_panel = Panel()
        player.autodj_programs = []
        player.autodj_config = {"enabled": True}
        player.autodj_last_signature = None
        player.autodj_last_fill = 0.0
        player.autodj_startup_pending = False
        player.current_index = -1
        player.tracks = []
        player.track_sources = []
        player.refresh_playlist = lambda: None
        player.save_state = lambda: None
        unavailable_program = {"name": "Bloco", "library_ids": [99], "filters": {}}

        with patch("radiostudio.ui.autodj_controller.active_program",
                   return_value=unavailable_program), patch(
                       "radiostudio.ui.autodj_controller.time.monotonic", return_value=10):
            AutoDJControllerMixin.fill_autodj_queue(player, force=True)

        self.assertEqual(player.autodj_panel.status,
                         "O bloco não tem bibliotecas habilitadas; ajuste sua seleção.")

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

    def test_full_queue_skips_library_candidates_and_history_queries(self):
        class Library:
            def libraries(self):
                return [{"id": "music", "enabled": True}]

            def tracks_for_libraries(self, _library_ids):
                raise AssertionError("fila completa não deve carregar as faixas indexadas")

            def latest_track_plays(self):
                raise AssertionError("fila completa não deve consultar o histórico")

        class Panel:
            status = ""

            def set_status(self, status):
                self.status = status

        player = AutoDJControllerMixin()
        player.music_library = Library()
        player.autodj_panel = Panel()
        player.autodj_config = {"enabled": True, "queue_ahead": 1}
        player.autodj_programs = []
        player.autodj_last_signature = None
        player.autodj_last_fill = 0.0
        player.autodj_startup_pending = False
        player.current_index = 0
        player.tracks = ["current.mp3", "next.mp3"]
        player.track_sources = ["manual", "manual"]
        player.refresh_playlist = lambda: None
        player.save_state = lambda: None

        with patch("radiostudio.ui.autodj_controller.time.monotonic", return_value=10):
            AutoDJControllerMixin.fill_autodj_queue(player)

        self.assertEqual(player.autodj_panel.status,
                         "Aleatório — todas as bibliotecas: fila completa")

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
