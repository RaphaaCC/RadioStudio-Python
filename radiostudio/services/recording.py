"""Bounded, isolated writer for stereo program-bus recordings."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
import uuid
import wave
from datetime import datetime
from pathlib import Path

import imageio_ffmpeg

from .broadcast import CHANNELS, SAMPLE_RATE

LOG = logging.getLogger("radiostudio")
QUEUE_CAPACITY_FRAMES = 250  # Five seconds at the 20 ms mixer cadence.
SUPPORTED_FORMATS = {"mp3", "wav"}
SUPPORTED_BITRATES = {128, 160, 192, 256, 320}


def unique_recording_path(directory: str | Path, format_name: str,
                         now: datetime | None = None) -> Path:
    """Choose a timestamped filename without replacing an existing recording."""
    extension = str(format_name).lower().lstrip(".")
    if extension not in SUPPORTED_FORMATS:
        raise ValueError("Formato de gravação inválido")
    folder = Path(directory).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    for sequence in range(10000):
        suffix = "" if sequence == 0 else f"_{sequence:02d}"
        candidate = folder / f"RadioStudio_{stamp}{suffix}.{extension}"
        partial_candidate = candidate.with_name(
            candidate.stem + ".partial" + candidate.suffix)
        if partial_candidate.exists():
            continue
        try:
            descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            return candidate
        except FileExistsError:
            continue
    raise OSError("Não foi possível reservar um nome exclusivo para a gravação")


class RecordingSession:
    """One recording, with disk and FFmpeg work isolated from the audio mixer."""

    def __init__(self, directory: str | Path, format_name: str = "mp3",
                 bitrate: int = 192, *, ffmpeg_path: str | None = None):
        self.format = str(format_name).lower()
        if self.format not in SUPPORTED_FORMATS:
            raise ValueError("Formato de gravação inválido")
        try:
            self.bitrate = int(bitrate)
        except (TypeError, ValueError, OverflowError):
            self.bitrate = 192
        if self.bitrate not in SUPPORTED_BITRATES:
            raise ValueError("Taxa de bits de gravação inválida")
        self.path = unique_recording_path(directory, self.format)
        self.partial_path = self.path.with_name(self.path.stem + ".partial" + self.path.suffix)
        self.session_id = uuid.uuid4().hex
        self.queue: queue.Queue[bytes] = queue.Queue(maxsize=QUEUE_CAPACITY_FRAMES)
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._ready = threading.Event()
        self._writer_finished = threading.Event()
        self._paused_at: float | None = None
        self._paused_seconds = 0.0
        self._started_at: float | None = None
        self._written_bytes = 0
        self.state = "starting"
        self.error = ""
        self.ffmpeg_path = ffmpeg_path
        self._thread = threading.Thread(
            target=self._writer, name="RadioStudioRecorder", daemon=True)

    def start(self) -> "RecordingSession":
        self._thread.start()
        if not self._ready.wait(8):
            self._fail("O gravador não iniciou dentro do prazo")
            self._stop_requested.set()
            self._thread.join(timeout=2)
            raise RuntimeError(self.error)
        if self.error:
            self._thread.join(timeout=2)
            raise RuntimeError(self.error)
        return self

    @property
    def active(self) -> bool:
        with self._lock:
            return self.state in {"recording", "paused"}

    @property
    def busy(self) -> bool:
        return bool(self._thread.is_alive()) or self.state == "starting"

    @property
    def elapsed_seconds(self) -> float:
        with self._lock:
            if self._started_at is None:
                return 0.0
            end = self._paused_at if self._paused_at is not None else time.monotonic()
            return max(0.0, end - self._started_at - self._paused_seconds)

    def submit(self, pcm: bytes) -> bool:
        with self._lock:
            if self.state != "recording":
                return False
        if not pcm:
            return True
        try:
            self.queue.put_nowait(bytes(pcm))
            return True
        except queue.Full:
            self._fail("A fila da gravação encheu; a sessão foi interrompida para proteger o áudio ao vivo")
            return False

    def pause(self) -> bool:
        with self._lock:
            if self.state != "recording":
                return False
            self._paused_at = time.monotonic()
            self.state = "paused"
            return True

    def resume(self) -> bool:
        with self._lock:
            if self.state != "paused":
                return False
            self._paused_seconds += max(0.0, time.monotonic() - self._paused_at)
            self._paused_at = None
            self.state = "recording"
            return True

    def stop(self, timeout: float = 12.0) -> bool:
        with self._lock:
            if self.state == "stopped":
                return True
            if self.state == "paused" and self._paused_at is not None:
                self._paused_seconds += max(0.0, time.monotonic() - self._paused_at)
                self._paused_at = None
            self._stop_requested.set()
            if self.state != "error":
                self.state = "stopping"
        self._thread.join(timeout=max(0.0, float(timeout)))
        if self._thread.is_alive():
            self._fail("O gravador demorou para finalizar; o arquivo parcial foi preservado")
            return False
        with self._lock:
            return self.state == "stopped"

    def request_stop(self) -> bool:
        with self._lock:
            if self.state not in {"recording", "paused"}:
                return False
            if self.state == "paused" and self._paused_at is not None:
                self._paused_seconds += max(0.0, time.monotonic() - self._paused_at)
                self._paused_at = None
            self._stop_requested.set()
            self.state = "stopping"
            return True

    def _fail(self, message: str) -> None:
        with self._lock:
            if not self.error:
                self.error = str(message)
            if self.state != "stopped":
                self.state = "error"
            self._stop_requested.set()
        LOG.error("Gravação interrompida (%s): %s", self.path, message)

    def _ffmpeg_command(self, output_path: Path) -> list[str]:
        executable = self.ffmpeg_path or imageio_ffmpeg.get_ffmpeg_exe()
        return [executable, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                "-i", "pipe:0", "-vn", "-codec:a", "libmp3lame", "-b:a",
                f"{self.bitrate}k", "-f", "mp3", str(output_path)]

    def _writer(self) -> None:
        wav_file = None
        process = None
        finalized = False
        try:
            if self.format == "wav":
                wav_file = wave.open(str(self.partial_path), "wb")
                wav_file.setnchannels(CHANNELS)
                wav_file.setsampwidth(2)
                wav_file.setframerate(SAMPLE_RATE)
            else:
                process = subprocess.Popen(
                    self._ffmpeg_command(self.partial_path), stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if self._stop_requested.is_set():
                raise RuntimeError(self.error or "Inicialização da gravação cancelada")
            with self._lock:
                self._started_at = time.monotonic()
                self.state = "recording"
            self._ready.set()
            while True:
                try:
                    frame = self.queue.get(timeout=0.1)
                except queue.Empty:
                    if self._stop_requested.is_set():
                        break
                    if process is not None and process.poll() is not None:
                        raise RuntimeError("O FFmpeg encerrou inesperadamente")
                    continue
                if wav_file is not None:
                    wav_file.writeframesraw(frame)
                else:
                    if process.poll() is not None or process.stdin is None:
                        raise RuntimeError("O FFmpeg encerrou inesperadamente")
                    process.stdin.write(frame)
                with self._lock:
                    self._written_bytes += len(frame)
                self.queue.task_done()

            if wav_file is not None:
                wav_file.close()
                wav_file = None
            if process is not None:
                if process.stdin:
                    process.stdin.close()
                try:
                    _stdout, stderr = process.communicate(timeout=8)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    try:
                        _stdout, stderr = process.communicate(timeout=2)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError(
                            "O FFmpeg não respondeu ao finalizar a gravação") from exc
                    detail = stderr.decode("utf-8", "replace").strip()
                    raise RuntimeError(
                        detail or "O FFmpeg excedeu o prazo de finalização da gravação")
                return_code = process.returncode
                process = None
                if return_code:
                    message = stderr.decode("utf-8", "replace").strip()
                    raise RuntimeError(message or "O FFmpeg não finalizou o arquivo MP3")

            if self.error:
                raise RuntimeError(self.error)
            os.replace(self.partial_path, self.path)
            finalized = True
            with self._lock:
                self.state = "stopped"
        except Exception as exc:
            self._fail(str(exc) or exc.__class__.__name__)
            if wav_file is not None:
                try:
                    wav_file.close()
                except Exception:
                    LOG.exception("Falha ao finalizar o WAV parcial")
            if process is not None:
                try:
                    if process.stdin:
                        process.stdin.close()
                except OSError:
                    pass
                try:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                        process.wait(timeout=2)
                    except Exception:
                        pass
                if process.stderr:
                    try:
                        process.stderr.read()
                        process.stderr.close()
                    except Exception:
                        pass
            LOG.exception("Falha ao gravar %s", self.path)
        finally:
            self._ready.set()
            if not finalized:
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    LOG.warning("Não foi possível liberar a reserva de %s", self.path)
            self._writer_finished.set()


def validate_recording_schedule(schedule: dict) -> dict:
    """Validate and normalize one one-time or weekly recording schedule."""
    from datetime import date
    import re

    if not isinstance(schedule, dict):
        raise ValueError("Agenda inválida")
    kind = schedule.get("type")
    if kind not in {"once", "weekly"}:
        raise ValueError("Tipo de agenda inválido")
    start = str(schedule.get("start", ""))
    end = str(schedule.get("end", ""))
    pattern = r"(?:[01]\d|2[0-3]):[0-5]\d"
    if not re.fullmatch(pattern, start) or not re.fullmatch(pattern, end):
        raise ValueError("Horário de agenda inválido")
    if start == end:
        raise ValueError("O horário de início e término não pode ser igual")
    enabled_value = schedule.get("enabled", True)
    if isinstance(enabled_value, bool):
        enabled = enabled_value
    elif type(enabled_value) is int and enabled_value in (0, 1):
        enabled = bool(enabled_value)
    else:
        enabled = False
    normalized = {"id": str(schedule.get("id") or uuid.uuid4().hex),
                  "type": kind, "start": start, "end": end,
                  "enabled": enabled}
    if kind == "once":
        try:
            normalized["date"] = date.fromisoformat(str(schedule.get("date", ""))).isoformat()
        except ValueError as exc:
            raise ValueError("Data da agenda inválida") from exc
        normalized["weekdays"] = []
    else:
        weekdays = schedule.get("weekdays", [])
        if not isinstance(weekdays, (list, tuple, set)):
            raise ValueError("Dias da semana inválidos")
        valid_days = sorted({int(day) for day in weekdays if str(day).isdigit()
                             and 0 <= int(day) <= 6})
        if not valid_days:
            raise ValueError("Selecione pelo menos um dia da semana")
        normalized["weekdays"] = valid_days
        normalized["date"] = ""
    return normalized


def sanitize_recording_schedules(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    schedules = []
    used_ids = set()
    for item in value:
        try:
            schedule = validate_recording_schedule(item)
        except (TypeError, ValueError, OverflowError):
            LOG.warning("Uma agenda de gravação inválida foi ignorada")
            continue
        while schedule["id"] in used_ids:
            schedule["id"] = uuid.uuid4().hex
        used_ids.add(schedule["id"])
        schedules.append(schedule)
    return schedules
