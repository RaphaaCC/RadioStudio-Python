"""Lightweight track analysis and automatic crossfade planning.

FFmpeg decodes only the first and last 20-second segments of each track, on a
worker thread. The result is used to cue short leading silence and adapt the
overlap to the outgoing tail and incoming loudness.
"""

from __future__ import annotations

import array
import logging
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import imageio_ffmpeg
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .track_metadata import read_track_metadata


ANALYSIS_SAMPLE_RATE = 8000
ANALYSIS_WINDOW_MS = 200
ANALYSIS_SEGMENT_SECONDS = 20
DEFAULT_MAX_MIX_MS = 8000
MAX_MIX_MS = 15000
MAX_INTRO_CUE_MS = 1200


@dataclass(frozen=True)
class TrackTransitionAnalysis:
    duration_ms: int
    intro_silence_ms: int = 0
    trailing_silence_ms: int = 0
    tail_fade_ms: int = 0
    intro_loudness_db: float = -18.0
    tail_loudness_db: float = -18.0


@dataclass(frozen=True)
class AutomaticTransitionPlan:
    duration_ms: int
    cue_ms: int
    fade_in_ms: int


def _pcm_levels(pcm: bytes, sample_rate: int, window_ms: int = ANALYSIS_WINDOW_MS) -> list[float]:
    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm) - (len(pcm) % samples.itemsize)])
    if sys.byteorder != "little":
        samples.byteswap()
    window_samples = max(1, round(sample_rate * window_ms / 1000))
    levels = []
    for start in range(0, len(samples), window_samples):
        segment = samples[start:start + window_samples]
        if not segment:
            continue
        mean_square = sum(sample * sample for sample in segment) / len(segment)
        rms = math.sqrt(mean_square) / 32768
        levels.append(20 * math.log10(max(rms, 1e-7)))
    return levels


def _mean_db(levels: list[float], fallback: float = -60.0) -> float:
    if not levels:
        return fallback
    mean_square = sum(10 ** (level / 10) for level in levels) / len(levels)
    return 10 * math.log10(max(mean_square, 1e-12))


def _silence_length(levels: list[float], threshold_db: float, *, from_end: bool) -> int:
    ordered = reversed(levels) if from_end else iter(levels)
    count = 0
    for level in ordered:
        if level > threshold_db:
            break
        count += 1
    return count * ANALYSIS_WINDOW_MS


def _tail_fade_length(levels: list[float]) -> int:
    """Return a tail fade duration only when its energy falls steadily."""
    # Smooth over one second to avoid treating individual beats as a fade.
    smooth = [_mean_db(levels[index:index + 5])
              for index in range(0, len(levels), 5)]
    if len(smooth) < 8:
        return 0
    first_candidate = max(0, len(smooth) - 16)
    for start in range(first_candidate, len(smooth) - 5):
        section = smooth[start:]
        drop = section[0] - _mean_db(section[-3:])
        if drop < 7:
            continue
        x_mean = (len(section) - 1) / 2
        y_mean = sum(section) / len(section)
        denominator = sum((index - x_mean) ** 2 for index in range(len(section)))
        slope = (sum((index - x_mean) * (level - y_mean)
                     for index, level in enumerate(section)) / denominator
                 if denominator else 0)
        falling_steps = sum(current <= previous + 2
                            for previous, current in zip(section, section[1:]))
        if slope <= -0.45 and falling_steps >= (len(section) - 1) * 0.6:
            return min(15000, (len(section) - 1) * 1000)
    return 0


