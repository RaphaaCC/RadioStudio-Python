"""SQLite-backed music-library index and durable playback history."""

from __future__ import annotations

import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..config import APP_DIR, AUDIO_EXTENSIONS
from .audio_paths import audio_path_key
from .track_metadata import read_track_metadata


class MusicLibrary:
    """Stores indexed tracks and a metadata snapshot for every played track."""

    def __init__(self, database: Path | None = None):
        self.database = Path(database or (APP_DIR / "music_library.sqlite3"))
        # Trigger one retention check on the first play after startup, then batch
        # checks to keep per-track writes inexpensive.
        self._plays_since_prune = 100
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS libraries (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1, last_scan REAL, track_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tracks (
                    path TEXT NOT NULL, library_id TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
                    mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL, title TEXT NOT NULL,
                    artist TEXT NOT NULL DEFAULT '', album TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(path, library_id)
                );
                CREATE TABLE IF NOT EXISTS play_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, played_at REAL NOT NULL,
                    path TEXT NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '',
                    album TEXT NOT NULL DEFAULT ''
                );
            """)
            self._ensure_columns(db, {
                "libraries": {
                    "enabled": "INTEGER NOT NULL DEFAULT 1",
                    "last_scan": "REAL",
                    "track_count": "INTEGER NOT NULL DEFAULT 0",
                },
                "tracks": {
                    "mtime_ns": "INTEGER NOT NULL DEFAULT 0",
                    "size": "INTEGER NOT NULL DEFAULT 0",
                    "title": "TEXT NOT NULL DEFAULT ''",
                    "artist": "TEXT NOT NULL DEFAULT ''",
                    "album": "TEXT NOT NULL DEFAULT ''",
                    "albumartist": "TEXT NOT NULL DEFAULT ''",
                    "genre": "TEXT NOT NULL DEFAULT ''",
                    "year": "TEXT NOT NULL DEFAULT ''",
                    "composer": "TEXT NOT NULL DEFAULT ''",
                    "duration": "REAL NOT NULL DEFAULT 0",
                },
                "play_history": {
                    "played_at": "REAL NOT NULL DEFAULT 0",
                    "path": "TEXT NOT NULL DEFAULT ''",
                    "title": "TEXT NOT NULL DEFAULT ''",
                    "artist": "TEXT NOT NULL DEFAULT ''",
                    "album": "TEXT NOT NULL DEFAULT ''",
                    "albumartist": "TEXT NOT NULL DEFAULT ''",
                    "genre": "TEXT NOT NULL DEFAULT ''",
                    "year": "TEXT NOT NULL DEFAULT ''",
                    "composer": "TEXT NOT NULL DEFAULT ''",
                    "duration": "REAL NOT NULL DEFAULT 0",
                },
            })
            db.executescript("""
                CREATE INDEX IF NOT EXISTS tracks_library_idx ON tracks(library_id);
                CREATE INDEX IF NOT EXISTS tracks_artist_idx ON tracks(artist COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS tracks_genre_idx ON tracks(genre COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS play_history_time_idx ON play_history(played_at DESC);
                CREATE INDEX IF NOT EXISTS play_history_path_idx ON play_history(path, played_at DESC);
                CREATE INDEX IF NOT EXISTS play_history_artist_idx ON play_history(artist COLLATE NOCASE, played_at DESC);
            """)
            db.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _ensure_columns(db, schema):
        for table, columns in schema.items():
            existing = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
            for column, definition in columns.items():
                if column not in existing:
                    db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.database), timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def libraries(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM libraries ORDER BY name COLLATE NOCASE")]

    def add_library(self, name: str, root: str):
        root_path = Path(root).expanduser().resolve()
        if not root_path.is_dir():
            raise ValueError("Selecione uma pasta existente.")
        name = name.strip() or root_path.name or str(root_path)
        library_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("INSERT INTO libraries(id, name, root) VALUES(?, ?, ?)",
                       (library_id, name, str(root_path)))
        return library_id

    def update_library(self, library_id: str, *, name=None, enabled=None):
        updates, values = [], []
        if name is not None:
            updates.append("name=?")
            values.append(str(name).strip())
        if enabled is not None:
            updates.append("enabled=?")
            values.append(int(bool(enabled)))
        if updates:
            values.append(library_id)
            with self._connect() as db:
                db.execute(f"UPDATE libraries SET {', '.join(updates)} WHERE id=?", values)

    def remove_library(self, library_id: str):
        with self._connect() as db:
            db.execute("DELETE FROM libraries WHERE id=?", (library_id,))

    @staticmethod
    def _entries(directory: Path):
        """Return one directory snapshot through a single testable filesystem boundary."""
        return list(directory.iterdir())

    def scan_library(self, library_id: str):
        """Re-index one folder tree. Returns (indexed, skipped, errors)."""
        with self._connect() as db:
            library = db.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
        if library is None:
            raise ValueError("Biblioteca não encontrada.")
        root = Path(library["root"])
        if not root.is_dir():
            raise FileNotFoundError(f"Pasta da biblioteca não encontrada: {root}")
        seen, indexed, skipped, errors = set(), 0, 0, 0
        with self._connect() as db:
            existing = {row["path"]: (row["mtime_ns"], row["size"])
                        for row in db.execute(
                            "SELECT path, mtime_ns, size FROM tracks WHERE library_id=?",
                            (library_id,))}
        # Walk one directory at a time so an inaccessible folder does not abort the scan.
        folders, discovery_errors = self._subdirectories(root)
        errors += discovery_errors
        complete = discovery_errors == 0
        batch = []
        upsert = """INSERT INTO tracks(path, library_id, mtime_ns, size, title,
            artist, album, albumartist, genre, year, composer, duration)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path, library_id) DO UPDATE SET
            mtime_ns=excluded.mtime_ns, size=excluded.size, title=excluded.title,
            artist=excluded.artist, album=excluded.album, albumartist=excluded.albumartist,
            genre=excluded.genre, year=excluded.year, composer=excluded.composer,
            duration=excluded.duration"""

        def flush_batch():
            nonlocal errors, indexed
            if not batch:
                return
            try:
                with self._connect() as db:
                    db.executemany(upsert, batch)
                indexed += len(batch)
            except sqlite3.Error:
                errors += len(batch)
            batch.clear()

        for directory in [root, *folders]:
            try:
                entries = self._entries(directory)
            except OSError:
                errors += 1
                complete = False
                continue
            for file in entries:
                try:
                    file_info = file.stat()
                    if (not stat.S_ISREG(file_info.st_mode)
                            or file.suffix.lower() not in AUDIO_EXTENSIONS):
                        continue
                    path = str(file.resolve())
                    seen.add(path)
                    if existing.get(path) == (file_info.st_mtime_ns, file_info.st_size):
                        skipped += 1
                        continue
                    metadata = read_track_metadata(path)
                    batch.append((path, library_id, file_info.st_mtime_ns, file_info.st_size,
                                  metadata.title, metadata.artist, metadata.album,
                                  metadata.albumartist, metadata.genre, metadata.year,
                                  metadata.composer, metadata.duration))
                    if len(batch) >= 100:
                        flush_batch()
                except (OSError, ValueError, sqlite3.Error):
                    errors += 1
                    complete = False
        flush_batch()
        # Remove deleted files from this library only after a completed traversal.
        with self._connect() as db:
            existing = {row[0] for row in db.execute("SELECT path FROM tracks WHERE library_id=?", (library_id,))}
            stale = existing - seen if complete else set()
            if stale:
                db.executemany("DELETE FROM tracks WHERE path=? AND library_id=?",
                               ((path, library_id) for path in stale))
            count = db.execute("SELECT COUNT(*) FROM tracks WHERE library_id=?", (library_id,)).fetchone()[0]
            db.execute("UPDATE libraries SET last_scan=?, track_count=? WHERE id=?",
                       (time.time(), count, library_id))
        return indexed, skipped, errors

    @staticmethod
    def _subdirectories(root):
        stack = []
        errors = 0
        result = []

        def add_subdirectories(folder):
            nonlocal errors
            try:
                entries = MusicLibrary._entries(folder)
            except OSError:
                errors += 1
                return []
            directories = []
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    errors += 1
                    continue
                if stat.S_ISDIR(info.st_mode):
                    directories.append(entry)
            return directories

        stack.extend(add_subdirectories(root))
        while stack:
            folder = stack.pop()
            result.append(folder)
            stack.extend(add_subdirectories(folder))
        return result, errors

    def tracks_for_libraries(self, library_ids):
        ids = [str(value) for value in library_ids]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as db:
            rows = db.execute(f"SELECT tracks.* FROM tracks JOIN libraries ON libraries.id=tracks.library_id "
                              f"WHERE tracks.library_id IN ({placeholders}) AND libraries.enabled=1",
                              ids).fetchall()
        return [dict(row) for row in rows]

    def record_play(self, metadata, played_at=None):
        with self._connect() as db:
            db.execute("""INSERT INTO play_history(played_at, path, title, artist, album,
                albumartist, genre, year, composer, duration) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (float(played_at or time.time()), metadata.path, metadata.title, metadata.artist,
                        metadata.album, metadata.albumartist, metadata.genre, metadata.year,
                        metadata.composer, metadata.duration))
            self._plays_since_prune += 1
            if self._plays_since_prune >= 100:
                self._plays_since_prune = 0
                count = db.execute("SELECT COUNT(*) FROM play_history").fetchone()[0]
                if count > 50000:
                    db.execute("DELETE FROM play_history WHERE id IN (SELECT id FROM play_history "
                               "ORDER BY played_at ASC, id ASC LIMIT ?)", (count - 50000,))

    def recent_history(self, limit=500):
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM play_history ORDER BY played_at DESC, id DESC LIMIT ?", (int(limit),))]

    def latest_track_plays(self):
        with self._connect() as db:
            rows = db.execute("SELECT path, MAX(played_at) AS played_at FROM play_history GROUP BY path")
            paths = {}
            for row in rows:
                key = audio_path_key(row["path"])
                paths[key] = max(paths.get(key, 0.0), float(row["played_at"]))
            rows = db.execute("SELECT artist, MAX(played_at) AS played_at FROM play_history "
                              "WHERE artist<>'' GROUP BY artist COLLATE NOCASE")
            artists = {str(row["artist"]).casefold(): float(row["played_at"]) for row in rows}
        return paths, artists

    def clear_history(self):
        with self._connect() as db:
            db.execute("DELETE FROM play_history")
