"""Read audio tags and format the public now-playing title."""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tinytag import TinyTag


DEFAULT_STATION_METADATA = {
    "name": "RadioStudio Python",
    "description": "",
    "genre": "Other",
    "url": "",
    "title_format": "%artist - %title",
}

PLACEHOLDERS = (
    "title", "artist", "album", "albumartist", "genre", "year", "track",
    "composer", "filename", "duration", "station",
)
TOKEN_PATTERN = re.compile(r"%%|%([A-Za-z_]+)")


@dataclass(frozen=True)
class TrackMetadata:
    path: str
    title: str
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    genre: str = ""
    year: str = ""
    track: str = ""
    composer: str = ""
    duration: float = 0.0

    @property
    def display_title(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


def _value(raw) -> str:
    return str(raw).strip() if raw is not None else ""


@lru_cache(maxsize=4096)
def _read_cached(path: str, modified_ns: int, size: int) -> TrackMetadata:
    filename = Path(path).stem
    try:
        tag = TinyTag.get(path)
        duration = float(tag.duration or 0)
        if not math.isfinite(duration) or duration < 0:
            duration = 0.0
        return TrackMetadata(
            path=path,
            title=_value(tag.title) or filename,
            artist=_value(tag.artist),
            album=_value(tag.album),
            albumartist=_value(tag.albumartist),
            genre=_value(tag.genre),
            year=_value(tag.year),
            track=_value(tag.track),
            composer=_value(tag.composer),
            duration=duration,
        )
    except Exception:
        # A damaged or unsupported tag must not prevent the playlist/library
        # from loading; the filename remains a usable title fallback.
        return TrackMetadata(path=path, title=filename)


def read_track_metadata(path: str) -> TrackMetadata:
    file = Path(path)
    try:
        stat = file.stat()
    except (OSError, ValueError, TypeError):
        return TrackMetadata(path=str(file), title=file.stem)
    return _read_cached(str(file), stat.st_mtime_ns, stat.st_size)


def unknown_placeholders(pattern: str) -> list[str]:
    return sorted({match.group(1).lower() for match in TOKEN_PATTERN.finditer(pattern)
                   if match.group(1) and match.group(1).lower() not in PLACEHOLDERS})


def format_broadcast_title(pattern: str, track: TrackMetadata,
                           station: dict) -> str:
    values = {
        "title": track.title, "artist": track.artist, "album": track.album,
        "albumartist": track.albumartist, "genre": track.genre, "year": track.year,
        "track": track.track, "composer": track.composer,
        "filename": Path(track.path).stem,
        "duration": f"{int(track.duration) // 60:02}:{int(track.duration) % 60:02}"
        if track.duration else "",
        "station": station.get("name", ""),
    }

    def replace(match):
        if match.group(0) == "%%":
            return "%"
        return values.get(match.group(1).lower(), match.group(0))

    rendered = TOKEN_PATTERN.sub(replace, pattern).strip()
    rendered = re.sub(r"\(\s*\)|\[\s*\]", "", rendered)
    rendered = re.sub(r"\s*[-–—|•]\s*[-–—|•]\s*", " - ", rendered)
    rendered = re.sub(r"\s{2,}", " ", rendered)
    return rendered.strip(" \t-–—|•") or track.display_title
