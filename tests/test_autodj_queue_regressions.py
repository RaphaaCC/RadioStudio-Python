import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.services.autodj import choose_track, program_signature, reserve_track
from radiostudio.ui.autodj_controller import AutoDJControllerMixin, _AutoDJFillWorker
from radiostudio.ui.playback import PlaybackMixin
from radiostudio.ui.persistence import _tuple_tree


class _ImmediateThreadPool:
    def start(self, worker, priority=0):
        worker.run()


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
    def setUp(self):
        self.pool = _ImmediateThreadPool()
        self.pool_patch = patch(
            "radiostudio.ui.autodj_controller.QThreadPool",
            SimpleNamespace(globalInstance=lambda: self.pool),
        )
        self.pool_patch.start()
        self.addCleanup(self.pool_patch.stop)

    def test_track_start_history_reuses_metadata_already_loaded_for_the_display(self):
        metadata = SimpleNamespace(path="current.mp3", title="Current")
        library = Mock()
        player = AutoDJControllerMixin()
        player.music_library = library
        player.autodj_config = {"remove_played_from_queue": False}
        player._display_sync_path = "current.mp3"
        player._display_sync_metadata = metadata

        with patch("radiostudio.ui.autodj_controller.read_track_metadata") as read_metadata:
            AutoDJControllerMixin.record_track_started(player, "current.mp3")

        read_metadata.assert_not_called()
        library.record_play.assert_called_once_with(metadata)

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
                         "As pastas selecionadas para este bloco estão desabilitadas ou indisponíveis; ajuste a seleção.")

    def test_legacy_integer_library_id_matches_sqlite_text_id(self):
        class Library:
            @staticmethod
            def libraries():
                return [{"id": "7", "enabled": True}]

        block = {"id": "legacy", "library_ids": [7]}
        player = AutoDJControllerMixin()
        player.music_library = Library()
        player.autodj_programs = [block]

        with patch("radiostudio.ui.autodj_controller.active_program", return_value=block):
            selected_program, selected_ids, has_requested = player._autodj_selection()

        self.assertIs(selected_program, block)
        self.assertEqual(selected_ids, ["7"])
        self.assertTrue(has_requested)

    def test_schedule_block_can_select_collection_without_all_library_folders(self):
        class Library:
            @staticmethod
            def libraries():
                return [
                    {"id": "music", "enabled": True},
                    {"id": "talk", "enabled": True},
                ]

        block = {"id": "favorites", "library_ids": [],
                 "sources": [{"type": "collection", "id": "favorites", "weight": 1}]}
        player = AutoDJControllerMixin()
        player.music_library = Library()
        player.autodj_programs = [block]

        with patch("radiostudio.ui.autodj_controller.active_program", return_value=block):
            selected_program, selected_ids, has_requested = player._autodj_selection()

        self.assertIs(selected_program, block)
        self.assertEqual(selected_ids, [])
        self.assertTrue(has_requested)

    def test_schedule_block_without_explicit_sources_defaults_to_enabled_folders(self):
        class Library:
            @staticmethod
            def libraries():
                return [
                    {"id": "music", "enabled": True},
                    {"id": "talk", "enabled": True},
                    {"id": "archived", "enabled": False},
                ]

        block = {"id": "all-music", "library_ids": [], "sources": []}
        player = AutoDJControllerMixin()
        player.music_library = Library()
        player.autodj_programs = [block]

        with patch("radiostudio.ui.autodj_controller.active_program", return_value=block):
            _program, selected_ids, has_requested = player._autodj_selection()

        self.assertEqual(selected_ids, ["music", "talk"])
        self.assertFalse(has_requested)

    def test_no_active_schedule_block_uses_every_enabled_library_folder(self):
        class Library:
            @staticmethod
            def libraries():
                return [
                    {"id": "music", "enabled": True},
                    {"id": "talk", "enabled": True},
                    {"id": "disabled", "enabled": False},
                ]

        player = AutoDJControllerMixin()
        player.music_library = Library()
        player.autodj_programs = []

        with patch("radiostudio.ui.autodj_controller.active_program", return_value=None):
            program, selected_ids, has_requested = player._autodj_selection()

        self.assertIsNone(program)
        self.assertEqual(selected_ids, ["music", "talk"])
        self.assertFalse(has_requested)

    def test_schedule_change_bypasses_fill_throttle_and_replaces_old_block_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate_path = Path(folder) / "new-block.mp3"
            candidate_path.write_bytes(b"audio")
            old_block = {"id": "old", "library_ids": ["music"]}
            new_block = {"id": "new", "library_ids": ["music"]}

            class Library:
                @staticmethod
                def libraries():
                    return [{"id": "music", "enabled": True}]

                @staticmethod
                def tracks_for_libraries(ids):
                    self.assertEqual(ids, ["music"])
                    return [{"path": str(candidate_path), "artist": "New Artist"}]

                @staticmethod
                def latest_track_plays(**_kwargs):
                    return {}, {}

            class Panel:
                @staticmethod
                def set_status(_status):
                    pass

            player = _AutoDJQueueHarness()
            player.tracks = ["current.mp3", "old-generated.mp3"]
            player.track_sources = ["manual", "autodj"]
            player.autodj_config = {"enabled": True, "queue_ahead": 1}
            player.autodj_programs = [new_block]
            player.autodj_last_signature = (
                program_signature(old_block), ("music",))
            player.autodj_last_fill = 95.0
            player.autodj_startup_pending = False
            player.music_library = Library()
            player.autodj_panel = Panel()

            with patch("radiostudio.ui.autodj_controller.active_program",
                       return_value=new_block), patch(
                           "radiostudio.ui.autodj_controller.time.monotonic", return_value=100):
                AutoDJControllerMixin.fill_autodj_queue(player)

            self.assertEqual(player.tracks, ["current.mp3", str(candidate_path)])
            self.assertEqual(player.track_sources, ["manual", "autodj"])
            self.assertEqual(player.autodj_last_signature,
                             (program_signature(new_block), ("music",)))

    def test_unchanged_program_keeps_throttle_without_reading_library(self):
        block = {"id": "same", "library_ids": ["music"]}

        class Library:
            @staticmethod
            def libraries():
                raise AssertionError("bloco inalterado deve respeitar o throttle")

        class Panel:
            @staticmethod
            def set_status(_status):
                pass

        player = AutoDJControllerMixin()
        player.autodj_config = {"enabled": True}
        player.autodj_programs = [block]
        player.autodj_last_signature = (program_signature(block), ("music",))
        player.autodj_last_fill = 95.0
        player.music_library = Library()
        player.autodj_panel = Panel()

        with patch("radiostudio.ui.autodj_controller.active_program",
                   return_value=block), patch(
                       "radiostudio.ui.autodj_controller.time.monotonic", return_value=100):
            AutoDJControllerMixin.fill_autodj_queue(player)

        self.assertEqual(player.autodj_last_fill, 95.0)

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
                         "Aleatório — Biblioteca completa: fila completa")

    def test_autodj_worker_discards_stale_queue_result_and_applies_fresh_result(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder) / "candidate.mp3"
            candidate.write_bytes(b"audio")

            class CapturingPool:
                worker = None

                def start(self, worker, priority=0):
                    self.worker = worker

            pool = CapturingPool()
            self.pool = pool

            class Library:
                def __init__(self):
                    self.queried = False

                @staticmethod
                def libraries():
                    return [{"id": "music", "enabled": True}]

                def tracks_for_libraries(self, _library_ids):
                    self.queried = True
                    return [{"path": str(candidate), "artist": "Artist"}]

                @staticmethod
                def latest_track_plays(**_kwargs):
                    return {}, {}

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
            player.tracks = ["current.mp3"]
            player.track_sources = ["manual"]
            player.refresh_playlist = lambda: None
            player.save_state = lambda: None
            player.autodj_next_calls = 0
            player.next_track = lambda: setattr(
                player, "autodj_next_calls", player.autodj_next_calls + 1)

            AutoDJControllerMixin.fill_autodj_queue(player, force=True)

            self.assertIsNotNone(pool.worker)
            self.assertFalse(player.music_library.queried)
            self.assertEqual(player.tracks, ["current.mp3"])

            stale_worker = pool.worker
            player._autodj_waiting_for_queue = True
            player.tracks[0] = "changed-current.mp3"
            player.track_sources[0] = "schedule"
            stale_worker.run()

            self.assertEqual(player.tracks, ["changed-current.mp3"])
            self.assertFalse(player._autodj_waiting_for_queue)
            self.assertIsNot(pool.worker, stale_worker)
            self.assertTrue(player.music_library.queried)

            player._autodj_waiting_for_queue = True
            pool.worker.run()

            self.assertEqual(player.tracks, ["changed-current.mp3", str(candidate)])
            self.assertEqual(player.track_sources, ["schedule", "autodj"])
            self.assertEqual(player.autodj_next_calls, 1)
            self.assertFalse(player._autodj_waiting_for_queue)

    def test_manual_stop_during_autodj_fill_prevents_automatic_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder) / "candidate.mp3"
            candidate.write_bytes(b"audio")

            class Library:
                @staticmethod
                def libraries():
                    return [{"id": "music", "enabled": True}]

                @staticmethod
                def tracks_for_libraries(_library_ids):
                    return [{"path": str(candidate), "artist": "Artist"}]

                @staticmethod
                def latest_track_plays(**_kwargs):
                    return {}, {}

            class Panel:
                @staticmethod
                def set_status(_status):
                    pass

            class MainWindowHarness(AutoDJControllerMixin):
                def __init__(self):
                    self.music_library = Library()
                    self.autodj_panel = Panel()
                    self.autodj_config = {"enabled": True, "queue_ahead": 1}
                    self.autodj_programs = []
                    self.autodj_last_signature = None
                    self.autodj_last_fill = 0.0
                    self.autodj_startup_pending = False
                    self.current_index = -1
                    self.tracks = []
                    self.track_sources = []
                    self.player = SimpleNamespace(
                        playbackState=lambda: QMediaPlayer.StoppedState)
                    self.playlist = SimpleNamespace(selectRow=lambda _row: None)
                    self.next_calls = 0

                def refresh_playlist(self):
                    pass

                def save_state(self):
                    pass

                def next_track(self):
                    self.next_calls += 1

                def cancel_transition_action(self):
                    pass

                def cancel_mix(self):
                    pass

                def automatic_fade_out_ms(self):
                    return 0

                def finish_stop(self):
                    pass

            window = MainWindowHarness()
            window.gain_ramps = {}
            window.gain_paused_at = None
            window.gain_timer = SimpleNamespace(stop=lambda: None)
            window.finish_stop = lambda: None
            window.cancel_transition_action = lambda: None
            window.cancel_mix = lambda: None
            window.automatic_fade_out_ms = lambda: 0
            self.pool = SimpleNamespace(worker=None, start=lambda worker, priority=0:
                                        setattr(self.pool, "worker", worker))
            window.fill_autodj_queue(force=True)
            PlaybackMixin.stop(window)
            self.pool.worker.run()

            self.assertEqual(window._autodj_stop_generation, 1)
            self.assertEqual(window.tracks, [str(candidate)])
            self.assertEqual(window.next_calls, 0)

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

    def test_queue_fill_reserves_queued_artists_without_changing_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            reserved_path = Path(folder) / "queued.mp3"
            candidate_path = Path(folder) / "other-artist.mp3"
            reserved_path.write_bytes(b"audio")
            candidate_path.write_bytes(b"audio")

            class Library:
                def tracks_for_libraries(self, _library_ids):
                    return [
                        {"path": str(reserved_path), "artist": "Queued Artist"},
                        {"path": str(candidate_path), "artist": "Other Artist"},
                    ]

                def latest_track_plays(self, **_kwargs):
                    return {}, {}

            request = {
                "program": None,
                "program_name": "Random",
                "library_ids": ["library"],
                "config": {"artist_cooldown_minutes": 60,
                           "track_cooldown_minutes": 60},
                "reserved_paths": (str(reserved_path),),
                "excluded_paths": (),
                "needed": 1,
                "queue_target": 4,
            }
            worker = _AutoDJFillWorker(Library(), request)
            results = []
            worker.signals.finished.connect(results.append)

            worker.run()

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["paths"], [str(candidate_path)])

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
