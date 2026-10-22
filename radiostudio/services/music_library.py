"""SQLite-backed music-library index and durable playback history."""

from __future__ import annotations

import sqlite3
import stat
import tempfile
import time
import uuid
import json
import base64
import re
from dataclasses import fields, replace
from contextlib import contextmanager
from pathlib import Path

from ..config import AUDIO_EXTENSIONS, MUSIC_LIBRARY_FILE
from .audio_paths import audio_path_key
from .track_metadata import TrackMetadata, read_track_metadata


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
                CREATE TABLE IF NOT EXISTS metadata_overrides (
                    path_key TEXT PRIMARY KEY NOT NULL, path TEXT NOT NULL,
                    fields_json TEXT NOT NULL DEFAULT '{}',
                    custom_tags_json TEXT NOT NULL DEFAULT '{}',
                    cover_mime TEXT NOT NULL DEFAULT '', cover_data BLOB,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS library_collections (
                    id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'manual', criteria_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_tracks (
                    collection_id TEXT NOT NULL REFERENCES library_collections(id) ON DELETE CASCADE,
                    path_key TEXT NOT NULL, path TEXT NOT NULL,
                    PRIMARY KEY(collection_id, path_key)
                );
                CREATE TABLE IF NOT EXISTS track_tags (
                    path_key TEXT NOT NULL, path TEXT NOT NULL, tag TEXT NOT NULL,
                    PRIMARY KEY(path_key, tag)
                );
                CREATE TABLE IF NOT EXISTS rotation_groups (
                    id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS track_rotation_groups (
                    path_key TEXT NOT NULL, path TEXT NOT NULL,
                    group_id TEXT NOT NULL REFERENCES rotation_groups(id) ON DELETE CASCADE,
                    PRIMARY KEY(path_key, group_id)
                );
                CREATE TABLE IF NOT EXISTS metadata_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, path_key TEXT NOT NULL,
                    path TEXT NOT NULL, target TEXT NOT NULL, created_at REAL NOT NULL,
                    before_json TEXT NOT NULL, after_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending_tag_writes (
                    path_key TEXT PRIMARY KEY NOT NULL, path TEXT NOT NULL,
                    fields_json TEXT NOT NULL, custom_tags_json TEXT NOT NULL,
                    cover_mime TEXT NOT NULL DEFAULT '', cover_data BLOB,
                    clear_cover INTEGER NOT NULL DEFAULT 0,
                    preserve_overlay INTEGER NOT NULL DEFAULT 0, queued_at REAL NOT NULL
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
                "pending_tag_writes": {
                    "preserve_overlay": "INTEGER NOT NULL DEFAULT 0",
                },
            })
            db.executescript("""
                CREATE INDEX IF NOT EXISTS tracks_library_idx ON tracks(library_id);
                CREATE INDEX IF NOT EXISTS tracks_artist_idx ON tracks(artist COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS tracks_genre_idx ON tracks(genre COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS play_history_time_idx ON play_history(played_at DESC);
                CREATE INDEX IF NOT EXISTS play_history_path_idx ON play_history(path, played_at DESC);
                CREATE INDEX IF NOT EXISTS play_history_artist_idx ON play_history(artist COLLATE NOCASE, played_at DESC);
                CREATE INDEX IF NOT EXISTS collection_tracks_path_idx ON collection_tracks(path_key);
                CREATE INDEX IF NOT EXISTS track_tags_tag_idx ON track_tags(tag COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS rotation_groups_path_idx ON track_rotation_groups(path_key);
                CREATE INDEX IF NOT EXISTS metadata_history_path_idx ON metadata_history(path_key, id DESC);
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
                                    override_row = db.execute(
                                        "SELECT fields_json FROM metadata_overrides WHERE path_key=?",
                                        (audio_path_key(path),)).fetchone()
                                    override = self._json_object(override_row[0]) if override_row else {}
                                    indexed_values = {name: getattr(metadata, name) for name in
                                                      ("title", "artist", "album", "albumartist",
                                                       "genre", "year", "composer", "duration")}
                                    indexed_values.update({name: override[name] for name in
                                                           ("title", "artist", "album", "albumartist",
                                                            "genre", "year", "composer") if name in override})
                                    indexed_values = {name: "; ".join(map(str, value))
                                                      if isinstance(value, list) else value
                                                      for name, value in indexed_values.items()}
                                    batch.append((path, library_id, file_info.st_mtime_ns,
                                                  file_info.st_size, indexed_values["title"],
                                                  indexed_values["artist"], indexed_values["album"],
                                                  indexed_values["albumartist"], indexed_values["genre"],
                                                  indexed_values["year"], indexed_values["composer"],
                                                  indexed_values["duration"]))
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

    def apply_metadata_overrides(self, tracks):
        """Apply overlays to indexed row dictionaries without rereading files."""
        tracks = [dict(track) for track in tracks]
        if not tracks:
            return tracks
        keys = list({audio_path_key(track.get("path", "")) for track in tracks} - {""})
        overrides = {}
        with self._connect() as db:
            for offset in range(0, len(keys), 400):
                group = keys[offset:offset + 400]
                if not group:
                    continue
                marks = ",".join("?" for _ in group)
                for row in db.execute(
                        f"SELECT path_key,fields_json FROM metadata_overrides "
                        f"WHERE path_key IN ({marks})", group):
                    overrides[row["path_key"]] = self._json_object(row["fields_json"])
        indexed_fields = {"title", "artist", "album", "albumartist", "genre", "year",
                          "composer", "comments", "lyrics"}
        for track in tracks:
            values = overrides.get(audio_path_key(track.get("path", "")), {})
            for name in indexed_fields.intersection(values):
                value = values[name]
                track[name] = "; ".join(map(str, value)) if isinstance(value, list) else str(value or "")
        return tracks

    def catalog_tracks(self, *, search="", library_id=None, collection_id=None,
                       tag=None, group_id=None, sort_by="title", descending=False):
        """Return enabled indexed tracks with overlays and organization labels applied."""
        with self._connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT tracks.* FROM tracks JOIN libraries ON libraries.id=tracks.library_id "
                "WHERE libraries.enabled=1 ORDER BY tracks.title COLLATE NOCASE")]
            collections = {str(row["id"]): dict(row) for row in db.execute(
                "SELECT * FROM library_collections")}
            manual_members = {}
            for row in db.execute("SELECT collection_id,path_key FROM collection_tracks"):
                manual_members.setdefault(str(row["collection_id"]), set()).add(str(row["path_key"]))
            tags_by_path = {}
            for row in db.execute("SELECT path_key,tag FROM track_tags"):
                tags_by_path.setdefault(str(row["path_key"]), []).append(str(row["tag"]))
            groups_by_path = {}
            for row in db.execute("SELECT path_key,group_id FROM track_rotation_groups"):
                groups_by_path.setdefault(str(row["path_key"]), []).append(str(row["group_id"]))
            group_names = {str(row["id"]): str(row["name"]) for row in db.execute(
                "SELECT id,name FROM rotation_groups")}
        rows = self.apply_metadata_overrides(rows)
        collection_names_by_path = {}
        all_rows_by_path = {audio_path_key(row.get("path", "")): row for row in rows}
        from .autodj import filter_tracks
        for item_id, item in collections.items():
            if item["kind"] == "manual":
                members = manual_members.get(item_id, set())
            else:
                criteria = self._json_object(item.get("criteria_json"))
                members = {audio_path_key(row["path"]) for row in filter_tracks(rows, criteria)}
            for path_key in members:
                if path_key in all_rows_by_path:
                    collection_names_by_path.setdefault(path_key, []).append(item["name"])
        smart_criteria = {}
        if collection_id:
            collection = collections.get(str(collection_id))
            if collection is None:
                return []
            if collection["kind"] == "manual":
                members = manual_members.get(str(collection_id), set())
                rows = [row for row in rows if audio_path_key(row.get("path", "")) in members]
            else:
                smart_criteria = self._json_object(collection.get("criteria_json"))
        if smart_criteria:
            rows = filter_tracks(rows, smart_criteria)
        if library_id:
            rows = [row for row in rows if str(row.get("library_id")) == str(library_id)]
        # A folder can be indexed in more than one library. Show it once in the
        # shared catalog, while retaining the selected library's own view.
        deduplicated = {}
        for row in rows:
            deduplicated.setdefault(audio_path_key(row.get("path", "")), row)
        rows = list(deduplicated.values())
        if tag:
            wanted_tag = str(tag).casefold()
            rows = [row for row in rows if wanted_tag in
                    {value.casefold() for value in tags_by_path.get(audio_path_key(row["path"]), [])}]
        if group_id:
            rows = [row for row in rows if str(group_id) in
                    groups_by_path.get(audio_path_key(row["path"]), [])]
        if search:
            needle = str(search).strip().casefold()
            if needle:
                rows = [row for row in rows if needle in " ".join(
                    [str(row.get(field, "")) for field in
                     ("path", "title", "artist", "album", "albumartist", "genre", "year", "composer")]
                    + tags_by_path.get(audio_path_key(row["path"]), [])).casefold()]
        for row in rows:
            key = audio_path_key(row.get("path", ""))
            row["tags"] = tags_by_path.get(key, [])
            row["rotation_groups"] = groups_by_path.get(key, [])
            row["group_names"] = [group_names[group_id] for group_id in row["rotation_groups"]
                                   if group_id in group_names]
            row["collection_names"] = collection_names_by_path.get(key, [])
            row["folder"] = str(Path(row.get("path", "")).parent)
        allowed_sort = {"title", "artist", "album", "albumartist", "composer", "genre",
                        "year", "duration", "tags", "collections", "groups", "folder", "path"}
        sort_by = sort_by if sort_by in allowed_sort else "title"
        def sort_value(row):
            if sort_by == "tags":
                return ", ".join(row.get("tags", []))
            if sort_by == "collections":
                return ", ".join(row.get("collection_names", []))
            if sort_by == "groups":
                return ", ".join(row.get("group_names", []))
            if sort_by == "duration":
                try:
                    return float(row.get("duration", 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    return 0.0
            return str(row.get(sort_by, "") or "")
        def sort_key(row):
            value = sort_value(row)
            if isinstance(value, str):
                value = value.casefold()
            return value, str(row.get("path", "")).casefold()
        rows.sort(key=sort_key, reverse=bool(descending))
        return rows

    def unique_tags(self):
        with self._connect() as db:
            return [row[0] for row in db.execute(
                "SELECT DISTINCT tag FROM track_tags ORDER BY tag COLLATE NOCASE")]

    @staticmethod
    def _path_identity(path):
        value = str(Path(path).expanduser())
        try:
            resolved = str(Path(value).resolve())
        except OSError:
            resolved = value
        return audio_path_key(resolved), resolved

    @staticmethod
    def _json_object(raw, default=None):
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            value = None
        return value if isinstance(value, dict) else (default or {})

    def read_metadata_override(self, path):
        key, _path = self._path_identity(path)
        if not key:
            return {"fields": {}, "custom_tags": {}, "cover_mime": "", "cover_data": None}
        with self._connect() as db:
            row = db.execute("SELECT * FROM metadata_overrides WHERE path_key=?", (key,)).fetchone()
        if row is None:
            return {"fields": {}, "custom_tags": {}, "cover_mime": "", "cover_data": None}
        return {"fields": self._json_object(row["fields_json"]),
                "custom_tags": self._json_object(row["custom_tags_json"]),
                "cover_mime": str(row["cover_mime"] or ""),
                "cover_data": bytes(row["cover_data"]) if row["cover_data"] is not None else None}

    def metadata_for_track(self, path):
        """Read file metadata and apply RadioStudio's per-track overlay."""
        return self.metadata_for_tracks([path])[str(path)]

    def metadata_for_tracks(self, paths):
        """Read several tracks with one overlay query for playlist/table refreshes."""
        from .track_metadata import read_track_metadata

        paths = [str(path) for path in paths]
        keys = list(dict.fromkeys(audio_path_key(path) for path in paths if path))
        overrides = {}
        with self._connect() as db:
            for offset in range(0, len(keys), 400):
                group = keys[offset:offset + 400]
                if not group:
                    continue
                marks = ",".join("?" for _ in group)
                overrides.update({row["path_key"]: self._json_object(row["fields_json"])
                                  for row in db.execute(
                    f"SELECT path_key,fields_json FROM metadata_overrides "
                    f"WHERE path_key IN ({marks})", group)})
        result = {}
        supported = {field.name for field in fields(TrackMetadata)} - {"path"}
        for path in paths:
            base = read_track_metadata(path)
            updates = {name: value for name, value in overrides.get(audio_path_key(path), {}).items()
                       if name in supported}
            for name, value in list(updates.items()):
                if isinstance(value, (list, tuple)):
                    updates[name] = "; ".join(str(item) for item in value)
                elif value is not None:
                    updates[name] = str(value)
            updates["year"] = str(updates.get("year", base.year) or "")
            result[path] = replace(base, **updates) if updates else base
        return result

    def save_metadata(self, path, fields_to_set, *, clear_fields=(), custom_tags=None,
                      cover_data=None, cover_mime="", clear_cover=False,
                      target="internal", active=False):
        """Save selected tag fields internally or to the audio file.

        A file-tag write requested for the currently active track is staged in
        the database and reported as pending; the caller can complete it after
        playback releases the file.
        """
        from .embedded_metadata import EmbeddedMetadataError, SUPPORTED_EXTENSIONS, write_embedded_metadata

        key, canonical_path = self._path_identity(path)
        if not key:
            raise ValueError("O caminho da faixa é inválido.")
        if target not in {"internal", "file"}:
            raise ValueError("O destino dos metadados é inválido.")
        if target == "file" and Path(canonical_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise EmbeddedMetadataError(
                "A gravação de tags não está disponível para este formato; use os metadados internos.")
        if not isinstance(fields_to_set, dict):
            raise ValueError("Os metadados precisam ser um objeto de campos.")
        allowed = {"title", "artist", "album", "albumartist", "genre", "year",
                   "track", "composer", "comments", "lyrics"}
        selected = {}
        for name, value in fields_to_set.items():
            if name not in allowed:
                continue
            text = value if isinstance(value, list) else str(value or "")
            if isinstance(text, list):
                text = [str(item)[:4096] for item in text[:100]]
            else:
                text = text[:16384]
            selected[name] = text
        for name in clear_fields or ():
            if name in allowed:
                selected[name] = ""
        tags = self._validate_custom_tags(custom_tags or {})
        if cover_data is not None:
            cover_data = bytes(cover_data)
            if len(cover_data) > 8 * 1024 * 1024:
                raise ValueError("A capa excede o limite de 8 MB.")
        if cover_data is not None and clear_cover:
            raise ValueError("Escolha uma capa ou a remoção da capa, não ambos.")

        before = self.read_metadata_override(canonical_path)
        after = {"fields": dict(before["fields"]),
                 "custom_tags": dict(before["custom_tags"]),
                 "cover_mime": before["cover_mime"],
                 "cover_data": before["cover_data"]}
        after["fields"].update(selected)
        if custom_tags is not None:
            for name, value in tags.items():
                if value == "" or value == []:
                    after["custom_tags"].pop(name, None)
                else:
                    after["custom_tags"][name] = value
        if cover_data is not None:
            after["cover_data"] = cover_data
            after["cover_mime"] = str(cover_mime or "application/octet-stream")[:80]
        elif clear_cover:
            after["cover_data"] = None
            after["cover_mime"] = ""

        embedded_before = self._file_metadata_snapshot(canonical_path) if target == "file" else None
        wrote_file = False
        if target == "file" and not active:
            write_embedded_metadata(canonical_path, selected, tags if custom_tags is not None else {},
                                    cover_data=cover_data, clear_cover=clear_cover)
            wrote_file = True
            # Keep lyrics internally because TinyTag does not expose ID3 USLT
            # or equivalent properties to playback and station displays.
            after["fields"] = dict(before["fields"])
            for name in selected:
                if name == "lyrics":
                    after["fields"][name] = selected[name]
                else:
                    after["fields"].pop(name, None)
            after["custom_tags"] = dict(before["custom_tags"])
            if custom_tags is not None:
                for name in tags:
                    after["custom_tags"].pop(name, None)
            if cover_data is not None or clear_cover:
                after["cover_data"] = None
                after["cover_mime"] = ""
        elif target == "file" and active:
            with self._connect() as db:
                db.execute("""INSERT INTO pending_tag_writes(path_key,path,fields_json,
                    custom_tags_json,cover_mime,cover_data,clear_cover,queued_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(path_key) DO UPDATE SET
                    fields_json=excluded.fields_json, custom_tags_json=excluded.custom_tags_json,
                    cover_mime=excluded.cover_mime, cover_data=excluded.cover_data,
                    clear_cover=excluded.clear_cover, queued_at=excluded.queued_at""",
                    (key, canonical_path, json.dumps(selected, ensure_ascii=False),
                     json.dumps(tags, ensure_ascii=False), str(cover_mime or ""), cover_data,
                     int(bool(clear_cover)), time.time()))

        def snapshot(value):
            data = dict(value)
            data["cover_data"] = (base64.b64encode(value["cover_data"]).decode("ascii")
                                  if value["cover_data"] is not None else None)
            return data
        before_snapshot = snapshot(before)
        if embedded_before is not None:
            before_snapshot["embedded"] = snapshot(embedded_before)
            before_snapshot["written_fields"] = list(selected)
            before_snapshot["written_custom_tags"] = list(tags if custom_tags is not None else ())
            before_snapshot["cover_touched"] = bool(cover_data is not None or clear_cover)
        with self._connect() as db:
            db.execute("""INSERT INTO metadata_overrides(path_key,path,fields_json,
                custom_tags_json,cover_mime,cover_data,updated_at) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(path_key) DO UPDATE SET path=excluded.path,
                fields_json=excluded.fields_json,custom_tags_json=excluded.custom_tags_json,
                cover_mime=excluded.cover_mime,cover_data=excluded.cover_data,
                updated_at=excluded.updated_at""",
                (key, canonical_path, json.dumps(after["fields"], ensure_ascii=False),
                 json.dumps(after["custom_tags"], ensure_ascii=False), after["cover_mime"],
                 after["cover_data"], time.time()))
            db.execute("INSERT INTO metadata_history(path_key,path,target,created_at,before_json,after_json) "
                       "VALUES(?,?,?,?,?,?)",
                       (key, canonical_path, target, time.time(),
                        json.dumps(before_snapshot, ensure_ascii=False),
                        json.dumps(snapshot(after), ensure_ascii=False)))
            self._refresh_indexed_track(db, key, canonical_path)
        if wrote_file:
            self.refresh_indexed_metadata(canonical_path)
        return {"pending": target == "file" and active, "target": target}

    @staticmethod
    def _validate_custom_tags(values):
        if not isinstance(values, dict):
            raise ValueError("As tags personalizadas precisam ser um objeto.")
        result = {}
        for name, value in values.items():
            name = str(name).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
                continue
            if isinstance(value, (list, tuple)):
                result[name] = [str(item)[:4096] for item in value[:100]]
            else:
                result[name] = str(value or "")[:16384]
        return result

    @staticmethod
    def _refresh_indexed_track(db, path_key, path):
        override = db.execute("SELECT fields_json FROM metadata_overrides WHERE path_key=?",
                              (path_key,)).fetchone()
        if override is None:
            return
        changes = MusicLibrary._json_object(override[0])
        columns = {"title", "artist", "album", "albumartist", "genre", "year", "composer"}
        updates = {key: changes[key] for key in columns if key in changes}
        if not updates:
            return
        assignments = ",".join(f'"{name}"=?' for name in updates)
        values = ["; ".join(map(str, value)) if isinstance(value, list) else str(value or "")
                  for value in updates.values()]
        for indexed_path, in db.execute("SELECT DISTINCT path FROM tracks"):
            if audio_path_key(indexed_path) == path_key:
                db.execute(f"UPDATE tracks SET {assignments} WHERE path=?",
                           (*values, indexed_path))

    def undo_metadata(self, path, *, active=False):
        key, _canonical = self._path_identity(path)
        with self._connect() as db:
            row = db.execute("SELECT * FROM metadata_history WHERE path_key=? ORDER BY id DESC LIMIT 1",
                             (key,)).fetchone()
            if row is None:
                return False
            before = self._json_object(row["before_json"])
            pending = db.execute("SELECT 1 FROM pending_tag_writes WHERE path_key=?", (key,)).fetchone()
        embedded = before.get("embedded") if row["target"] == "file" else None
        if row["target"] == "file" and pending:
            with self._connect() as db:
                db.execute("DELETE FROM pending_tag_writes WHERE path_key=?", (key,))
        elif row["target"] == "file" and isinstance(embedded, dict):
            cover = embedded.get("cover_data")
            try:
                cover = base64.b64decode(cover, validate=True) if cover else None
            except (ValueError, base64.binascii.Error):
                cover = None
            written_fields = before.get("written_fields", [])
            fields_value = embedded.get("fields", {})
            fields_to_restore = {name: fields_value.get(name, "") for name in written_fields
                                 if name in fields_value}
            written_tags = before.get("written_custom_tags", [])
            tags_to_restore = {str(name): embedded.get("custom_tags", {}).get(name, "")
                               for name in written_tags}
            clear_cover = bool(before.get("cover_touched") and cover is None)
            if active:
                with self._connect() as db:
                    db.execute("""INSERT INTO pending_tag_writes(path_key,path,fields_json,
                        custom_tags_json,cover_mime,cover_data,clear_cover,preserve_overlay,queued_at)
                        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(path_key) DO UPDATE SET
                        fields_json=excluded.fields_json,custom_tags_json=excluded.custom_tags_json,
                        cover_mime=excluded.cover_mime,cover_data=excluded.cover_data,
                        clear_cover=excluded.clear_cover,preserve_overlay=excluded.preserve_overlay,
                        queued_at=excluded.queued_at""",
                        (key, row["path"], json.dumps(fields_to_restore, ensure_ascii=False),
                         json.dumps(tags_to_restore, ensure_ascii=False),
                         embedded.get("cover_mime", ""), cover, int(clear_cover), 1, time.time()))
            else:
                from .embedded_metadata import write_embedded_metadata
                write_embedded_metadata(row["path"], fields_to_restore, tags_to_restore,
                                        cover_data=cover, cover_mime=embedded.get("cover_mime", ""),
                                        clear_cover=clear_cover)
        cover = before.get("cover_data")
        try:
            cover = base64.b64decode(cover, validate=True) if cover else None
        except (ValueError, base64.binascii.Error):
            cover = None
        with self._connect() as db:
            db.execute("""INSERT INTO metadata_overrides(path_key,path,fields_json,
                custom_tags_json,cover_mime,cover_data,updated_at) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(path_key) DO UPDATE SET fields_json=excluded.fields_json,
                custom_tags_json=excluded.custom_tags_json,cover_mime=excluded.cover_mime,
                cover_data=excluded.cover_data,updated_at=excluded.updated_at""",
                (key, row["path"], json.dumps(before.get("fields", {}), ensure_ascii=False),
                 json.dumps(before.get("custom_tags", {}), ensure_ascii=False),
                 str(before.get("cover_mime", "") or ""), cover, time.time()))
            db.execute("DELETE FROM metadata_history WHERE id=?", (row["id"],))
            self._refresh_indexed_track(db, key, row["path"])
        self.refresh_indexed_metadata(row["path"])
        return True

    def pending_file_tag_writes(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM pending_tag_writes ORDER BY queued_at")]

    def complete_pending_file_tag_write(self, path):
        key, _canonical = self._path_identity(path)
        with self._connect() as db:
            row = db.execute("SELECT * FROM pending_tag_writes WHERE path_key=?", (key,)).fetchone()
        if row is None:
            return False
        from .embedded_metadata import write_embedded_metadata
        write_embedded_metadata(row["path"], self._json_object(row["fields_json"]),
                                self._json_object(row["custom_tags_json"]),
                                cover_data=bytes(row["cover_data"]) if row["cover_data"] else None,
                                cover_mime=row["cover_mime"],
                                clear_cover=bool(row["clear_cover"]))
        with self._connect() as db:
            db.execute("DELETE FROM pending_tag_writes WHERE path_key=?", (key,))
            override = db.execute("SELECT * FROM metadata_overrides WHERE path_key=?", (key,)).fetchone()
            if override is not None and not bool(row["preserve_overlay"]):
                fields_value = self._json_object(override["fields_json"])
                for name, value in self._json_object(row["fields_json"]).items():
                    if name == "lyrics":
                        fields_value[name] = value
                    else:
                        fields_value.pop(name, None)
                custom_value = self._json_object(override["custom_tags_json"])
                for name in self._json_object(row["custom_tags_json"]):
                    custom_value.pop(name, None)
                cover_mime, cover_data = override["cover_mime"], override["cover_data"]
                if row["cover_data"] is not None or row["clear_cover"]:
                    cover_mime, cover_data = "", None
                db.execute("UPDATE metadata_overrides SET fields_json=?,custom_tags_json=?,"
                           "cover_mime=?,cover_data=?,updated_at=? WHERE path_key=?",
                           (json.dumps(fields_value, ensure_ascii=False),
                            json.dumps(custom_value, ensure_ascii=False), cover_mime, cover_data,
                            time.time(), key))
        self.refresh_indexed_metadata(row["path"])
        return True

    @staticmethod
    def _file_metadata_snapshot(path):
        from .embedded_metadata import read_embedded_custom_tags, read_embedded_tags
        from tinytag import TinyTag

        tag = TinyTag.get(path, tags=True, duration=False, image=True)
        raw_fields = {
            "title": "title", "artist": "artist", "album": "album",
            "albumartist": "albumartist", "genre": "genre", "year": "year",
            "track": "track", "composer": "composer", "comments": "comment",
            "lyrics": "lyrics",
        }
        fields_value = {}
        for name, attribute in raw_fields.items():
            value = getattr(tag, attribute, None)
            if isinstance(value, (list, tuple)):
                fields_value[name] = [str(item) for item in value]
            else:
                fields_value[name] = str(value) if value is not None else ""
        embedded_tags = read_embedded_tags(path)
        lyrics = next((value for name, value in embedded_tags.items()
                       if str(name).casefold() in {"lyrics", "unsyncedlyrics", "lyrics-eng"}
                       or str(name).casefold().startswith("lyrics-")), "")
        fields_value["lyrics"] = lyrics
        cover_data, cover_mime = None, ""
        try:
            image = tag.images.front_cover or tag.images.any
            if image and image.data and len(image.data) <= 8 * 1024 * 1024:
                cover_data = bytes(image.data)
                cover_mime = str(getattr(image, "mime_type", "") or "")
        except Exception:
            pass
        return {"fields": fields_value, "custom_tags": read_embedded_custom_tags(path),
                "cover_mime": cover_mime, "cover_data": cover_data}

    def refresh_indexed_metadata(self, path):
        """Refresh rows after an embedded tag replacement without rescanning folders."""
        from .track_metadata import read_track_metadata
        metadata = read_track_metadata(path)
        try:
            info = Path(path).stat()
            mtime_ns, size = info.st_mtime_ns, info.st_size
        except OSError:
            return
        key, canonical = self._path_identity(path)
        with self._connect() as db:
            override_row = db.execute("SELECT fields_json FROM metadata_overrides WHERE path_key=?",
                                      (key,)).fetchone()
            overrides = self._json_object(override_row[0]) if override_row else {}
            values = {name: getattr(metadata, name) for name in
                      ("title", "artist", "album", "albumartist", "genre", "year", "composer")}
            values.update({name: overrides[name] for name in values if name in overrides})
            values = {name: "; ".join(map(str, value)) if isinstance(value, list) else str(value or "")
                      for name, value in values.items()}
            paths = [row[0] for row in db.execute("SELECT DISTINCT path FROM tracks")
                     if audio_path_key(row[0]) == key]
            paths.append(canonical)
            for indexed_path in set(paths):
                db.execute("""UPDATE tracks SET mtime_ns=?,size=?,title=?,artist=?,album=?,
                    albumartist=?,genre=?,year=?,composer=?,duration=? WHERE path=?""",
                    (mtime_ns, size, values["title"], values["artist"], values["album"],
                     values["albumartist"], values["genre"], values["year"],
                     values["composer"], metadata.duration, indexed_path))

    def collections(self):
        with self._connect() as db:
            rows = db.execute("SELECT * FROM library_collections ORDER BY name COLLATE NOCASE").fetchall()
            result = [dict(row) for row in rows]
            for item in result:
                item["criteria"] = self._json_object(item.pop("criteria_json", "{}"))
                if item["kind"] == "manual":
                    item["track_count"] = db.execute(
                        "SELECT COUNT(*) FROM collection_tracks WHERE collection_id=?", (item["id"],)
                    ).fetchone()[0]
        smart = [item for item in result if item["kind"] == "smart"]
        if smart:
            enabled_ids = [row["id"] for row in self.libraries() if row.get("enabled")]
            tracks = self.apply_metadata_overrides(self.tracks_for_libraries(enabled_ids))
            from .autodj import filter_tracks
            for item in smart:
                item["track_count"] = len(filter_tracks(tracks, item["criteria"]))
        return result

    def save_collection(self, name, *, collection_id=None, kind="manual", criteria=None, paths=()):
        name = str(name or "").strip()
        if not name:
            raise ValueError("Informe um nome para a coleção.")
        if kind not in {"manual", "smart"}:
            raise ValueError("O tipo da coleção é inválido.")
        collection_id = str(collection_id or uuid.uuid4().hex)
        now = time.time()
        with self._connect() as db:
            db.execute("""INSERT INTO library_collections(id,name,kind,criteria_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                kind=excluded.kind,criteria_json=excluded.criteria_json,updated_at=excluded.updated_at""",
                (collection_id, name, kind, json.dumps(criteria or {}, ensure_ascii=False), now, now))
            if kind == "manual":
                db.execute("DELETE FROM collection_tracks WHERE collection_id=?", (collection_id,))
                db.executemany("INSERT OR IGNORE INTO collection_tracks(collection_id,path_key,path) VALUES(?,?,?)",
                               [(collection_id, *self._path_identity(path)) for path in paths])
        return collection_id

    def add_tracks_to_collection(self, collection_id, paths):
        """Add manual-collection members without replacing hidden or unavailable entries."""
        collection_id = str(collection_id)
        members = []
        for path in paths:
            path_key, canonical_path = self._path_identity(path)
            if path_key:
                members.append((collection_id, path_key, canonical_path))
        with self._connect() as db:
            collection = db.execute(
                "SELECT kind FROM library_collections WHERE id=?", (collection_id,)
            ).fetchone()
            if collection is None or collection["kind"] != "manual":
                raise ValueError("A coleção selecionada não é uma coleção manual disponível.")
            db.executemany(
                "INSERT OR IGNORE INTO collection_tracks(collection_id,path_key,path) VALUES(?,?,?)",
                members)

    def delete_collection(self, collection_id):
        with self._connect() as db:
            db.execute("DELETE FROM library_collections WHERE id=?", (str(collection_id),))

    def tags_for_track(self, path):
        key, _ = self._path_identity(path)
        with self._connect() as db:
            return [row[0] for row in db.execute(
                "SELECT tag FROM track_tags WHERE path_key=? ORDER BY tag COLLATE NOCASE", (key,))]

    def set_track_tags(self, path, tags):
        key, canonical = self._path_identity(path)
        normalized = list(dict.fromkeys(str(tag).strip()[:80] for tag in tags
                                        if str(tag).strip()))
        with self._connect() as db:
            db.execute("DELETE FROM track_tags WHERE path_key=?", (key,))
            db.executemany("INSERT OR IGNORE INTO track_tags(path_key,path,tag) VALUES(?,?,?)",
                           [(key, canonical, tag) for tag in normalized[:100]])

    def rotation_groups(self):
        with self._connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM rotation_groups ORDER BY name COLLATE NOCASE")]
            for row in rows:
                row["track_count"] = db.execute(
                    "SELECT COUNT(*) FROM track_rotation_groups WHERE group_id=?", (row["id"],)
                ).fetchone()[0]
        return rows

    def save_rotation_group(self, name, weight=1.0, group_id=None):
        name = str(name or "").strip()
        weight = float(weight)
        if not name or not 0 < weight <= 100:
            raise ValueError("Informe nome e peso entre 0 e 100.")
        group_id = str(group_id or uuid.uuid4().hex)
        with self._connect() as db:
            db.execute("INSERT INTO rotation_groups(id,name,weight,created_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET name=excluded.name,weight=excluded.weight",
                       (group_id, name, weight, time.time()))
        return group_id

    def delete_rotation_group(self, group_id):
        with self._connect() as db:
            db.execute("DELETE FROM rotation_groups WHERE id=?", (str(group_id),))

    def set_track_rotation_groups(self, path, group_ids):
        key, canonical = self._path_identity(path)
        ids = list(dict.fromkeys(str(value) for value in group_ids))
        with self._connect() as db:
            db.execute("DELETE FROM track_rotation_groups WHERE path_key=?", (key,))
            db.executemany("INSERT OR IGNORE INTO track_rotation_groups(path_key,path,group_id) "
                           "VALUES(?,?,?)", [(key, canonical, group_id) for group_id in ids])

    def track_organization(self, path):
        key, _ = self._path_identity(path)
        with self._connect() as db:
            collections = [row[0] for row in db.execute(
                "SELECT collection_id FROM collection_tracks WHERE path_key=?", (key,))]
            groups = [row[0] for row in db.execute(
                "SELECT group_id FROM track_rotation_groups WHERE path_key=?", (key,))]
            tags = [row[0] for row in db.execute(
                "SELECT tag FROM track_tags WHERE path_key=? ORDER BY tag COLLATE NOCASE", (key,))]
        return {"collections": collections, "groups": groups, "tags": tags}

    def tracks_for_sources(self, sources):
        """Merge AutoDJ source pools; overlapping paths keep only their max weight."""
        sources = [source for source in (sources or []) if isinstance(source, dict)]
        if not sources:
            return []
        libraries = {str(row["id"]): row for row in self.libraries() if row.get("enabled")}
        all_tracks = self.apply_metadata_overrides(self.tracks_for_libraries(list(libraries)))
        by_path = {audio_path_key(row.get("path", "")): row for row in all_tracks}
        selected: dict[str, tuple[dict, float]] = {}
        collections = {str(item["id"]): item for item in self.collections()}
        groups = {str(item["id"]): item for item in self.rotation_groups()}
        with self._connect() as db:
            manual_map = {}
            for row in db.execute("SELECT collection_id,path_key FROM collection_tracks"):
                manual_map.setdefault(str(row["collection_id"]), set()).add(str(row["path_key"]))
            group_map = {}
            for row in db.execute("SELECT group_id,path_key FROM track_rotation_groups"):
                group_map.setdefault(str(row["group_id"]), set()).add(str(row["path_key"]))
            tag_map = {}
            for row in db.execute("SELECT tag,path_key FROM track_tags"):
                tag_map.setdefault(str(row["tag"]).casefold(), set()).add(str(row["path_key"]))
        for source in sources:
            kind = str(source.get("type", "library"))
            source_id = str(source.get("id", ""))
            try:
                weight = max(0.0, min(100.0, float(source.get("weight", 1))))
            except (TypeError, ValueError, OverflowError):
                weight = 1.0
            if kind == "library":
                paths = {audio_path_key(row["path"]) for row in all_tracks
                         if str(row.get("library_id")) == source_id}
            elif kind == "collection" and source_id in collections:
                collection = collections[source_id]
                if collection["kind"] == "manual":
                    paths = manual_map.get(source_id, set())
                else:
                    from .autodj import filter_tracks
                    paths = {audio_path_key(row["path"]) for row in
                             filter_tracks(all_tracks, collection.get("criteria", {}))}
            elif kind == "group":
                paths = group_map.get(source_id, set())
            elif kind == "tag":
                paths = tag_map.get(source_id.casefold(), set())
            else:
                continue
            if kind == "group" and source_id in groups:
                if "weight" not in source:
                    weight = float(groups[source_id].get("weight", 1))
            for path_key in paths:
                track = by_path.get(path_key)
                if track is None:
                    continue
                previous = selected.get(path_key)
                if previous is None or weight > previous[1]:
                    selected[path_key] = (track, weight)
        return [dict(track, rotation_weight=weight) for track, weight in selected.values()]

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
