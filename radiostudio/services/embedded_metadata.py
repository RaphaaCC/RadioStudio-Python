"""Safe, lossless embedded-tag updates using the bundled FFmpeg executable.

FFmpeg's stream-copy mode avoids decoding/re-encoding the audio. Containers and
tag fields vary, so this module accepts only formats that the application can
re-mux and verifies the staged file before atomically replacing the source.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path

import imageio_ffmpeg
from tinytag import TinyTag


# These are the containers verified with stream-copy remuxing and TinyTag
# duration checks. ADTS AAC does not reliably carry tags, and Ogg remuxing can
# corrupt granule-based duration, so those formats stay internal-only.
SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".flac", ".opus", ".wav"}
_TAG_NAMES = {
    "title": "title", "artist": "artist", "album": "album",
    "albumartist": "album_artist", "genre": "genre", "year": "date",
    "track": "track", "composer": "composer", "comments": "comment",
    "lyrics": "lyrics",
}
_TINYTAG_NAMES = {
    "title": "title", "artist": "artist", "album": "album",
    "albumartist": "albumartist", "genre": "genre", "year": "year",
    "track": "track", "composer": "composer", "comments": "comment",
    "lyrics": "lyrics",
}
_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "image/bmp": ".bmp",
}
_STANDARD_TAGS = {
    "title", "artist", "album", "album_artist", "albumartist", "genre", "date",
    "year", "track", "composer", "comment", "comments", "description", "lyrics",
    "unsyncedlyrics", "lyrics-eng",
}


class EmbeddedMetadataError(ValueError):
    """The requested embedded metadata update could not be safely completed."""


def read_embedded_tags(path):
    """Read the metadata dictionary through FFmpeg's metadata-only muxer."""
    source = Path(path)
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS or not source.is_file():
        return {}
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
               "-nostdin", "-i", str(source), "-map_metadata", "0", "-f", "ffmetadata", "-"]
    try:
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   check=False, timeout=30,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if completed.returncode != 0:
        return {}
    values = {}
    continuation = "\u0000"
    try:
        raw_tags = completed.stdout.decode("utf-8", "replace")
        raw_tags = raw_tags.replace("\\\r\n", continuation).replace("\\\n", continuation)
        lines = raw_tags.splitlines()
    except Exception:
        return {}
    for line in lines:
        if not line or line.startswith(";") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            continue
        value = []
        index = 0
        while index < len(raw_value):
            char = raw_value[index]
            if char == "\\" and index + 1 < len(raw_value):
                index += 1
                escaped = raw_value[index]
                value.append("\n" if escaped == "n" else escaped)
            else:
                value.append(char)
            index += 1
        values.setdefault(name, []).append("".join(value).replace(continuation, "\n"))
    return {name: items[0] if len(items) == 1 else items for name, items in values.items()}


def read_embedded_custom_tags(path):
    """Read non-standard, format-specific metadata keys."""
    values = read_embedded_tags(path)
    return {name: value for name, value in values.items()
            if name.casefold() not in _STANDARD_TAGS
            and not name.casefold().startswith("lyrics-")}


