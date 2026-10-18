import unittest
import tempfile
from pathlib import Path

from radiostudio.services.audio_paths import audio_path_key, same_audio_path
from radiostudio.services.music_library import MusicLibrary


class AudioPathTests(unittest.TestCase):
    def test_windows_path_separator_and_case_variants_are_equal(self):
        self.assertTrue(same_audio_path(
            r"C:\Music\Artist\Song.mp3", "c:/music/artist/song.mp3"))

    def test_distinct_paths_remain_distinct(self):
        self.assertFalse(same_audio_path(
            r"C:\Music\Artist\Song.mp3", r"C:\Music\Other\Song.mp3"))

    def test_empty_path_only_matches_empty_path(self):
        self.assertTrue(same_audio_path("", ""))
        self.assertFalse(same_audio_path("", r"C:\Music\Song.mp3"))

    def test_path_keys_match_after_separator_and_case_normalization(self):
        self.assertEqual(
            audio_path_key(r"C:\Music\Artist\Song.mp3"),
            audio_path_key("c:/music/artist/song.mp3"))

    def test_play_history_merges_path_variants_using_the_latest_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            library = MusicLibrary(Path(folder) / "library.sqlite3")
            with library._connect() as db:
                db.executemany(
                    "INSERT INTO play_history(played_at, path, title) VALUES(?, ?, ?)",
                    [(1000, r"C:\Music\Song.mp3", "Song"),
                     (2000, "C:/Music/Song.mp3", "Song")])

            paths, _artists = library.latest_track_plays()

        self.assertEqual(paths[audio_path_key(r"C:\Music\Song.mp3")], 2000)

    def test_autodj_history_query_limits_rows_to_repeat_cooldowns(self):
        with tempfile.TemporaryDirectory() as folder:
            library = MusicLibrary(Path(folder) / "library.sqlite3")
            with library._connect() as db:
                db.executemany(
                    "INSERT INTO play_history(played_at, path, title, artist) VALUES(?, ?, ?, ?)",
                    [(1998, "recent.mp3", "Recent", "Band"),
                     (1920, "within-track-window.mp3", "Within", "BAND"),
                     (1800, "expired.mp3", "Expired", "Old Artist")])

            paths, artists = library.latest_track_plays(
                path_window_seconds=100, artist_window_seconds=50, now=2000)

        self.assertEqual(paths, {
            audio_path_key("recent.mp3"): 1998,
            audio_path_key("within-track-window.mp3"): 1920,
        })
        self.assertEqual(artists, {"band": 1998})


if __name__ == "__main__":
    unittest.main()
