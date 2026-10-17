"""On-demand FFmpeg conversion for formats the Qt playback backend rejects."""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

import imageio_ffmpeg
from PySide6.QtCore import QObject, QProcess, QTimer


class AudioDecodeFallback(QObject):
    """Convert unsupported sources to temporary PCM WAV without blocking Qt."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs = {}
        self._temporary_files: set[str] = set()

    def convert(self, player, source: str, callback) -> bool:
        """Start a decode job; callback receives (source, wav_path, error)."""
        if player in self._jobs:
            return False
        source_path = Path(source)
        if not source_path.is_file():
            try:
                callback(source, "", "Arquivo não encontrado")
            except Exception:
                logging.getLogger("radiostudio").exception(
                    "Falha no callback de decodificação alternativa")
            return False

        output_path = Path(tempfile.gettempdir()) / f"radiostudio-decoded-{uuid.uuid4().hex}.wav"
        process = QProcess(self)
        job = {"process": process, "source": source, "output": str(output_path),
               "callback": callback}
        self._jobs[player] = job
        process.finished.connect(
            lambda code, _status, p=player, current=job: self._finish(p, current, code))
        process.errorOccurred.connect(
            lambda error, p=player, current=job: self._process_error(p, current, error))
        try:
            process.setProgram(imageio_ffmpeg.get_ffmpeg_exe())
            process.setArguments([
                "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-i", source, "-map", "0:a:0", "-vn", "-sn", "-dn",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", "-threads", "1",
                "-f", "wav", str(output_path),
            ])
            process.start()
        except Exception as exc:
            logging.getLogger("radiostudio").exception(
                "Falha ao preparar o decodificador FFmpeg para %s", source)
            self._finish(player, job, -1, str(exc))
            return False
        return True

    def cancel(self, player) -> None:
        job = self._jobs.pop(player, None)
        if not job:
            return
        process = job["process"]
        if process.state() != QProcess.NotRunning:
            process.kill()
        else:
            self._remove_file(job["output"])
            process.deleteLater()

    def is_pending(self, player) -> bool:
        return player in self._jobs

    def _process_error(self, player, job, error) -> None:
        if error == QProcess.FailedToStart:
            self._finish(player, job, -1, job["process"].errorString())

    def _finish(self, player, job, exit_code: int, error: str = "") -> None:
        if self._jobs.get(player) is not job:
            self._remove_file(job["output"])
            job["process"].deleteLater()
            return
        del self._jobs[player]
        output = Path(job["output"])
        if not error:
            error = job["process"].readAllStandardError().data().decode("utf-8", "replace").strip()
        try:
            output_is_valid = output.is_file() and output.stat().st_size > 44
        except OSError:
            output_is_valid = False
        callback = job["callback"]
        try:
            if exit_code == 0 and output_is_valid:
                self._temporary_files.add(str(output))
                callback(job["source"], str(output), "")
            else:
                self._remove_file(str(output))
                callback(job["source"], "", error or "FFmpeg não conseguiu decodificar o áudio")
        except Exception:
            logging.getLogger("radiostudio").exception(
                "Falha no callback de decodificação alternativa")
        finally:
            job["process"].deleteLater()

    def release(self, path: str, delay_ms: int = 3000) -> None:
        if path and path in self._temporary_files:
            QTimer.singleShot(max(0, int(delay_ms)), lambda target=path: self._remove_file(target))

    def _remove_file(self, path: str, attempt: int = 0) -> None:
        try:
            Path(path).unlink(missing_ok=True)
            self._temporary_files.discard(path)
        except OSError:
            if attempt < 5:
                QTimer.singleShot(2000, lambda target=path, n=attempt + 1:
                                  self._remove_file(target, n))
            else:
                logging.getLogger("radiostudio").warning(
                    "Não foi possível remover o áudio temporário: %s", path)

    def shutdown(self) -> None:
        jobs = tuple(self._jobs.items())
        self._jobs.clear()
        for _player, job in jobs:
            process = job["process"]
            if process.state() != QProcess.NotRunning:
                process.kill()
                process.waitForFinished(1000)
            self._remove_file(job["output"])
            process.deleteLater()
        for path in tuple(self._temporary_files):
            self._remove_file(path)
