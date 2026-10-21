import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from radiostudio.services.backups import (
    BackupError, create_backup, import_backup, repair_database, validate_backup,
)
from radiostudio.services.music_library import MusicLibrary


class BackupServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.state_file = self.profile / "state.json"
        self.database_file = self.profile / "music_library.sqlite3"
        self.backups = self.root / "backups"
        self.recovery = self.root / "recovery"
        self.media = self.root / "media"
        self.media.mkdir()
        self.state_file.write_text(json.dumps({"station_metadata": {"name": "Origem"}}),
                                   encoding="utf-8")
        self.library = MusicLibrary(self.database_file)
        self.library.add_library("Acervo", str(self.media))

    def tearDown(self):
        self.temporary.cleanup()

    def _backup(self, retention=10):
        return create_backup(self.backups, retention, self.state_file,
                             self.database_file, "test-version")

    def test_export_contains_valid_state_and_library_database(self):
        result = self._backup()

        self.assertTrue(result.path.is_file())
        self.assertTrue(validate_backup(result.path)["has_library"])
        with zipfile.ZipFile(result.path) as archive:
            self.assertEqual(set(archive.namelist()), {
                "manifest.json", "state.json", "music_library.sqlite3"})

    def test_export_hashes_and_archives_database_without_loading_it_all_into_memory(self):
        original_read_bytes = Path.read_bytes

        def reject_database_snapshot_read(path):
            if (path.name == "music_library.sqlite3"
                    and path.parent.name.startswith("radiostudio-export-")):
                raise AssertionError("database snapshot must be streamed from disk")
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", reject_database_snapshot_read):
            result = self._backup()

        self.assertTrue(validate_backup(result.path)["has_library"])

    def test_retention_removes_only_old_radiostudio_backups(self):
        unrelated = self.backups / "keep-this.zip"
        self.backups.mkdir()
        unrelated.write_bytes(b"untouched")
        created = [self._backup(retention=2) for _ in range(3)]

        remaining = sorted(self.backups.glob("RadioStudio-backup-*.zip"))
        self.assertEqual(len(remaining), 2)
        self.assertEqual(unrelated.read_bytes(), b"untouched")
        self.assertEqual(created[-1].removed, 1)

    def test_import_replaces_state_and_library_and_preserves_current_profile(self):
        backup = self._backup().path
        self.state_file.write_text(json.dumps({"station_metadata": {"name": "Destino"}}),
                                   encoding="utf-8")

        result = import_backup(backup, self.state_file, self.database_file, self.recovery)

        self.assertEqual(json.loads(self.state_file.read_text(encoding="utf-8"))
                         ["station_metadata"]["name"], "Origem")
        self.assertTrue(result.restored_database)
        self.assertTrue((result.recovery_directory / "state.json").is_file())
        restored = MusicLibrary(self.database_file)
        self.assertEqual([item["name"] for item in restored.libraries()], ["Acervo"])

    def test_invalid_import_leaves_current_profile_intact(self):
        invalid = self.root / "invalid.zip"
        with zipfile.ZipFile(invalid, "w") as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("../state.json", "{}")
        original = self.state_file.read_bytes()

        with self.assertRaises(BackupError):
            import_backup(invalid, self.state_file, self.database_file, self.recovery)

        self.assertEqual(self.state_file.read_bytes(), original)
        self.assertTrue(self.database_file.is_file())

    def test_repair_prefers_a_valid_backup_for_a_corrupt_database(self):
        self._backup()
        with closing(sqlite3.connect(self.database_file)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.database_file.write_bytes(b"not a sqlite database")

        result = repair_database(self.database_file, self.backups, self.recovery)

        self.assertTrue(result.repaired)
        self.assertIn("backup", result.message.lower())
        self.assertEqual([item["name"] for item in MusicLibrary(self.database_file).libraries()],
                         ["Acervo"])
        self.assertTrue((result.recovery_directory / "music_library.raw.sqlite3").is_file())

    def test_repair_rebuilds_database_when_no_backup_can_be_used(self):
        with closing(sqlite3.connect(self.database_file)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.database_file.write_bytes(b"damaged database")

        result = repair_database(self.database_file, self.backups, self.recovery)

        self.assertTrue(result.repaired)
        self.assertIn("banco novo", result.message.lower())
        self.assertEqual(MusicLibrary(self.database_file).libraries(), [])
        self.assertTrue((result.recovery_directory / "music_library.raw.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
