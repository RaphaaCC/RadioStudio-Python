"""SQLite-backed music-library index and durable playback history."""

from __future__ import annotations

import sqlite3
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..config import AUDIO_EXTENSIONS, MUSIC_LIBRARY_FILE
from .audio_paths import audio_path_key
from .track_metadata import read_track_metadata


class MusicLibrary:
    """Stores indexed tracks and a metadata snapshot for every played track."""

    def __init__(self, database: Path | None = None):
        self.database = Path(database or MUSIC_LIBRARY_FILE)
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
        """Stream one directory through a single testable filesystem boundary."""
        return directory.iterdir()

    def scan_library(self, library_id: str):
        """Re-index one folder tree. Returns (indexed, skipped, errors)."""
        with self._connect() as db:
            library = db.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
        if library is None:
            raise ValueError("Biblioteca não encontrada.")
        root = Path(library["root"])
        if not root.is_dir():
            raise FileNotFoundError(f"Pasta da biblioteca não encontrada: {root}")
        indexed, skipped, errors = 0, 0, 0
        # Process each directory as it is enumerated. This avoids a second
        # directory walk. A temporary SQLite table tracks discovered paths so
        # scans of large libraries do not keep two full path indexes in RAM.
        folders = [root]
        complete = True
        batch = []
        seen_batch = []
        upsert = """INSERT INTO tracks(path, library_id, mtime_ns, size, title,
            artist, album, albumartist, genre, year, composer, duration)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path, library_id) DO UPDATE SET
            mtime_ns=excluded.mtime_ns, size=excluded.size, title=excluded.title,
            artist=excluded.artist, album=excluded.album, albumartist=excluded.albumartist,
            genre=excluded.genre, year=excluded.year, composer=excluded.composer,
            duration=excluded.duration"""

        with tempfile.TemporaryDirectory(prefix="radiostudio-scan-") as temporary:
            seen_database = Path(temporary) / "seen.sqlite3"
            with self._connect() as db:
                db.execute("ATTACH DATABASE ? AS scan_cache", (str(seen_database),))
                db.execute("CREATE TABLE scan_cache.seen (path TEXT PRIMARY KEY NOT NULL) WITHOUT ROWID")

                def flush_batch():
                    nonlocal errors, indexed, complete
                    if not batch and not seen_batch:
                        return
                    try:
                        if seen_batch:
                            db.executemany(
                                "INSERT OR IGNORE INTO scan_cache.seen(path) VALUES(?)",
                                seen_batch)
                        if batch:
                            db.executemany(upsert, batch)
                        db.commit()
                        indexed += len(batch)
                    except sqlite3.Error:
                        db.rollback()
                        errors += max(len(batch), len(seen_batch))
                        # A partial write means this traversal is not
                        # authoritative. Keep stale rows until a later scan
                        # can index every discovered track.
                        complete = False
                    finally:
                        batch.clear()
                        seen_batch.clear()

                while folders:
                    directory = folders.pop()
                    try:
                        entries = self._entries(directory)
                    except OSError:
                        errors += 1
                        complete = False
                        continue
                    try:
                        for file in entries:
                            try:
                                entry_info = file.stat(follow_symlinks=False)
                                if stat.S_ISDIR(entry_info.st_mode):
                                    folders.append(file)
                                    continue
                                if file.suffix.lower() not in AUDIO_EXTENSIONS:
                                    continue
                                # Preserve indexing of symlinked media files,
                                # while not following directory symlinks into
                                # trees outside the library.
                                file_info = (entry_info if stat.S_ISREG(entry_info.st_mode)
                                             else file.stat())
                                if not stat.S_ISREG(file_info.st_mode):
                                    continue
                                path = str(file.resolve())
                                seen_batch.append((path,))
                                existing = db.execute(
                                    "SELECT mtime_ns, size FROM tracks WHERE library_id=? AND path=?",
                                    (library_id, path)).fetchone()
                                if (existing is not None
                                        and (existing["mtime_ns"], existing["size"])
                                        == (file_info.st_mtime_ns, file_info.st_size)):
                                    skipped += 1
                                else:
                                    metadata = read_track_metadata(path)
                                    batch.append((path, library_id, file_info.st_mtime_ns,
                                                  file_info.st_size, metadata.title,
                                                  metadata.artist, metadata.album,
                                                  metadata.albumartist, metadata.genre,
                                                  metadata.year, metadata.composer,
                                                  metadata.duration))
                                if len(seen_batch) >= 100:
                                    flush_batch()
                            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                                errors += 1
                                complete = False
                    except OSError:
                        # Path.iterdir() is lazy, so filesystem errors can be
                        # raised after iteration starts rather than on creation.
                        errors += 1
                        complete = False
                flush_batch()

                # Remove deleted files from this library only after a completed
                # traversal. The temp table keeps this cleanup bounded in RAM.
                if complete:
                    try:
                        db.execute(
                            "DELETE FROM tracks WHERE library_id=? AND NOT EXISTS ("
                            "SELECT 1 FROM scan_cache.seen AS seen WHERE seen.path=tracks.path)",
                            (library_id,))
                        db.commit()
                    except sqlite3.Error:
                        db.rollback()
                        errors += 1
                        complete = False
                count = db.execute(
                    "SELECT COUNT(*) FROM tracks WHERE library_id=?", (library_id,)
                ).fetchone()[0]
                db.execute("UPDATE libraries SET last_scan=?, track_count=? WHERE id=?",
                           (time.time(), count, library_id))
        return indexed, skipped, errors

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

    def history_after_id(self, after_id, limit=1000):
        """Fetch newly inserted history without copying the full recent page."""
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM play_history WHERE id>? "
                "ORDER BY played_at DESC, id DESC LIMIT ?",
                (max(0, int(after_id)), max(1, int(limit))))]

    def latest_track_plays(self, path_window_seconds=None,
                           artist_window_seconds=None, now=None):
        """Return latest plays only within the cooldown windows when supplied.

        AutoDJ only needs recent history for its configured repeat windows. A
        bounded indexed time range avoids grouping the entire retained history
        every time the queue is replenished. Omitting a window preserves the
        full-history behavior for older callers.
        """
        now = time.time() if now is None else float(now)

        def read_latest(db, column, window, where=""):
            if window is None:
                cutoff_clause = ""
                values = ()
            else:
                seconds = max(0.0, float(window))
                if seconds <= 0:
                    return {}
                cutoff_clause = " AND played_at>=?"
                values = (now - seconds,)
            group_column = f"{column} COLLATE NOCASE" if column == "artist" else column
            rows = db.execute(
                f"SELECT {column}, MAX(played_at) AS played_at FROM play_history "
                f"WHERE 1=1{where}{cutoff_clause} GROUP BY {group_column}", values)
            if column == "path":
                latest = {}
                for row in rows:
                    key = audio_path_key(row[column])
                    latest[key] = max(latest.get(key, 0.0), float(row["played_at"]))
                return latest
            return {str(row[column]).casefold(): float(row["played_at"]) for row in rows}

        with self._connect() as db:
            paths = read_latest(db, "path", path_window_seconds)
            artists = read_latest(db, "artist", artist_window_seconds, " AND artist<>''")
        return paths, artists

    def clear_history(self):
        with self._connect() as db:
            db.execute("DELETE FROM play_history")