def analyze_pcm_segments(intro_pcm: bytes, tail_pcm: bytes | None,
                         duration_ms: int) -> TrackTransitionAnalysis:
    """Analyze already-decoded mono PCM; exposed separately for deterministic tests."""
    intro = _pcm_levels(intro_pcm, ANALYSIS_SAMPLE_RATE)
    tail = _pcm_levels(tail_pcm or b"", ANALYSIS_SAMPLE_RATE)
    peak = max(intro + tail, default=-60.0)
    # Combine a fixed floor with a relative gate so quiet recordings still work.
    gate = max(-52.0, peak - 38.0)
    intro_silence = _silence_length(intro, gate, from_end=False)
    tail_silence = _silence_length(tail, gate, from_end=True)
    active_intro = intro[min(len(intro), intro_silence // ANALYSIS_WINDOW_MS):]
    intro_loudness = _mean_db(active_intro[:15], -18.0)
    tail_loudness = _mean_db(tail[-15:], -18.0)
    fade_length = _tail_fade_length(tail[:-max(1, tail_silence // ANALYSIS_WINDOW_MS)]
                                    if tail_silence else tail)
    return TrackTransitionAnalysis(
        duration_ms=max(0, int(duration_ms)),
        intro_silence_ms=min(10000, intro_silence),
        trailing_silence_ms=min(20000, tail_silence),
        tail_fade_ms=fade_length,
        intro_loudness_db=intro_loudness,
        tail_loudness_db=tail_loudness,
    )


def _decode_segment(ffmpeg: str, path: str, seconds: int, *, from_end: bool = False) -> bytes:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1"]
    if from_end:
        command.extend(["-sseof", f"-{seconds}"])
    command.extend(["-i", path, "-t", str(seconds), "-vn", "-ac", "1",
                    "-ar", str(ANALYSIS_SAMPLE_RATE), "-f", "s16le", "pipe:1"])
    options = {"capture_output": True, "timeout": 20, "check": False}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, **options)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("FFmpeg não conseguiu analisar o áudio")
    return result.stdout


@lru_cache(maxsize=256)
def _analyze_cached(path: str, modified_ns: int, size: int, duration_ms: int):
    del modified_ns, size  # They are part of the cache key and invalidate changed files.
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    intro = _decode_segment(ffmpeg, path, ANALYSIS_SEGMENT_SECONDS)
    tail = (_decode_segment(ffmpeg, path, min(ANALYSIS_SEGMENT_SECONDS,
                                               max(1, math.ceil(duration_ms / 1000))),
                            from_end=True)
            if duration_ms > 0 else None)
    return analyze_pcm_segments(intro, tail, duration_ms)


def analyze_track_transition(path: str) -> TrackTransitionAnalysis | None:
    """Analyze a track; failures are returned as no result so playback can fall back."""
    try:
        file_path = Path(path)
        stat = file_path.stat()
        duration_ms = max(0, round(read_track_metadata(str(file_path)).duration * 1000))
        return _analyze_cached(str(file_path), stat.st_mtime_ns, stat.st_size, duration_ms)
    except Exception:
        logging.getLogger("radiostudio").debug(
            "Análise automática indisponível para %s", path, exc_info=True)
        return None


def plan_automatic_transition(outgoing: TrackTransitionAnalysis | None,
                              incoming: TrackTransitionAnalysis | None,
                              maximum_mix_ms: int = 0) -> AutomaticTransitionPlan:
    """Choose a conservative overlap from the outgoing ending and incoming level."""
    try:
        configured_max = int(maximum_mix_ms)
    except (TypeError, ValueError, OverflowError):
        configured_max = 0
    cap = min(MAX_MIX_MS, max(500, configured_max or DEFAULT_MAX_MIX_MS))

    if outgoing and outgoing.tail_fade_ms >= 1600:
        desired = max(2000, outgoing.tail_fade_ms)
    elif outgoing and outgoing.trailing_silence_ms >= 400:
        desired = max(1500, min(cap, outgoing.trailing_silence_ms + 900))
    else:
        incoming_level = incoming.intro_loudness_db if incoming else -18.0
        if incoming_level >= -9:
            desired = 2500
        elif incoming_level <= -25:
            desired = 6000
        else:
            desired = 4200
        if (outgoing and incoming and outgoing.tail_loudness_db >= -10
                and incoming.intro_loudness_db >= -10):
            desired = min(desired, 3000)

    duration = min(cap, max(500, int(desired)))
    leading_silence = incoming.intro_silence_ms if incoming else 0
    cue = min(MAX_INTRO_CUE_MS, max(0, leading_silence - 150))
    if incoming:
        if incoming.intro_loudness_db >= -9:
            fade_in = 700
        elif incoming.intro_loudness_db <= -25:
            fade_in = 1300
        else:
            fade_in = 950
    else:
        fade_in = 900
    return AutomaticTransitionPlan(duration_ms=duration, cue_ms=cue, fade_in_ms=fade_in)


def bounded_transition_duration_ms(requested_ms: int, outgoing_duration_ms: int = 0,
                                   outgoing_remaining_ms: int = 0,
                                   incoming_duration_ms: int = 0) -> int:
    """Keep an overlap within both tracks and the audible remainder of the current one."""
    try:
        duration = max(1, int(requested_ms))
        caps = (int(outgoing_duration_ms) // 2,
                int(outgoing_remaining_ms),
                int(incoming_duration_ms) // 2)
    except (TypeError, ValueError, OverflowError):
        return 1
    for cap in caps:
        if cap > 0:
            duration = min(duration, cap)
    return max(1, duration)


def equal_power_gains(progress: float) -> tuple[float, float]:
    """Return outgoing/incoming gains for a constant-power crossfade."""
    phase = min(1.0, max(0.0, float(progress))) * math.pi / 2
    return math.cos(phase), math.sin(phase)


class _AnalysisSignals(QObject):
    finished = Signal(str, object)


class _AnalysisTask(QRunnable):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.signals = _AnalysisSignals()

    @Slot()
    def run(self):
        self.signals.finished.emit(self.path, analyze_track_transition(self.path))


class TransitionAnalyzer(QObject):
    """Deduplicates asynchronous analysis requests and emits results on the UI thread."""

    result_ready = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending: set[str] = set()
        # Keep the Python wrappers (and their signal objects) alive until the
        # queued completion slot runs. Fast worker completion can otherwise
        # race wrapper collection after QThreadPool.start() returns.
        self._jobs: dict[str, _AnalysisTask] = {}

    def request(self, path: str) -> None:
        if not path or path in self._pending:
            return
        self._pending.add(path)
        task = _AnalysisTask(path)
        task.signals.finished.connect(self._complete)
        self._jobs[path] = task
        QThreadPool.globalInstance().start(task)

    @Slot(str, object)
    def _complete(self, path: str, analysis) -> None:
        self._pending.discard(path)
        self._jobs.pop(path, None)
        self.result_ready.emit(path, analysis)
