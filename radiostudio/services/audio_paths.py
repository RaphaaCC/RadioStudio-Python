"""Path identity helpers for local audio files."""

from __future__ import annotations

import os
import re


_WINDOWS_SHORT_PATH_COMPONENT = re.compile(r"~\d+(?:\.[^\\/]*)?(?:[\\/]|$)")


def audio_path_key(path) -> str:
    """Return a stable identity for a local path on the current OS."""
    if not path:
        return ""
    try:
        raw_path = os.fspath(path)
    except (TypeError, ValueError):
        return str(path)
    value = raw_path
    # Path.resolve() on indexed files expands Windows 8.3 aliases, while Qt
    # and playlists may retain names such as RUNNER~1. Resolve only paths that
    # can contain such aliases; ordinary library rows stay on the cheap lexical
    # normalization path during large catalog and history queries.
    if (os.name == "nt" and isinstance(raw_path, str)
            and _WINDOWS_SHORT_PATH_COMPONENT.search(raw_path)):
        try:
            value = os.path.realpath(raw_path)
        except OSError:
            value = raw_path
    return os.path.normcase(os.path.normpath(value))


def same_audio_path(left, right) -> bool:
    """Compare local paths after applying the host OS path normalization.

    Qt's QUrl.toLocalFile() returns forward slashes on Windows even when the
    playlist stores the same path with backslashes. Raw string comparisons can
    then miss playback end events and repeatedly restart waveform analysis.
    """
    return audio_path_key(left) == audio_path_key(right)
