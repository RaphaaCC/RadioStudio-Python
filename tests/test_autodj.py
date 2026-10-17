import tempfile
import unittest
import sqlite3
import errno
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from radiostudio.services.autodj import active_program, choose_track, filter_tracks
from radiostudio.services.music_library import MusicLibrary
from radiostudio.services.track_metadata import TrackMetadata


class AutoDJTests(unittest.TestCase):
    def test_weekly_and_overnight_schedule(self):
        block = {"id": "late", "enabled": True, "name": "Madrugada", "days": [4],
                 "start": "22:00", "end": "02:00", "priority": 3}
        self.assertEqual(active_program([block], datetime(2026, 10, 16, 23, 20)), block)
        self.assertEqual(active_program([block], datetime(2026, 10, 17, 1, 0)), block)
        self.assertIsNone(active_program([block], datetime(2026, 10, 17, 3, 0)))

    def test_invalid_schedule_times_do_not_activate_a_default_midnight_block(self):
        malformed = {"enabled": True, "days": list(range(7)),
                     "start": "not-a-time", "end": "01:00"}
        self.assertIsNone(active_program([malformed], datetime(2026, 10, 16, 0, 30)))

    def test_filtering_by_tags_and_keywords(self):
        tracks = [
            {"path": "a", "title": "Noite Azul", "artist": "Lia", "album": "Ao Vivo", "genre": "Pop", "year": "2020"},
            {"path": "b", "title": "Manhã", "artist": "Beto", "album": "Estúdio", "genre": "Rock", "year": "2010"},
        ]
        result = filter_tracks(tracks, {"genres": "Pop", "artists": "Lia",
                                       "include_keywords": "noite, ao vivo", "year_from": 2015,
                                       "keyword_fields": ["title", "album"]})
        self.assertEqual(["a"], [track["path"] for track in result])

    def test_year_filter_excludes_tracks_with_unknown_year(self):
        tracks = [
            {"path": "known", "title": "Known", "year": "2008"},
            {"path": "unknown", "title": "Unknown", "year": ""},
            {"path": "invalid", "title": "Invalid", "year": "not-a-year"},
            {"path": "newer", "title": "Newer", "year": "2015"},
        ]

        result = filter_tracks(tracks, {"year_to": 2010})

        self.assertEqual(["known"], [track["path"] for track in result])

    def test_repeat_cooldowns_fall_back_without_stalling(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first.mp3"; first.write_bytes(b"a")
            second = Path(folder) / "second.mp3"; second.write_bytes(b"b")
            tracks = [
                {"path": str(first), "artist": "A", "title": "One"},
                {"path": str(second), "artist": "A", "title": "Two"},
            ]
            history = ({str(first).casefold(): 999, str(second).casefold(): 999}, {"a": 999})
            chosen, note = choose_track(tracks, history, artist_cooldown_minutes=10,
                                        track_cooldown_minutes=10, now=1000)
            self.assertIn(chosen, tracks)
            self.assertTrue(note)

    def test_malformed_candidate_and_cooldown_values_do_not_break_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "track.mp3"
            path.write_bytes(b"audio")
            track = {"path": str(path), "artist": "Artist", "title": "Title"}
            chosen, _note = choose_track(
                [None, track], ({}, {}), artist_cooldown_minutes=float("inf"),
                track_cooldown_minutes="invalid", now="invalid")
            self.assertEqual(chosen, track)

    def test_library_index_and_metadata_history_are_durable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "music"; root.mkdir()
            (root / "Song One.mp3").write_bytes(b"not encoded, tag reader falls back to filename")
            db = Path(folder) / "library.sqlite3"
            store = MusicLibrary(db)
            library_id = store.add_library("Pop", str(root))
            indexed, unchanged, errors = store.scan_library(library_id)
            self.assertEqual((indexed, unchanged, errors), (1, 0, 0))
            self.assertEqual(store.libraries()[0]["track_count"], 1)
            self.assertEqual(store.scan_library(library_id), (0, 1, 0))
            metadata = TrackMetadata(path=str(root / "Song One.mp3"), title="Song One",
                                     artist="Artist", album="Album", genre="Pop")
            store.record_play(metadata, played_at=1234)
            history = store.recent_history()
            self.assertEqual((history[0]["played_at"], history[0]["artist"], history[0]["album"]),
                             (1234.0, "Artist", "Album"))
            self.assertEqual(store.latest_track_plays()[0][str(root / "Song One.mp3").casefold()], 1234)
            store.remove_library(library_id)
            self.assertEqual(store.recent_history()[0]["title"], "Song One")

    def test_existing_library_database_is_migrated_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "library.sqlite3"
            db = sqlite3.connect(database)
            try:
                db.executescript("""
                    CREATE TABLE libraries (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL UNIQUE
                    );
                    CREATE TABLE tracks (
                        path TEXT NOT NULL, library_id TEXT NOT NULL, mtime_ns INTEGER NOT NULL,
                        size INTEGER NOT NULL, title TEXT NOT NULL,
                        artist TEXT NOT NULL DEFAULT '', album TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY(path, library_id)
                    );
                    CREATE TABLE play_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, played_at REAL NOT NULL,
                        path TEXT NOT NULL, title TEXT NOT NULL,
                        artist TEXT NOT NULL DEFAULT '', album TEXT NOT NULL DEFAULT ''
                    );
                """)
                db.execute("INSERT INTO libraries(id, name, root) VALUES('legacy', 'Legacy', ?)",
                           (folder,))
                db.execute("INSERT INTO play_history(played_at, path, title, artist, album) "
                           "VALUES(1, 'legacy.mp3', 'Legacy title', 'Artist', 'Album')")
                db.commit()
            finally:
                db.close()

            store = MusicLibrary(database)
            self.assertEqual(store.libraries()[0]["track_count"], 0)
            history = store.recent_history()
            self.assertEqual(history[0]["title"], "Legacy title")
            self.assertEqual(history[0]["genre"], "")

    def test_scan_keeps_indexed_tracks_when_a_subfolder_cannot_be_examined(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "music"
            protected = root / "restricted"
            protected.mkdir(parents=True)
            track = protected / "Song.mp3"
            track.write_bytes(b"audio")
            store = MusicLibrary(Path(folder) / "library.sqlite3")
            library_id = store.add_library("Music", str(root))
            self.assertEqual(store.scan_library(library_id), (1, 0, 0))

            original_entries = MusicLibrary._entries

            def failing_entries(directory):
                # The stored library root is resolved before scanning, so compare
                # the directory identity by its stable leaf/parent names rather
                # than by the original temporary Path spelling on Windows.
                if directory.name == "restricted" and directory.parent.name == "music":
                    raise PermissionError(errno.EACCES, "access denied", str(directory))
                return original_entries(directory)

            with patch.object(MusicLibrary, "_entries", side_effect=failing_entries):
                indexed, skipped, errors = store.scan_library(library_id)

            self.assertEqual((indexed, skipped), (0, 0))
            self.assertGreater(errors, 0)
            self.assertEqual(len(store.tracks_for_libraries([library_id])), 1)


if __name__ == "__main__":
    unittest.main()
