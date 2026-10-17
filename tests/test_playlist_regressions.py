import unittest

from radiostudio.ui.playlist import PlaylistMixin, m3u_entries


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

    def cancel_pending_transition(self):
        pass

    def refresh_playlist(self):
        self.refreshed = True

    def save_state(self):
        self.saved = True


class PlaylistRegressionTests(unittest.TestCase):
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

    def test_m3u_entries_strip_whitespace_and_ignore_indented_comments(self):
        self.assertEqual(
            m3u_entries([" #EXTM3U ", " song one.mp3  ", "", "\t#EXTINF:10,x", "sub/song two.mp3\t"]),
            ["song one.mp3", "sub/song two.mp3"],
        )


if __name__ == "__main__":
    unittest.main()
