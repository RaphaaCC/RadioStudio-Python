import base64
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import imageio_ffmpeg
from PySide6.QtWidgets import QApplication, QPlainTextEdit
from tinytag import TinyTag

from radiostudio.services.embedded_metadata import (
    SUPPORTED_EXTENSIONS, EmbeddedMetadataError, read_embedded_custom_tags,
    write_embedded_metadata,
)
from radiostudio.services.music_library import MusicLibrary
from radiostudio.ui.library_panel import LibraryPanel
from radiostudio.ui.metadata_editor import MetadataEditorDialog
from radiostudio.ui.autodj_controller import _AutoDJFillWorker


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL9sAAAAASUVORK5CYII=")


class MusicLibraryFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media = self.root / "music"
        self.subfolder = self.media / "album"
        self.subfolder.mkdir(parents=True)
        self.alpha = self.subfolder / "Alpha.mp3"
        self.beta = self.media / "Beta.mp3"
        self.alpha.write_bytes(b"test audio placeholder")
        self.beta.write_bytes(b"test audio placeholder")
        self.library = MusicLibrary(self.root / "music_library.sqlite3")
        self.library_id = self.library.add_library("Music", str(self.media))
        self.assertEqual(self.library.scan_library(self.library_id), (2, 0, 0))

    def tearDown(self):
        self.temporary.cleanup()

    def test_internal_overlays_batch_metadata_collections_tags_and_undo(self):
        self.library.save_metadata(
            str(self.alpha), {"title": "Radio Alpha", "artist": ["One", "Two"],
                              "comments": "First line\nSecond line"},
            custom_tags={"BPM": "120"}, target="internal")

        tracks = self.library.metadata_for_tracks([str(self.alpha), str(self.beta)])
        self.assertEqual(tracks[str(self.alpha)].title, "Radio Alpha")
        self.assertEqual(tracks[str(self.alpha)].artist, "One; Two")
        self.assertEqual(tracks[str(self.alpha)].comments, "First line\nSecond line")
        self.assertEqual(self.library.read_metadata_override(str(self.alpha))["custom_tags"],
                         {"BPM": "120"})
        self.assertEqual(self.library.catalog_tracks(search="radio")[0]["path"],
                         str(self.alpha.resolve()))

        self.library.set_track_tags(str(self.alpha), ["rotation", "featured"])
        manual_id = self.library.save_collection("Manual picks", kind="manual",
                                                 paths=[str(self.alpha)])
        smart_id = self.library.save_collection(
            "Radio titles", kind="smart",
            criteria={"include_keywords": "radio", "keyword_fields": ["title"]})
        self.assertEqual([row["path"] for row in self.library.catalog_tracks(
            collection_id=manual_id)], [str(self.alpha.resolve())])
        self.assertEqual([row["path"] for row in self.library.catalog_tracks(
            collection_id=smart_id)], [str(self.alpha.resolve())])
        smart = next(item for item in self.library.collections() if item["id"] == smart_id)
        self.assertEqual(smart["track_count"], 1)
        self.assertEqual([row["path"] for row in self.library.catalog_tracks(tag="featured")],
                         [str(self.alpha.resolve())])

        self.assertTrue(self.library.undo_metadata(str(self.alpha)))
        self.assertEqual(self.library.metadata_for_track(str(self.alpha)).title, "Alpha")
        self.assertEqual([row["title"] for row in self.library.catalog_tracks()], ["Alpha", "Beta"])

    def test_overlapping_rotation_sources_use_max_weight_and_deduplicate_paths(self):
        low = self.library.save_rotation_group("Low", 2)
        high = self.library.save_rotation_group("High", 5)
        self.library.set_track_rotation_groups(str(self.alpha), [low, high])
        self.library.set_track_rotation_groups(str(self.beta), [low])
        self.library.set_track_tags(str(self.alpha), ["featured"])
        manual = self.library.save_collection("Single", kind="manual", paths=[str(self.alpha)])

        candidates = self.library.tracks_for_sources([
            {"type": "group", "id": low}, {"type": "group", "id": high},
            {"type": "tag", "id": "featured", "weight": 3},
            {"type": "collection", "id": manual, "weight": 4},
        ])
        by_name = {Path(track["path"]).name: track for track in candidates}
        self.assertEqual(set(by_name), {"Alpha.mp3", "Beta.mp3"})
        self.assertEqual(by_name["Alpha.mp3"]["rotation_weight"], 5)
        self.assertEqual(by_name["Beta.mp3"]["rotation_weight"], 2)

    def test_autodj_merges_legacy_libraries_with_new_organization_sources(self):
        group = self.library.save_rotation_group("Selection", 3)
        self.library.set_track_rotation_groups(str(self.alpha), [group])
        request = {
            "program": {"filters": {}}, "sources": [{"type": "group", "id": group}],
            "library_ids": [self.library_id], "program_name": "Evening",
            "config": {"track_cooldown_minutes": 0, "artist_cooldown_minutes": 0},
            "reserved_paths": [], "excluded_paths": [], "needed": 5,
            "queue_target": 5,
        }
        worker = _AutoDJFillWorker(self.library, request)
        results = []
        worker.signals.finished.connect(results.append)
        worker.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["candidate_count"], 2)
        self.assertEqual(len(results[0]["paths"]), 2)

    def test_catalog_deduplicates_overlapping_indexed_folders_and_sorts_organization(self):
        overlapping_id = self.library.add_library("Album", str(self.subfolder))
        self.assertEqual(self.library.scan_library(overlapping_id), (1, 0, 0))
        self.library.set_track_tags(str(self.alpha), ["zebra"])
        self.library.set_track_tags(str(self.beta), ["alpha"])

        all_tracks = self.library.catalog_tracks()
        self.assertEqual(len(all_tracks), 2)
        self.assertEqual([Path(row["path"]).name for row in
                          self.library.catalog_tracks(sort_by="tags")], ["Beta.mp3", "Alpha.mp3"])
        album_tracks = self.library.catalog_tracks(library_id=overlapping_id)
        self.assertEqual([Path(row["path"]).name for row in album_tracks], ["Alpha.mp3"])

    def test_adding_to_collection_preserves_members_from_disabled_folders(self):
        extra_folder = self.root / "extra"
        extra_folder.mkdir()
        extra_track = extra_folder / "Gamma.mp3"
        extra_track.write_bytes(b"test audio placeholder")
        extra_library_id = self.library.add_library("Extra", str(extra_folder))
        self.library.scan_library(extra_library_id)
        collection_id = self.library.save_collection(
            "Keep", kind="manual", paths=[str(self.alpha)])
        self.library.update_library(self.library_id, enabled=False)

        main_window = SimpleNamespace(music_library=self.library)
        panel = LibraryPanel(main_window)
        try:
            panel.catalog_table.selectRow(0)
            with patch("radiostudio.ui.library_panel.QInputDialog.getItem",
                       return_value=("Keep", True)):
                panel.add_selected_to_collection()

            self.library.update_library(self.library_id, enabled=True)
            rows = self.library.catalog_tracks(collection_id=collection_id)
            self.assertEqual({Path(row["path"]).name for row in rows}, {"Alpha.mp3", "Gamma.mp3"})
            self.assertEqual(panel.catalog_table.item(0, 7).text(), "Keep")
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_legacy_database_migration_preserves_tracks_history_and_adds_library_tables(self):
        database = self.root / "legacy.sqlite3"
        with closing(sqlite3.connect(database)) as db:
            db.executescript("""
                CREATE TABLE libraries (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    root TEXT NOT NULL UNIQUE);
                CREATE TABLE tracks (path TEXT NOT NULL, library_id TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL, title TEXT NOT NULL,
                    artist TEXT NOT NULL DEFAULT '', album TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(path, library_id));
                CREATE TABLE play_history (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    played_at REAL NOT NULL, path TEXT NOT NULL, title TEXT NOT NULL,
                    artist TEXT NOT NULL DEFAULT '', album TEXT NOT NULL DEFAULT '');
                INSERT INTO libraries(id,name,root) VALUES('old','Old','legacy');
                INSERT INTO tracks(path,library_id,mtime_ns,size,title,artist,album)
                    VALUES('legacy.mp3','old',1,2,'Legacy title','Artist','Album');
                INSERT INTO play_history(played_at,path,title,artist,album)
                    VALUES(123,'legacy.mp3','Played title','Artist','Album');
            """)
            db.commit()

        migrated = MusicLibrary(database)
        self.assertEqual(migrated.tracks_for_libraries(["old"])[0]["title"], "Legacy title")
        self.assertEqual(migrated.recent_history()[0]["title"], "Played title")
        migrated.save_collection("New collection")
        self.assertEqual(migrated.collections()[0]["name"], "New collection")

    def test_file_tag_update_cover_custom_tag_and_undo_preserve_audio(self):
        path = self.root / "tagged.mp3"
        generated = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "0.3",
             "-c:a", "libmp3lame", "-metadata", "title=Original", "-metadata", "BPM=96",
             str(path)], capture_output=True, timeout=20)
        if generated.returncode:
            self.skipTest("FFmpeg não oferece o codificador MP3 necessário ao teste.")

        write_embedded_metadata(path, {}, cover_data=_PNG, cover_mime="image/png")
        original_duration = TinyTag.get(str(path), tags=True, duration=True).duration
        self.library.save_metadata(
            str(path), {"title": "Updated", "artist": "Radio", "album": "Test Album",
                        "albumartist": "Radio Artist", "genre": "Pop", "year": "2026",
                        "track": "3", "composer": "Composer", "comments": "A comment",
                        "lyrics": "A line\nAnother line"},
            custom_tags={"BPM": "120", "STATION_CODE": "RS-1"},
            target="file")
        written = TinyTag.get(str(path), tags=True, duration=True, image=True)
        self.assertEqual(written.title, "Updated")
        self.assertEqual(written.artist, "Radio")
        self.assertEqual(written.album, "Test Album")
        self.assertEqual(written.albumartist, "Radio Artist")
        self.assertEqual(written.genre, "Pop")
        self.assertEqual(str(written.year), "2026")
        self.assertEqual(str(written.track), "3")
        self.assertEqual(written.composer, "Composer")
        self.assertEqual(written.comment, "A comment")
        self.assertEqual(self.library.metadata_for_track(str(path)).lyrics, "A line\nAnother line")
        self.assertAlmostEqual(written.duration, original_duration, delta=0.1)
        self.assertIsNotNone(written.images.any)
        self.assertEqual(read_embedded_custom_tags(path).get("BPM"), "120")
        self.assertEqual(read_embedded_custom_tags(path).get("STATION_CODE"), "RS-1")
        self.assertEqual(self.library.metadata_for_track(str(path)).title, "Updated")

        self.assertTrue(self.library.undo_metadata(str(path)))
        restored = TinyTag.get(str(path), tags=True, duration=True, image=True)
        self.assertEqual(restored.title, "Original")
        self.assertEqual(restored.artist or "", "")
        self.assertEqual(self.library.metadata_for_track(str(path)).lyrics, "")
        self.assertIsNotNone(restored.images.any)
        self.assertEqual(read_embedded_custom_tags(path).get("BPM"), "96")
        self.assertIsNone(read_embedded_custom_tags(path).get("STATION_CODE"))
        self.assertAlmostEqual(restored.duration, original_duration, delta=0.1)

        self.library.save_metadata(str(path), {}, clear_cover=True, target="file")
        self.assertIsNone(TinyTag.get(str(path), tags=True, image=True).images.any)
        self.assertTrue(self.library.undo_metadata(str(path)))
        self.assertIsNotNone(TinyTag.get(str(path), tags=True, image=True).images.any)

    def test_active_file_edit_is_deferred_and_undo_cancels_the_pending_write(self):
        result = self.library.save_metadata(
            str(self.alpha), {"title": "Queued title"}, target="file", active=True)
        self.assertTrue(result["pending"])
        self.assertEqual(self.library.metadata_for_track(str(self.alpha)).title, "Queued title")
        self.assertEqual(len(self.library.pending_file_tag_writes()), 1)

        self.assertTrue(self.library.undo_metadata(str(self.alpha), active=True))
        self.assertEqual(self.library.pending_file_tag_writes(), [])
        self.assertEqual(self.library.metadata_for_track(str(self.alpha)).title, "Alpha")

    def test_unsupported_active_file_is_rejected_instead_of_queuing_a_write(self):
        unsupported = self.root / "unsupported.ogg"
        unsupported.write_bytes(b"unchanged")
        with self.assertRaises(EmbeddedMetadataError):
            self.library.save_metadata(
                str(unsupported), {"title": "New title"}, target="file", active=True)
        self.assertEqual(self.library.pending_file_tag_writes(), [])
        self.assertEqual(unsupported.read_bytes(), b"unchanged")

    def test_deferred_file_edit_survives_reopen_and_undo_restores_absent_tag(self):
        path = self.root / "queued.mp3"
        generated = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "0.3",
             "-c:a", "libmp3lame", str(path)], capture_output=True, timeout=20)
        if generated.returncode:
            self.skipTest("FFmpeg não oferece o codificador MP3 necessário ao teste.")
        self.assertIsNone(TinyTag.get(str(path), tags=True).title)

        result = self.library.save_metadata(
            str(path), {"title": "Queued title"}, target="file", active=True)
        self.assertTrue(result["pending"])
        reopened = MusicLibrary(self.library.database)
        self.assertEqual(len(reopened.pending_file_tag_writes()), 1)
        self.assertIsNone(TinyTag.get(str(path), tags=True).title)
        self.assertTrue(reopened.complete_pending_file_tag_write(str(path)))
        self.assertEqual(TinyTag.get(str(path), tags=True).title, "Queued title")
        self.assertTrue(reopened.undo_metadata(str(path)))
        self.assertIsNone(TinyTag.get(str(path), tags=True).title)
        self.assertEqual(reopened.metadata_for_track(str(path)).title, "queued")

    def test_staging_failure_and_unsupported_format_leave_original_unchanged(self):
        path = self.root / "preserve.mp3"
        generated = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "0.2",
             "-c:a", "libmp3lame", str(path)], capture_output=True, timeout=20)
        if generated.returncode:
            self.skipTest("FFmpeg não oferece o codificador MP3 necessário ao teste.")
        original = path.read_bytes()
        with patch("radiostudio.services.embedded_metadata.subprocess.run",
                   return_value=SimpleNamespace(returncode=1, stderr=b"write failed")):
            with self.assertRaises(EmbeddedMetadataError):
                write_embedded_metadata(path, {"title": "Must not replace"})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.glob(".preserve.radiostudio-*")), [])

        unsupported = self.root / "keep.txt"
        unsupported.write_bytes(b"keep this")
        with self.assertRaises(EmbeddedMetadataError):
            write_embedded_metadata(unsupported, {"title": "No"})
        self.assertEqual(unsupported.read_bytes(), b"keep this")

    def test_external_file_change_during_tag_write_is_preserved(self):
        path = self.root / "concurrent.mp3"
        generated = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "0.2",
             "-c:a", "libmp3lame", str(path)], capture_output=True, timeout=20)
        if generated.returncode:
            self.skipTest("FFmpeg não oferece o codificador MP3 necessário ao teste.")

        original_get = TinyTag.get

        def mutate_original_after_staging(source, *args, **kwargs):
            result = original_get(source, *args, **kwargs)
            if Path(source).name.startswith(".concurrent.radiostudio-"):
                with path.open("ab") as current:
                    current.write(b"external update")
            return result

        with patch("radiostudio.services.embedded_metadata.TinyTag.get",
                   side_effect=mutate_original_after_staging):
            with self.assertRaisesRegex(EmbeddedMetadataError, "alterado durante a edição"):
                write_embedded_metadata(path, {"title": "Must not overwrite"})
        self.assertTrue(path.read_bytes().endswith(b"external update"))
        self.assertEqual(list(path.parent.glob(".concurrent.radiostudio-*")), [])

    def test_verified_containers_keep_duration_and_unsupported_formats_are_internal_only(self):
        formats = {
            "mp3": (["-c:a", "libmp3lame"], []),
            "m4a": (["-c:a", "aac"], ["-f", "ipod"]),
            "m4b": (["-c:a", "aac"], ["-f", "ipod"]),
            "flac": (["-c:a", "flac"], []),
            "opus": (["-c:a", "libopus"], []),
            "wav": (["-c:a", "pcm_s16le"], []),
        }
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        for extension, (codec, muxer) in formats.items():
            with self.subTest(extension=extension):
                path = self.root / f"verified.{extension}"
                generated = subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                     "-i", "sine=frequency=440:sample_rate=44100", "-t", "0.3",
                     *codec, *muxer, str(path)], capture_output=True, timeout=20)
                if generated.returncode:
                    self.fail(f"Não foi possível criar o arquivo de teste {extension}.")
                before = TinyTag.get(str(path), tags=True, duration=True).duration
                write_embedded_metadata(path, {"title": "Verified"})
                after = TinyTag.get(str(path), tags=True, duration=True).duration
                self.assertEqual(TinyTag.get(str(path), tags=True).title, "Verified")
                self.assertAlmostEqual(after, before, delta=0.25)
        self.assertNotIn(".ogg", SUPPORTED_EXTENSIONS)
        self.assertNotIn(".aac", SUPPORTED_EXTENSIONS)
        unsupported = self.root / "unsupported.ogg"
        unsupported.write_bytes(b"leave unchanged")
        with self.assertRaises(EmbeddedMetadataError):
            write_embedded_metadata(unsupported, {"title": "No"})
        self.assertEqual(unsupported.read_bytes(), b"leave unchanged")

    def test_library_and_editor_open_offscreen_with_multiline_fields(self):
        panel = LibraryPanel(SimpleNamespace(music_library=self.library))
        editor = MetadataEditorDialog(self.library, [str(self.alpha)], default_target="file")
        try:
            self.assertEqual(panel.catalog_table.columnCount(), 10)
            self.assertIsInstance(editor.field_controls["lyrics"][1], QPlainTextEdit)
            self.assertEqual(editor.target.currentData(), "file")
            apply, edit, _multi = editor.field_controls["title"]
            clear = editor.clear_controls["title"]
            apply.setChecked(True)
            edit.clear()
            self.assertNotIn("title", editor.values()["fields"])
            clear.setChecked(True)
            self.assertIn("title", editor.values()["clear_fields"])
            apply.setChecked(False)
            self.assertNotIn("title", editor.values()["clear_fields"])
        finally:
            editor.close()
            panel.close()

    def test_double_clicking_catalog_track_adds_it_to_queue_without_playing(self):
        main_window = SimpleNamespace(
            music_library=self.library, tracks=[], add_audio_paths=Mock(),
            play_index=Mock())
        panel = LibraryPanel(main_window)
        try:
            panel.refresh_catalog()
            panel.catalog_table.selectRow(0)
            panel.catalog_table.doubleClicked.emit(panel.catalog_table.model().index(0, 0))
            main_window.add_audio_paths.assert_called_once_with([str(self.alpha.resolve())])
            main_window.play_index.assert_not_called()
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
