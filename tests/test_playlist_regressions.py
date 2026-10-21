import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget

from radiostudio.ui.playlist import PlaylistMixin, _FolderScanWorker, m3u_entries
from radiostudio.ui.playlist_table import PlaylistTable


class _SelectionModel:
    def __init__(self):
        self.selected = []

    def select(self, index, flags):
        self.selected.append((index, flags))


class _Model:
    @staticmethod
    def index(row, column):
        return row, column


class _Playlist:
    def __init__(self):
        self.selection = _SelectionModel()
        self.table_model = _Model()
        self.cleared = False

    def clearSelection(self):
        self.cleared = True

    def selectionModel(self):
        return self.selection

    def model(self):
        return self.table_model


class _PlaylistHarness(PlaylistMixin):
    def __init__(self):
        self.tracks = ["a.mp3", "b.mp3", "c.mp3", "d.mp3"]
        self.track_sources = ["manual", "autodj", "schedule", "manual"]
        self.current_index = 2
        self.playlist = _Playlist()
        self.saved = False
        self.refreshed = False
        self.cancelled = False

    def cancel_pending_transition(self):
        self.cancelled = True

    def refresh_playlist(self, **_kwargs):
        self.refreshed = True

    def save_state(self):
        self.saved = True


class _PlaylistRefreshHarness(PlaylistMixin):
    def __init__(self):
        self.tracks = ["a.mp3", "b.mp3"]
        self.current_index = 0
        self.playlist = QTableWidget(0, 3)


class PlaylistRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_playlist_refresh_appends_only_new_rows(self):
        harness = _PlaylistRefreshHarness()

        def metadata(path):
            return SimpleNamespace(title=Path(path).stem, artist="Artist", album="Album")

        with patch("radiostudio.ui.playlist.read_track_metadata", side_effect=metadata) as read:
            harness.refresh_playlist()
            harness.tracks.append("c.mp3")
            harness.refresh_playlist()

        self.assertEqual(read.call_count, 3)
        self.assertEqual(harness.playlist.rowCount(), 3)
        self.assertEqual(harness.playlist.item(0, 0).text(), "a")
        self.assertEqual(harness.playlist.item(2, 0).text(), "c")
        self.assertEqual(harness.playlist.item(2, 0).data(Qt.UserRole + 1), 2)

    def test_unchanged_playlist_refresh_skips_metadata_and_table_rebuild(self):
        harness = _PlaylistRefreshHarness()

        def metadata(path):
            return SimpleNamespace(title=Path(path).stem, artist="Artist", album="Album")

        with patch("radiostudio.ui.playlist.read_track_metadata", side_effect=metadata) as read:
            harness.refresh_playlist()
            original_item = harness.playlist.item(0, 0)
            harness.current_index = 1
            harness.refresh_playlist()

        self.assertEqual(read.call_count, 2)
        self.assertIs(harness.playlist.item(0, 0), original_item)
        self.assertTrue(harness.playlist.selectionModel().isRowSelected(
            1, harness.playlist.rootIndex()))

    def test_successful_drag_repairs_rows_removed_by_qt_move_cleanup(self):
        harness = _PlaylistRefreshHarness()
        harness.track_sources = ["manual", "autodj"]
        harness.cancel_pending_transition = lambda: None
        harness.save_state = lambda: None
        harness.playlist = PlaylistTable(
            lambda *_args: None, lambda _order: True, lambda *_args: None,
            lambda *_args: None,
            restore_order=lambda: harness.refresh_playlist(force=True),
        )

        def metadata(path):
            return SimpleNamespace(title=Path(path).stem, artist="Artist", album="Album")

        with patch("radiostudio.ui.playlist.read_track_metadata", side_effect=metadata):
            harness.refresh_playlist()
            # A completed drag commits queue order before Qt's base startDrag
            # removes its original source rows using their now-stale indexes.
            accepted = harness.commit_playlist_order([
                ("b.mp3", 1), ("a.mp3", 0),
            ])
            self.assertTrue(accepted)
            self.assertEqual(harness.current_index, 1)
            harness.playlist.removeRow(1)
            harness.playlist.restore_after_drag()

        self.assertEqual(harness.playlist.rowCount(), 2)
        self.assertEqual(
            [harness.playlist.item(row, 0).data(Qt.UserRole)
             for row in range(harness.playlist.rowCount())],
            harness.tracks,
        )
        self.assertTrue(harness.playlist.selectionModel().isRowSelected(
            harness.current_index, harness.playlist.rootIndex()))

    def test_rejected_drag_rebuilds_damaged_table_even_when_queue_is_unchanged(self):
        harness = _PlaylistRefreshHarness()
        harness.track_sources = ["manual", "autodj"]
        harness.cancel_pending_transition = lambda: None
        harness.save_state = lambda: None

        def metadata(path):
            return SimpleNamespace(title=Path(path).stem, artist="Artist", album="Album")

        with patch("radiostudio.ui.playlist.read_track_metadata", side_effect=metadata):
            harness.refresh_playlist()
            harness.playlist.takeItem(0, 0)
            accepted = harness.commit_playlist_order([
                ("a.mp3", 0),
            ])

        self.assertFalse(accepted)
        self.assertEqual(harness.playlist.item(0, 0).data(Qt.UserRole), "a.mp3")
        self.assertEqual(harness.playlist.item(1, 0).data(Qt.UserRole), "b.mp3")

    def test_reorder_preserves_current_track_and_its_source(self):
        harness = _PlaylistHarness()

        PlaylistMixin.reorder_tracks(harness, [2], 1)

        self.assertEqual(harness.tracks, ["a.mp3", "c.mp3", "b.mp3", "d.mp3"])
        self.assertEqual(harness.track_sources,
                         ["manual", "schedule", "autodj", "manual"])
        self.assertEqual(harness.current_index, 1)
        self.assertTrue(harness.refreshed)
        self.assertTrue(harness.saved)
        self.assertTrue(harness.playlist.cleared)

    def test_dropping_without_changing_order_does_not_interrupt_transition(self):
        harness = _PlaylistHarness()

        committed = PlaylistMixin.commit_playlist_order(harness, [
            (path, row) for row, path in enumerate(harness.tracks)
        ])

        self.assertTrue(committed)
        self.assertFalse(harness.cancelled)
        self.assertFalse(harness.saved)

    def test_drag_commit_preserves_current_track_and_source_by_original_row(self):
        harness = _PlaylistHarness()

        committed = PlaylistMixin.commit_playlist_order(harness, [
            ("c.mp3", 2), ("a.mp3", 0), ("b.mp3", 1), ("d.mp3", 3),
        ])

        self.assertTrue(committed)
        self.assertEqual(harness.tracks, ["c.mp3", "a.mp3", "b.mp3", "d.mp3"])
        self.assertEqual(harness.track_sources,
                         ["schedule", "manual", "autodj", "manual"])
        self.assertEqual(harness.current_index, 0)
        self.assertTrue(harness.saved)

    def test_dragging_another_track_across_current_keeps_current_in_queue(self):
        harness = _PlaylistHarness()

        committed = PlaylistMixin.commit_playlist_order(harness, [
            ("a.mp3", 0), ("d.mp3", 3), ("b.mp3", 1), ("c.mp3", 2),
        ])

        self.assertTrue(committed)
        self.assertEqual(harness.tracks, ["a.mp3", "d.mp3", "b.mp3", "c.mp3"])
        self.assertEqual(harness.track_sources,
                         ["manual", "manual", "autodj", "schedule"])
        self.assertEqual(harness.current_index, 3)

    def test_drag_commit_recovers_current_row_from_active_media_path(self):
        harness = _PlaylistHarness()
        harness.current_index = 1  # Simulate an index stale during a transition.
        harness.player = object()
        harness.original_media_path = lambda _player: "c.mp3"

        committed = PlaylistMixin.commit_playlist_order(harness, [
            ("a.mp3", 0), ("d.mp3", 3), ("b.mp3", 1), ("c.mp3", 2),
        ])

        self.assertTrue(committed)
        self.assertEqual(harness.current_index, 3)
        self.assertEqual(harness.tracks[harness.current_index], "c.mp3")

    def test_drag_commit_rejects_missing_or_duplicate_rows_without_losing_current(self):
        for order in (
                [("a.mp3", 0), ("b.mp3", 1), ("d.mp3", 3)],
                [("a.mp3", 0), ("b.mp3", 1), ("b.mp3", 1), ("d.mp3", 3)]):
            with self.subTest(order=order):
                harness = _PlaylistHarness()

                committed = PlaylistMixin.commit_playlist_order(harness, order)

                self.assertFalse(committed)
                self.assertEqual(harness.tracks, ["a.mp3", "b.mp3", "c.mp3", "d.mp3"])
                self.assertEqual(harness.current_index, 2)
                self.assertTrue(harness.refreshed)

    def test_m3u_entries_strip_whitespace_and_ignore_indented_comments(self):
        self.assertEqual(
            m3u_entries([" #EXTM3U ", " song one.mp3  ", "", "\t#EXTINF:10,x", "sub/song two.mp3\t"]),
            ["song one.mp3", "sub/song two.mp3"],
        )

    def test_folder_scan_worker_collects_audio_in_deterministic_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "b.mp3").write_bytes(b"audio")
            (root / "notes.txt").write_text("ignore", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            (nested / "a.wav").write_bytes(b"audio")
            results = []
            worker = _FolderScanWorker(folder)
            worker.signals.finished.connect(lambda path, result: results.append((path, result)))

            worker.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], folder)
        paths, metadata_by_path, errors = results[0][1]
        self.assertEqual(paths, [str(root / "b.mp3"), str(nested / "a.wav")])
        self.assertEqual(set(metadata_by_path), set(paths))
        self.assertEqual(metadata_by_path[str(root / "b.mp3")].title, "b")
        self.assertEqual(errors, 0)

    def test_folder_scan_worker_emits_completion_after_unexpected_walk_failure(self):
        results = []
        worker = _FolderScanWorker("unavailable-folder")
        worker.signals.finished.connect(lambda path, result: results.append((path, result)))

        with self.assertLogs("radiostudio", level="ERROR"):
            with patch("radiostudio.ui.playlist.os.walk",
                       side_effect=RuntimeError("simulated filesystem failure")):
                worker.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "unavailable-folder")
        self.assertEqual(results[0][1], ([], {}, 1))

    def test_drag_validation_does_not_access_filesystem_until_drop(self):
        class Url:
            def isLocalFile(self):
                return True

            @staticmethod
            def toLocalFile():
                return r"Z:\music\not-yet-checked.mp3"

        class MimeData:
            @staticmethod
            def urls():
                return [Url()]

        class Event:
            @staticmethod
            def mimeData():
                return MimeData()

        with patch("radiostudio.ui.playlist_table.Path.is_file") as is_file:
            self.assertTrue(PlaylistTable.has_audio_urls(Event()))
            is_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
