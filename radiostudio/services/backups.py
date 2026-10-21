"""Safe export, import, and recovery helpers for the user profile."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
import zlib
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .music_library import MusicLibrary

logger = logging.getLogger("radiostudio")
BACKUP_PREFIX = "RadioStudio-backup-"
_BACKUP_PATTERN = re.compile(r"^RadioStudio-backup-\d{8}-\d{6}-\d{6}\.zip$")
_ALLOWED_MEMBERS = {"manifest.json", "state.json", "music_library.sqlite3"}
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_STATE_BYTES = 128 * 1024 * 1024
_MAX_DATABASE_BYTES = 20 * 1024 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024


class BackupError(ValueError):
    """A backup could not be safely read, written, or restored."""


@dataclass(frozen=True)
class BackupResult:
    path: Path
    removed: int
    retention_warning: str = ""


@dataclass(frozen=True)
class ImportResult:
    recovery_directory: Path
    restored_database: bool


@dataclass(frozen=True)
class RepairResult:
    repaired: bool
    message: str
    recovery_directory: Path | None = None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_record(data: bytes) -> dict:
    return {"size": len(data), "sha256": _digest(data)}


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def _state_object(data: bytes) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"O arquivo de configurações não contém JSON válido: {exc}") from exc
    if not isinstance(value, dict):
        raise BackupError("O arquivo de configurações precisa conter um objeto JSON.")
    return value


def _sqlite_integrity(path: Path) -> None:
    try:
        with closing(sqlite3.connect(str(path), timeout=15)) as db:
            rows = db.execute("PRAGMA integrity_check").fetchall()
        if not rows or any(row[0] != "ok" for row in rows):
            detail = "; ".join(str(row[0]) for row in rows[:5]) or "sem resultado"
            raise BackupError(f"O banco de dados não passou na verificação: {detail}")
    except (sqlite3.Error, OSError) as exc:
        raise BackupError(f"Não foi possível verificar o banco de dados: {exc}") from exc


def _snapshot_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(str(source), timeout=15)) as source_db, \
                closing(sqlite3.connect(str(target), timeout=15)) as target_db:
            source_db.backup(target_db, pages=512, sleep=0.02)
            target_db.commit()
    except (sqlite3.Error, OSError) as exc:
        target.unlink(missing_ok=True)
        raise BackupError(f"Não foi possível copiar o banco de dados: {exc}") from exc
    _sqlite_integrity(target)


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int,
                 destination: Path | None = None) -> tuple[bytes | None, int, str]:
    if info.flag_bits & 0x1:
        raise BackupError("O arquivo de backup está criptografado e não pode ser importado.")
    if info.file_size > limit:
        raise BackupError(f"O conteúdo {info.filename} excede o limite permitido.")
    digest = hashlib.sha256()
    total = 0
    data = bytearray() if destination is None else None
    output = destination.open("wb") if destination is not None else None
    try:
        with zf.open(info, "r") as source:
            while True:
                chunk = source.read(_COPY_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise BackupError(f"O conteúdo {info.filename} excede o limite permitido.")
                digest.update(chunk)
                if output is None:
                    data.extend(chunk)
                else:
                    output.write(chunk)
    except BackupError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
        raise BackupError(f"Não foi possível ler {info.filename}: {exc}") from exc
    finally:
        if output is not None:
            output.close()
    return (bytes(data) if data is not None else None, total, digest.hexdigest())


@contextmanager
def _validated_backup(backup_file: Path):
    backup_file = Path(backup_file)
    try:
        zf = zipfile.ZipFile(backup_file, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"Não foi possível abrir o arquivo de backup: {exc}") from exc

    with zf:
        infos = zf.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise BackupError("O backup contém arquivos duplicados.")
        if any(name not in _ALLOWED_MEMBERS for name in names):
            raise BackupError("O backup contém um arquivo ou caminho não permitido.")
        by_name = {info.filename: info for info in infos}
        if "manifest.json" not in by_name or "state.json" not in by_name:
            raise BackupError("O backup não contém o manifesto e as configurações exigidos.")
        if any(info.is_dir() or info.compress_type not in
               (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for info in infos):
            raise BackupError("O backup contém uma entrada inválida.")

        manifest_bytes, _, _ = _read_member(
            zf, by_name["manifest.json"], _MAX_MANIFEST_BYTES)
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError("O manifesto do backup está inválido.") from exc
        if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
            raise BackupError("A versão do formato de backup não é compatível.")
        records = manifest.get("files")
        if not isinstance(records, dict) or set(records) != set(by_name) - {"manifest.json"}:
            raise BackupError("O manifesto não corresponde aos arquivos do backup.")

        state, state_size, state_hash = _read_member(
            zf, by_name["state.json"], _MAX_STATE_BYTES)
        _state_object(state)
        state_record = records.get("state.json")
        if (not isinstance(state_record, dict) or state_record.get("size") != state_size
                or state_record.get("sha256") != state_hash):
            raise BackupError("A integridade das configurações do backup não confere.")

        with tempfile.TemporaryDirectory(prefix="radiostudio-backup-") as temporary:
            database = None
            if "music_library.sqlite3" in by_name:
                database = Path(temporary) / "music_library.sqlite3"
                size, digest = _read_member(
                    zf, by_name["music_library.sqlite3"], _MAX_DATABASE_BYTES,
                    destination=database)[1:]
                record = records.get("music_library.sqlite3")
                if (not isinstance(record, dict) or record.get("size") != size
                        or record.get("sha256") != digest):
                    raise BackupError("A integridade do banco de dados do backup não confere.")
                _sqlite_integrity(database)
            yield state, database, manifest


def validate_backup(backup_file: Path) -> dict:
    with _validated_backup(backup_file) as (_state, database, manifest):
        return {"created_at": str(manifest.get("created_at", "")),
                "has_library": database is not None,
                "app_version": str(manifest.get("app_version", ""))}


def _backup_candidates(folder: Path) -> list[Path]:
    try:
        paths = [path for path in folder.iterdir()
                 if path.is_file() and _BACKUP_PATTERN.fullmatch(path.name)]
    except OSError:
        return []
    def sort_key(path):
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            modified = 0
        return modified, path.name

    return sorted(paths, key=sort_key, reverse=True)


def create_backup(destination: Path, retention: int, state_file: Path,
                  database_file: Path, app_version: str = "") -> BackupResult:
    if not str(destination or "").strip():
        raise BackupError("Selecione uma pasta de destino para o backup.")
    destination = Path(destination).expanduser()
    state_file = Path(state_file)
    database_file = Path(database_file)
    try:
        state = state_file.read_bytes()
        _state_object(state)
        destination.mkdir(parents=True, exist_ok=True)
        retention = max(1, min(500, int(retention)))
    except (OSError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"Não foi possível preparar o backup: {exc}") from exc

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    name = f"{BACKUP_PREFIX}{_timestamp()}.zip"
    final_path = destination / name
    temporary_zip = destination / (name + ".tmp")
    try:
        with tempfile.TemporaryDirectory(prefix="radiostudio-export-") as temporary:
            files = {"state.json": state}
            database_snapshot = None
            if database_file.is_file():
                database_snapshot = Path(temporary) / "music_library.sqlite3"
                _snapshot_database(database_file, database_snapshot)
                # Keep the database on disk and let hashing/ZIP write it in
                # bounded chunks. Reading the whole snapshot here can consume
                # more RAM than the library itself on large stations.
                files["music_library.sqlite3"] = database_snapshot

            manifest = {"format_version": 1, "created_at": created_at,
                        "app_version": str(app_version),
                        "files": {key: _file_record(value) if isinstance(value, bytes)
                                  else {"size": value.stat().st_size,
                                        "sha256": _hash_file(value)}
                                  for key, value in files.items()}}
            with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=1) as zf:
                zf.writestr("manifest.json", json.dumps(
                    manifest, ensure_ascii=False, separators=(",", ":")))
                zf.writestr("state.json", state)
                if database_snapshot is not None:
                    zf.write(database_snapshot, "music_library.sqlite3")
        os.replace(temporary_zip, final_path)
    except (OSError, sqlite3.Error, zipfile.BadZipFile, BackupError) as exc:
        temporary_zip.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"Não foi possível criar o backup: {exc}") from exc

    removed = 0
    warning = ""
    try:
        candidates = _backup_candidates(destination)
        for old in candidates[retention:]:
            old.unlink()
            removed += 1
    except OSError as exc:
        warning = f"O backup foi criado, mas a retenção não pôde remover um arquivo: {exc}"
        logger.warning(warning)
    return BackupResult(final_path, removed, warning)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while True:
            chunk = source.read(_COPY_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def preserve_profile(state_file: Path, database_file: Path,
                     recovery_root: Path) -> Path:
    state_file = Path(state_file)
    database_file = Path(database_file)
    recovery_root = Path(recovery_root)
    recovery_directory = recovery_root / f"RadioStudio-recovery-{_timestamp()}"
    recovery_directory.mkdir(parents=True, exist_ok=False)
    present = {"state": state_file.is_file(), "database": database_file.is_file(),
               "database_raw": False, "sidecars": []}
    if present["state"]:
        shutil.copy2(state_file, recovery_directory / "state.json")
    if present["database"]:
        try:
            _snapshot_database(database_file, recovery_directory / "music_library.sqlite3")
        except (OSError, BackupError):
            present["database_raw"] = True
            shutil.copy2(database_file, recovery_directory / "music_library.raw.sqlite3")
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(database_file) + suffix)
                if sidecar.is_file():
                    target = recovery_directory / f"music_library.raw.sqlite3{suffix}"
                    shutil.copy2(sidecar, target)
                    present["sidecars"].append(suffix)
    (recovery_directory / "profile.json").write_text(
        json.dumps(present, separators=(",", ":")), encoding="utf-8")
    return recovery_directory


def _remove_database_sidecars(database_file: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(str(database_file) + suffix).unlink(missing_ok=True)


def restore_preserved_profile(recovery_directory: Path, state_file: Path,
                              database_file: Path) -> None:
    recovery_directory = Path(recovery_directory)
    state_file = Path(state_file)
    database_file = Path(database_file)
    try:
        present = json.loads((recovery_directory / "profile.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"O registro de recuperação não pode ser lido: {exc}") from exc

    state_source = recovery_directory / "state.json"
    if present.get("state") and state_source.is_file():
        state_file.parent.mkdir(parents=True, exist_ok=True)
        stage = state_file.with_name(state_file.name + ".restore.tmp")
        shutil.copy2(state_source, stage)
        os.replace(stage, state_file)
    elif not present.get("state"):
        state_file.unlink(missing_ok=True)

    if present.get("database"):
        if present.get("database_raw"):
            source = recovery_directory / "music_library.raw.sqlite3"
            database_file.parent.mkdir(parents=True, exist_ok=True)
            _remove_database_sidecars(database_file)
            stage = database_file.with_name(database_file.name + ".restore.tmp")
            shutil.copy2(source, stage)
            os.replace(stage, database_file)
            for suffix in present.get("sidecars", []):
                side_source = recovery_directory / f"music_library.raw.sqlite3{suffix}"
                if side_source.is_file():
                    shutil.copy2(side_source, Path(str(database_file) + suffix))
        else:
            source = recovery_directory / "music_library.sqlite3"
            database_file.parent.mkdir(parents=True, exist_ok=True)
            _remove_database_sidecars(database_file)
            stage = database_file.with_name(database_file.name + ".restore.tmp")
            shutil.copy2(source, stage)
            os.replace(stage, database_file)
    elif not present.get("database"):
        _remove_database_sidecars(database_file)
        database_file.unlink(missing_ok=True)


def import_backup(backup_file: Path, state_file: Path, database_file: Path,
                  recovery_root: Path) -> ImportResult:
    state_file = Path(state_file)
    database_file = Path(database_file)
    recovery_directory = preserve_profile(state_file, database_file, recovery_root)
    restored_database = False
    try:
        with _validated_backup(backup_file) as (state, database, _manifest):
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_stage = state_file.with_name(state_file.name + ".import.tmp")
            state_stage.write_bytes(state)
            database_stage = None
            if database is not None:
                database_file.parent.mkdir(parents=True, exist_ok=True)
                database_stage = database_file.with_name(database_file.name + ".import.tmp")
                _snapshot_database(database, database_stage)

            if database_stage is not None:
                _remove_database_sidecars(database_file)
                os.replace(database_stage, database_file)
                restored_database = True
            os.replace(state_stage, state_file)
        return ImportResult(recovery_directory, restored_database)
    except Exception as exc:
        try:
            restore_preserved_profile(recovery_directory, state_file, database_file)
        except Exception as rollback_error:
            raise BackupError(
                f"A importação falhou ({exc}) e a restauração automática também falhou "
                f"({rollback_error}). Os dados anteriores foram preservados em "
                f"{recovery_directory}.") from exc
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"A importação falhou; os dados atuais foram restaurados: {exc}") from exc
    finally:
        for staged in (state_file.with_name(state_file.name + ".import.tmp"),
                       database_file.with_name(database_file.name + ".import.tmp")):
            staged.unlink(missing_ok=True)


def _salvage_database(source: Path, target: Path) -> tuple[int, list[str]]:
    """Copy readable library rows into a newly created schema, skipping damaged rows."""
    MusicLibrary(target)
    tables = {
        "libraries": ("id", "name", "root", "enabled", "last_scan", "track_count"),
        "tracks": ("path", "library_id", "mtime_ns", "size", "title", "artist", "album",
                   "albumartist", "genre", "year", "composer", "duration"),
        "play_history": ("id", "played_at", "path", "title", "artist", "album",
                         "albumartist", "genre", "year", "composer", "duration"),
    }
    recovered = 0
    errors = []
    try:
        with closing(sqlite3.connect(str(source), timeout=3)) as old_db, \
                closing(sqlite3.connect(str(target), timeout=15)) as new_db:
            new_db.execute("PRAGMA foreign_keys=OFF")
            for table, allowed_columns in tables.items():
                try:
                    source_columns = {row[1] for row in old_db.execute(
                        f'PRAGMA table_info("{table}")')}
                    columns = [column for column in allowed_columns if column in source_columns]
                    if not columns:
                        continue
                    names = ",".join(f'"{column}"' for column in columns)
                    placeholders = ",".join("?" for _ in columns)
                    cursor = old_db.execute(f'SELECT {names} FROM "{table}"')
                    for row in cursor:
                        try:
                            new_db.execute(
                                f'INSERT OR IGNORE INTO "{table}" ({names}) VALUES ({placeholders})',
                                tuple(row))
                            if new_db.execute("SELECT changes()").fetchone()[0]:
                                recovered += 1
                        except sqlite3.Error:
                            errors.append(f"{table}: registro ignorado")
                except sqlite3.Error as exc:
                    errors.append(f"{table}: {exc}")
            new_db.commit()
            checkpoint = new_db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and checkpoint[0] != 0:
                raise sqlite3.DatabaseError("o SQLite não conseguiu concluir o checkpoint")
            journal_mode = new_db.execute("PRAGMA journal_mode=DELETE").fetchone()
            if not journal_mode or str(journal_mode[0]).lower() != "delete":
                raise sqlite3.DatabaseError("não foi possível consolidar o arquivo recuperado")
    except sqlite3.Error as exc:
        raise BackupError(f"Não foi possível gravar a recuperação do banco: {exc}") from exc
    _remove_database_sidecars(target)
    _sqlite_integrity(target)
    return recovered, errors


def repair_database(database_file: Path, backup_directory: Path,
                    recovery_root: Path, state_file: Path | None = None) -> RepairResult:
    database_file = Path(database_file)
    backup_directory = Path(backup_directory)
    recovery_root = Path(recovery_root)
    state_file = Path(state_file) if state_file is not None else database_file.with_name("state.json")
    if not database_file.exists():
        try:
            MusicLibrary(database_file)
        except (OSError, sqlite3.Error) as exc:
            raise BackupError(f"Não foi possível criar o banco de dados: {exc}") from exc
        return RepairResult(True, "O banco de dados foi criado novamente.")

    try:
        _sqlite_integrity(database_file)
    except BackupError as integrity_error:
        logger.warning("Falha na verificação do banco de dados %s: %s",
                       database_file, integrity_error)
    else:
        try:
            with closing(sqlite3.connect(str(database_file), timeout=15)) as db:
                db.execute("PRAGMA optimize")
        except sqlite3.Error:
            logger.exception("Falha não crítica ao otimizar o banco de dados")
        return RepairResult(False, "O banco de dados está íntegro; nenhuma reparação foi necessária.")

    recovery_directory = preserve_profile(state_file, database_file, recovery_root)
    staging = database_file.with_name(database_file.name + f".repair-{_timestamp()}")
    restored_from = ""
    try:
        for candidate in _backup_candidates(backup_directory):
            try:
                with _validated_backup(candidate) as (_state, backup_database, _manifest):
                    if backup_database is None:
                        continue
                    _snapshot_database(backup_database, staging)
                _sqlite_integrity(staging)
                restored_from = candidate.name
                break
            except (BackupError, OSError):
                logger.warning("Backup ignorado durante recuperação: %s", candidate,
                               exc_info=True)

        if not restored_from:
            try:
                recovered_rows, row_errors = _salvage_database(database_file, staging)
            except (BackupError, OSError) as exc:
                staging.unlink(missing_ok=True)
                raise BackupError(f"Não foi possível recuperar os dados legíveis: {exc}") from exc
        else:
            recovered_rows, row_errors = 0, []

        _remove_database_sidecars(database_file)
        os.replace(staging, database_file)
        _sqlite_integrity(database_file)
        if restored_from:
            message = f"Banco reparado usando o backup {restored_from}."
        elif recovered_rows:
            message = f"Banco reconstruído; {recovered_rows} registro(s) foram recuperados."
            if row_errors:
                message += f" {len(row_errors)} registro(s) ou tabela(s) danificado(s) foram ignorados."
        else:
            message = ("Um banco novo foi criado porque nenhum registro pôde ser recuperado. "
                       "A cópia danificada foi preservada para análise.")
        return RepairResult(True, message, recovery_directory)
    except Exception as exc:
        staging.unlink(missing_ok=True)
        try:
            restore_preserved_profile(recovery_directory,
                                      state_file, database_file)
        except Exception:
            logger.exception("Não foi possível restaurar o banco original após falha de reparação")
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"Não foi possível reparar o banco; os arquivos originais foram preservados: {exc}") from exc

