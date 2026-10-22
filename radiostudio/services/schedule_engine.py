"""Date and recurrence helpers for the track scheduler."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any


EVENT_ACTIONS = {
    "play_track", "play_playlist", "play_cart", "playback_command",
    "broadcast_command",
}
PLAYBACK_COMMANDS = {"play", "pause", "resume", "stop", "next"}
BROADCAST_COMMANDS = {"start", "stop"}
PLAYBACK_MODES = {"at_time", "after_current"}
PLAYLIST_MODES = {"insert", "replace", "load_only"}
RECURRENCES = {"once", "weekly"}
_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")


def normalize_event(raw: Any) -> dict[str, Any] | None:
    """Validate and migrate an event, including the old ``day/path`` shape."""
    if not isinstance(raw, dict):
        return None
    event_time = str(raw.get("time", ""))
    if not _TIME_PATTERN.fullmatch(event_time):
        return None

    raw_action = raw.get("action")
    legacy = raw_action is None
    if legacy:
        action = "play_track"
    elif isinstance(raw_action, str) and raw_action in EVENT_ACTIONS:
        action = raw_action
    else:
        return None
    enabled_value = raw.get("enabled", True)
    if isinstance(enabled_value, bool):
        enabled = enabled_value
    elif type(enabled_value) is int and enabled_value in (0, 1):
        enabled = bool(enabled_value)
    else:
        return None
    raw_recurrence = raw.get("recurrence")
    if raw_recurrence is None:
        recurrence = "weekly"
    elif isinstance(raw_recurrence, str) and raw_recurrence in RECURRENCES:
        recurrence = raw_recurrence
    else:
        return None
    weekdays: list[int] = []
    if raw_recurrence is None:
        try:
            legacy_day = int(raw.get("day", 0))
        except (TypeError, ValueError, OverflowError):
            return None
        if not 0 <= legacy_day <= 7:
            return None
        weekdays = list(range(7)) if legacy_day == 0 else [legacy_day - 1]
    elif recurrence == "weekly":
        raw_weekdays = raw.get("weekdays", [])
        if not isinstance(raw_weekdays, list):
            return None
        for value in raw_weekdays:
            try:
                day = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if 0 <= day <= 6 and day not in weekdays:
                weekdays.append(day)
        weekdays.sort()
        if not weekdays:
            return None

    event_date = ""
    if recurrence == "once":
        event_date = str(raw.get("date", ""))
        try:
            date.fromisoformat(event_date)
        except ValueError:
            return None

    target = raw.get("target")
    if legacy:
        target = raw.get("path", "")
    if action in {"play_track", "play_playlist"}:
        if not isinstance(target, str) or not target.strip():
            return None
        target = target.strip()
    elif action == "play_cart":
        try:
            target = int(target)
        except (TypeError, ValueError, OverflowError):
            return None
        if not 0 <= target <= 24:
            return None
    elif action == "playback_command":
        if not isinstance(target, str) or target not in PLAYBACK_COMMANDS:
            return None
    elif action == "broadcast_command" and (
            not isinstance(target, str) or target not in BROADCAST_COMMANDS):
        return None

    playback_mode = raw.get("playback_mode", "at_time")
    if not isinstance(playback_mode, str) or playback_mode not in PLAYBACK_MODES:
        playback_mode = "at_time"
    playlist_mode = raw.get("playlist_mode", "insert")
    if not isinstance(playlist_mode, str) or playlist_mode not in PLAYLIST_MODES:
        playlist_mode = "insert"
    event_id = raw.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        event_id = uuid.uuid4().hex
    last_occurrence = raw.get("last_occurrence", "")
    if not isinstance(last_occurrence, str):
        last_occurrence = ""
    last_result = raw.get("last_result", "")
    if not isinstance(last_result, str) or last_result not in {
            "", "success", "late", "missed", "failed", "manual"}:
        last_result = ""

    result = {
        "id": event_id,
        "enabled": enabled,
        "recurrence": recurrence,
        "time": event_time,
        "date": event_date,
        "weekdays": weekdays,
        "action": action,
        "target": target,
        "playback_mode": playback_mode,
        "playlist_mode": playlist_mode,
        "last_occurrence": last_occurrence,
        "last_result": last_result,
    }
    return result


def occurrence_on(event: dict[str, Any], day: date) -> datetime | None:
    """Return this event's scheduled local datetime on ``day``, if any."""
    if event["recurrence"] == "once":
        try:
            event_day = date.fromisoformat(event["date"])
        except ValueError:
            return None
        if event_day != day:
            return None
    elif day.weekday() not in event["weekdays"]:
        return None
    hour, minute = map(int, event["time"].split(":"))
    return datetime.combine(day, time(hour, minute))


def latest_occurrence_at_or_before(event: dict[str, Any], moment: datetime) -> datetime | None:
    """Find the latest occurrence at or before a local wall-clock time."""
    if event["recurrence"] == "once":
        try:
            event_day = date.fromisoformat(event["date"])
        except ValueError:
            return None
        candidate = occurrence_on(event, event_day)
        return candidate if candidate is not None and candidate <= moment else None
    for offset in range(7):
        candidate = occurrence_on(event, moment.date() - timedelta(days=offset))
        if candidate is not None and candidate <= moment:
            return candidate
    return None


def next_occurrence(event: dict[str, Any], moment: datetime) -> datetime | None:
    """Find the next strictly-future occurrence in local time."""
    if event["recurrence"] == "once":
        try:
            candidate = occurrence_on(event, date.fromisoformat(event["date"]))
        except ValueError:
            return None
        return candidate if candidate is not None and candidate > moment else None
    for offset in range(8):
        candidate = occurrence_on(event, moment.date() + timedelta(days=offset))
        if candidate is not None and candidate > moment:
            return candidate
    return None


def occurrence_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")
