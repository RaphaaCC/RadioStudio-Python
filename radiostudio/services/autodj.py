"""Scheduling and candidate selection for AutoDJ."""

from __future__ import annotations

import math
import random
import time
from datetime import datetime
from pathlib import Path

from .audio_paths import audio_path_key


DAY_NAMES = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
FILTER_FIELDS = ("title", "artist", "album", "albumartist", "genre", "composer", "year", "path")


def active_program(programs, now: datetime | None = None):
    """Return the highest-priority matching weekly block, including overnight blocks."""
    now = now or datetime.now()
    minute = now.hour * 60 + now.minute
    matches = []
    for index, block in enumerate(programs or []):
        if not isinstance(block, dict):
            continue
        if not block.get("enabled", True):
            continue
        raw_days = block.get("days", [])
        if not isinstance(raw_days, (list, tuple, set)):
            continue
        days = {int(day) for day in raw_days
                if str(day).lstrip("-").isdigit() and 0 <= int(day) < 7}
        start = _minute(block.get("start", "00:00"))
        end = _minute(block.get("end", "00:00"))
        if start is None or end is None or start == end:
            continue
        if start < end:
            applies = now.weekday() in days and start <= minute < end
            duration = end - start
        elif minute >= start:
            applies = now.weekday() in days
            duration = (1440 - start) + end
        else:
            applies = minute < end and (now.weekday() - 1) % 7 in days
            duration = (1440 - start) + end
        if applies:
            try:
                priority = int(block.get("priority", 0))
            except (TypeError, ValueError, OverflowError):
                priority = 0
            matches.append((priority, -duration, -index, block))
    return max(matches, default=(0, 0, 0, None))[-1]


def program_signature(program):
    if not program:
        return ("fallback",)
    libraries = program.get("library_ids", [])
    if not isinstance(libraries, (list, tuple, set)):
        libraries = []
    filters = program.get("filters", {})
    if not isinstance(filters, dict):
        filters = {}
    return (str(program.get("id", "")), tuple(sorted(map(str, libraries))),
            tuple(sorted((str(key), repr(value)) for key, value in filters.items())))


def _artist_key(track) -> str:
    if not isinstance(track, dict):
        return ""
    return str(track.get("artist") or track.get("albumartist") or "").strip().casefold()


def reserve_track(history, track, *, now=None) -> None:
    """Reserve a queued track in a mutable history snapshot.

    AutoDJ fills several future rows in one pass. Marking each queued selection as
    recent makes artist/track cooldowns apply to the queue itself, not only to
    tracks that have already started playing.
    """
    if not isinstance(track, dict):
        return
    try:
        timestamp = float(now if now is not None else time.time())
        if not math.isfinite(timestamp):
            timestamp = time.time()
    except (TypeError, ValueError, OverflowError):
        timestamp = time.time()
    last_paths, last_artists = history
    path = audio_path_key(track.get("path", ""))
    if path:
        last_paths[path] = timestamp
    artist = _artist_key(track)
    if artist:
        last_artists[artist] = timestamp


def filter_tracks(tracks, filters):
    filters = filters if isinstance(filters, dict) else {}
    genres = _tokens(filters.get("genres") or filters.get("genre"))
    genre_set = {genre.casefold() for genre in genres}
    artists = {token.casefold() for token in _tokens(filters.get("artists") or filters.get("artist"))}
    include = [token.casefold() for token in _tokens(filters.get("include_keywords"))]
    exclude = [token.casefold() for token in _tokens(filters.get("exclude_keywords"))]
    raw_fields = filters.get("keyword_fields", FILTER_FIELDS)
    if not isinstance(raw_fields, (list, tuple, set)):
        raw_fields = FILTER_FIELDS
    fields = [field for field in raw_fields if isinstance(field, str) and field in FILTER_FIELDS]
    try:
        year_from = int(filters.get("year_from") or 0)
    except (TypeError, ValueError, OverflowError):
        year_from = 0
    try:
        year_to = int(filters.get("year_to") or 0)
    except (TypeError, ValueError, OverflowError):
        year_to = 0
    result = []
    for track in tracks:
        track_genres = {genre.casefold() for genre in _tokens(track.get("genre", ""))}
        if genre_set and not (genre_set & track_genres):
            continue
        artist = str(track.get("artist", "")).casefold()
        album_artist = str(track.get("albumartist", "")).casefold()
        if artists and artist not in artists and album_artist not in artists:
            continue
        searchable = "\n".join(str(track.get(field, "")) for field in fields).casefold()
        if include and not all(token in searchable for token in include):
            continue
        if any(token in searchable for token in exclude):
            continue
        try:
            track_year = int(track.get("year") or 0)
        except (TypeError, ValueError, OverflowError):
            track_year = 0
        # An unknown year cannot satisfy a user-requested year range. Previously
        # year_to by itself treated missing/invalid years as year zero and let
        # those tracks through.
        if (year_from or year_to) and track_year <= 0:
            continue
        if year_from and track_year < year_from:
            continue
        if year_to and track_year > year_to:
            continue
        result.append(track)
    return result