def write_embedded_metadata(path, fields, custom_tags=None, *, cover_data=None,
                            cover_mime="", clear_cover=False):
    """Apply a lossless tag update, preserving the original on every failure."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise EmbeddedMetadataError(
            "A gravação de tags não está disponível para este formato; use os metadados internos.")
    if not source.is_file():
        raise EmbeddedMetadataError("O arquivo da faixa não está disponível.")
    if not isinstance(fields, dict) or not isinstance(custom_tags or {}, dict):
        raise EmbeddedMetadataError("Os campos de metadados são inválidos.")
    if cover_data is not None and (not isinstance(cover_data, (bytes, bytearray))
                                   or not 0 < len(cover_data) <= 8 * 1024 * 1024):
        raise EmbeddedMetadataError("A imagem da capa está vazia ou excede 8 MB.")
    if cover_data is not None and clear_cover:
        raise EmbeddedMetadataError("Escolha uma capa ou a remoção da capa, não ambos.")

    staging = source.with_name(f".{source.stem}.radiostudio-{uuid.uuid4().hex}{suffix}")
    cover_path = None
    original_mode = None
    try:
        original_stat = source.stat()
        original_fingerprint = (original_stat.st_dev, original_stat.st_ino,
                                original_stat.st_size, original_stat.st_mtime_ns)
        original_mode = stat.S_IMODE(original_stat.st_mode)
        preserve_cover = cover_data is None and not clear_cover
        input_tag = TinyTag.get(str(source), tags=True, duration=True, image=preserve_cover)
        try:
            original_duration = float(input_tag.duration or 0)
        except (TypeError, ValueError, OverflowError):
            original_duration = 0.0
        effective_cover = bytes(cover_data) if cover_data is not None else None
        effective_cover_mime = str(cover_mime or "").lower()
        if preserve_cover:
            existing_cover = input_tag.images.front_cover or input_tag.images.any
            if existing_cover is not None and existing_cover.data:
                if len(existing_cover.data) > 8 * 1024 * 1024:
                    raise EmbeddedMetadataError("A capa existente excede o limite de 8 MB.")
                effective_cover = bytes(existing_cover.data)
                effective_cover_mime = str(getattr(existing_cover, "mime_type", "") or "").lower()

        command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                   "-nostdin", "-y", "-i", str(source)]
        if effective_cover is not None:
            extension = _MIME_EXTENSIONS.get(effective_cover_mime)
            if not extension:
                message = ("O formato da nova capa não é compatível." if cover_data is not None
                           else "A capa existente não pode ser preservada neste formato.")
                raise EmbeddedMetadataError(message)
            with tempfile.NamedTemporaryFile(prefix="radiostudio-cover-", suffix=extension,
                                             delete=False) as cover_file:
                cover_file.write(effective_cover)
                cover_path = Path(cover_file.name)
            command.extend(["-i", str(cover_path)])

        # Only audio streams are mapped, so the operation cannot accidentally
        # preserve an old attached picture when replacing or clearing a cover.
        command.extend(["-map", "0:a", "-map_metadata", "0", "-map_chapters", "0", "-c", "copy"])
        if effective_cover is not None:
            command.extend(["-map", "1:v:0", "-disposition:v:0", "attached_pic"])
        if suffix == ".mp3":
            command.extend(["-id3v2_version", "3", "-write_id3v1", "1"])
        for name, value in fields.items():
            tag_name = _TAG_NAMES.get(str(name))
            if tag_name is None:
                continue
            if isinstance(value, (list, tuple)):
                value = "; ".join(str(item) for item in value)
            command.extend(["-metadata", f"{tag_name}={str(value or '')[:16384]}"])
        for name, value in (custom_tags or {}).items():
            name = str(name).strip()
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
                if isinstance(value, (list, tuple)):
                    value = "; ".join(str(item) for item in value)
                command.extend(["-metadata", f"{name}={str(value or '')[:16384]}"])
        command.append(str(staging))

        try:
            completed = subprocess.run(command, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, check=False, timeout=3600,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EmbeddedMetadataError(f"O gravador de tags não concluiu: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()[-600:]
            raise EmbeddedMetadataError(detail or "O formato não aceitou a alteração de tags.")
        if not staging.is_file() or staging.stat().st_size <= 0:
            raise EmbeddedMetadataError("O arquivo preparado ficou vazio.")

        # Verify that FFmpeg produced a readable audio file with the same
        # duration before making the replacement visible.
        try:
            staged_tag = TinyTag.get(
                str(staging), tags=True, duration=True,
                image=bool(effective_cover is not None or clear_cover))
            staged_duration = float(staged_tag.duration or 0)
        except Exception as exc:
            raise EmbeddedMetadataError("A verificação do arquivo preparado falhou.") from exc
        if original_duration > 0 and staged_duration > 0:
            tolerance = max(2.0, original_duration * 0.01)
            if abs(original_duration - staged_duration) > tolerance:
                raise EmbeddedMetadataError("A duração do áudio mudou na verificação do arquivo preparado.")

        def tag_text(value):
            if isinstance(value, (list, tuple)):
                return "; ".join(str(item) for item in value)
            return str(value or "")

        staged_embedded_tags = read_embedded_tags(staging)
        embedded_by_name = {str(name).casefold(): value
                            for name, value in staged_embedded_tags.items()}
        for name, expected in fields.items():
            if name == "lyrics":
                actual = (embedded_by_name.get("lyrics")
                          or embedded_by_name.get("unsyncedlyrics")
                          or embedded_by_name.get("lyrics-eng"))
                if tag_text(actual).strip() != tag_text(expected).strip():
                    raise EmbeddedMetadataError(
                        "O arquivo preparado não preservou um ou mais campos solicitados.")
                continue
            attribute = _TINYTAG_NAMES.get(str(name))
            if attribute is None:
                continue
            actual = getattr(staged_tag, attribute, None)
            if tag_text(actual).strip() != tag_text(expected).strip():
                raise EmbeddedMetadataError(
                    "O arquivo preparado não preservou um ou mais campos solicitados.")

        if custom_tags:
            staged_custom = read_embedded_custom_tags(staging)
            custom_by_name = {str(name).casefold(): value
                              for name, value in staged_custom.items()}
            for name, expected in custom_tags.items():
                actual = custom_by_name.get(str(name).casefold())
                if tag_text(actual).strip() != tag_text(expected).strip():
                    raise EmbeddedMetadataError(
                        "As tags personalizadas não foram gravadas corretamente.")

        staged_image = staged_tag.images.front_cover or staged_tag.images.any
        if clear_cover and staged_image is not None:
            raise EmbeddedMetadataError("A capa não foi removida do arquivo.")
        if effective_cover is not None and staged_image is None:
            raise EmbeddedMetadataError("A capa não foi incorporada ao arquivo.")
        if original_mode is not None:
            os.chmod(staging, original_mode)
        current_stat = source.stat()
        current_fingerprint = (current_stat.st_dev, current_stat.st_ino,
                               current_stat.st_size, current_stat.st_mtime_ns)
        if current_fingerprint != original_fingerprint:
            raise EmbeddedMetadataError(
                "O arquivo original foi alterado durante a edição; nenhuma mudança foi aplicada.")
        os.replace(staging, source)
        return {"path": str(source), "size": source.stat().st_size}
    except EmbeddedMetadataError:
        raise
    except OSError as exc:
        raise EmbeddedMetadataError(f"Não foi possível atualizar as tags: {exc}") from exc
    finally:
        staging.unlink(missing_ok=True)
        if cover_path is not None:
            cover_path.unlink(missing_ok=True)