def choose_track(tracks, history, *, artist_cooldown_minutes=0,
                 track_cooldown_minutes=0, excluded_paths=(), now=None):
    """Choose a fair random track, relaxing cooldowns only when needed to avoid a stall."""
    try:
        now = float(now if now is not None else time.time())
        if not math.isfinite(now):
            now = time.time()
    except (TypeError, ValueError, OverflowError):
        now = time.time()
    excluded = {audio_path_key(path) for path in excluded_paths}
    candidates = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        path_key = audio_path_key(track.get("path", ""))
        if path_key not in excluded:
            # Path normalization and artist fallback were repeated in every
            # cooldown stage and again during sorting. Cache them for this pick;
            # large libraries can contain tens of thousands of indexed rows.
            candidates.append((track, path_key, _artist_key(track)))
    if not candidates and excluded:
        # A tiny library may have fewer tracks than the queue target. Repeats in a
        # queue are still avoided; the caller can try again after playback advances.
        return None, "Sem faixas inéditas na fila; aguardando a reprodução avançar."
    if not candidates:
        return None, "Nenhuma faixa disponível para os filtros e bibliotecas selecionados."

    last_paths, last_artists = history
    def cooldown(value):
        try:
            return max(0, int(value)) * 60
        except (TypeError, ValueError, OverflowError):
            return 0

    track_window = cooldown(track_cooldown_minutes)
    artist_window = cooldown(artist_cooldown_minutes)

    def recent(candidate, window):
        played = last_paths.get(candidate[1])
        return bool(window and played is not None and played > 0 and now - played < window)

    def artist_recent(candidate):
        artist = candidate[2]
        played = last_artists.get(artist) if artist else None
        return bool(artist_window and played is not None and played > 0
                    and now - played < artist_window)

    stages = [
        (False, False, ""),
        (True, False, "A janela de repetição de artista foi flexibilizada: a biblioteca tem poucas opções."),
        (False, True, "A janela de repetição da faixa foi flexibilizada: a biblioteca tem poucas opções."),
        (True, True, "As janelas de repetição foram flexibilizadas: a biblioteca tem poucas opções."),
    ]
    eligible, reason = [], ""
    for relax_artist, relax_track, note in stages:
        eligible = [candidate for candidate in candidates
                    if (relax_artist or not artist_recent(candidate))
                    and (relax_track or not recent(candidate, track_window))]
        if eligible:
            reason = note
            break
    if not eligible:
        eligible, reason = candidates, "Todas as faixas estão dentro da janela de repetição."

    # Rotate toward tracks least recently heard, with random choice among the oldest
    # portion so shuffled libraries remain lively and deterministic ties do not form.
    eligible.sort(key=lambda candidate: last_paths.get(candidate[1], 0))
    if len(eligible) > 1:
        pool_size = max(1, min(len(eligible), math.ceil(len(eligible) * 0.2)))
        pool = eligible[:pool_size]
    else:
        pool = eligible
    while pool:
        selected = random.choice(pool)
        chosen = selected[0]
        if Path(str(chosen.get("path", ""))).is_file():
            return chosen, reason
        pool.remove(selected)
        eligible.remove(selected)
        if not pool and eligible:
            pool_size = max(1, min(len(eligible), math.ceil(len(eligible) * 0.2)))
            pool = eligible[:pool_size]
    return None, "Os arquivos candidatos desapareceram; examine novamente as bibliotecas."


def _minute(value):
    try:
        hour, minute = str(value).split(":", 1)
        hour, minute = int(hour), int(minute)
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour * 60 + minute
    except (TypeError, ValueError):
        pass
    return None


def _tokens(value):
    if isinstance(value, (list, tuple, set)):
        return [str(token).strip() for token in value if str(token).strip()]
    return [token.strip() for token in str(value or "").replace(";", ",").split(",") if token.strip()]
