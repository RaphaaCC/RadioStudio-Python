"""Compact clock and on-air waveform for the main window."""

from __future__ import annotations

from ..runtime_safety import guarded

from array import array
from datetime import datetime
from math import cos, floor, log10, pi, sin, sqrt
from pathlib import Path
import sys
from collections import deque

import imageio_ffmpeg
from PySide6.QtCore import QProcess, Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


WEEKDAYS = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
MONTHS = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
MAX_WAVE_BYTES = 32 * 1024 * 1024
WAVEFORM_STYLES = (
    ("Barras clássicas", "bars"),
    ("Linha suave", "line"),
    ("Área preenchida", "area"),
    ("Pontos", "dots"),
    ("Barras verticais", "vertical"),
    ("Espelhado", "mirror"),
    ("Onda reativa", "reactive_wave"),
    ("Espectro de frequências", "spectrum"),
    ("Espectro espelhado", "spectrum_mirror"),
    ("Curva de frequências", "spectrum_line"),
)


def minutes_seconds(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02}:{seconds % 60:02}"


class StudioClock(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedWidth(174)
        self.setMinimumHeight(105)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(1)
        self.date_label = QLabel()
        self.date_label.setAlignment(Qt.AlignCenter)
        self.date_label.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        self.time_label = QLabel()
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setFont(QFont("Segoe UI", 21, QFont.Bold))
        self.hour_label = QLabel()
        self.hour_label.setAlignment(Qt.AlignCenter)
        self.hour_label.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        layout.addWidget(self.date_label)
        layout.addWidget(self.time_label, 1)
        layout.addWidget(self.hour_label)
        self.update_time()

    @guarded
    def update_time(self):
        now = datetime.now()
        self.date_label.setText(f"{WEEKDAYS[now.weekday()]} {now.day:02} {MONTHS[now.month - 1]} {now.year}")
        self.time_label.setText(now.strftime("%H:%M:%S"))
        remaining = 3600 - now.minute * 60 - now.second
        self.hour_label.setText(f"Próxima hora em {remaining // 60:02}:{remaining % 60:02}")
        dark = self.palette().color(QPalette.Window).lightness() < 128
        self.hour_label.setStyleSheet(f"color: {'#70ddb0' if dark else '#08784f'}")


class Waveform(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.levels: list[float] = []
        self.progress = 0.0
        self.style = "bars"
        self.live_wave: list[float] = []
        self.live_spectrum = [0.0] * 48
        self._samples = deque(maxlen=4096)
        self._samples_since_spectrum = 0
        self.setMinimumHeight(32)

    def set_levels(self, levels: list[float]):
        self.levels = levels
        self.update()

    def reset_live(self):
        self.live_wave = []
        self.live_spectrum = [0.0] * len(self.live_spectrum)
        self._samples.clear()
        self._samples_since_spectrum = 0
        self.update()

    @guarded
    def set_progress(self, progress: float):
        self.progress = max(0.0, min(1.0, progress))
        self.update()

    def set_style(self, style: str):
        style = style if style in {value for _, value in WAVEFORM_STYLES} else "bars"
        if style != self.style:
            self._samples.clear()
            self.live_wave = []
            self.live_spectrum = [0.0] * len(self.live_spectrum)
        self.style = style
        self._samples_since_spectrum = 0
        self.update()

    @guarded
    def feed_audio_buffer(self, data: bytes, channels: int, sample_rate: int):
        if self.style not in {"reactive_wave", "spectrum", "spectrum_mirror", "spectrum_line"}:
            return
        if channels < 1 or sample_rate < 1:
            return
        samples = array("h")
        samples.frombytes(data[:len(data) // 2 * 2])
        if sys.byteorder != "little":
            samples.byteswap()
        frames = len(samples) // max(1, channels)
        if not frames:
            return
        frame_samples = []
        for frame in range(frames):
            start = frame * channels
            frame_samples.append(sum(samples[start + channel] for channel in range(channels)) /
                                 (channels * 32768))
        self._samples.extend(frame_samples)

        if self.style == "reactive_wave":
            recent = list(self._samples)[-2048:]
            count = 100
            self.live_wave = []
            for index in range(count):
                start = index * len(recent) // count
                end = max(start + 1, (index + 1) * len(recent) // count)
                selected = recent[start:end]
                peak = max((abs(value) for value in selected), default=0.0)
                self.live_wave.append(min(1.0, peak * 1.6))
            self.update()
        elif self.style in ("spectrum", "spectrum_mirror", "spectrum_line"):
            self._samples_since_spectrum += frames
            if len(self._samples) >= 2048 and self._samples_since_spectrum >= 2048:
                self._samples_since_spectrum = 0
                self._update_spectrum(list(self._samples)[-2048:], sample_rate)

    @guarded
    def _update_spectrum(self, samples: list[float], sample_rate: int):
        size = len(samples)
        real = [sample * (0.5 - 0.5 * cos(2 * pi * index / (size - 1)))
                for index, sample in enumerate(samples)]
        imaginary = [0.0] * size

        j = 0
        for i in range(1, size):
            bit = size >> 1
            while j & bit:
                j ^= bit
                bit >>= 1
            j ^= bit
            if i < j:
                real[i], real[j] = real[j], real[i]
                imaginary[i], imaginary[j] = imaginary[j], imaginary[i]

        length = 2
        while length <= size:
            angle = -2 * pi / length
            step_real, step_imaginary = cos(angle), sin(angle)
            half = length // 2
            for start in range(0, size, length):
                twiddle_real, twiddle_imaginary = 1.0, 0.0
                for offset in range(half):
                    even = start + offset
                    odd = even + half
                    odd_real = (twiddle_real * real[odd]
                                - twiddle_imaginary * imaginary[odd])
                    odd_imaginary = (twiddle_real * imaginary[odd]
                                     + twiddle_imaginary * real[odd])
                    real[odd] = real[even] - odd_real
                    imaginary[odd] = imaginary[even] - odd_imaginary
                    real[even] += odd_real
                    imaginary[even] += odd_imaginary
                    twiddle_real, twiddle_imaginary = (
                        twiddle_real * step_real - twiddle_imaginary * step_imaginary,
                        twiddle_real * step_imaginary + twiddle_imaginary * step_real)
            length *= 2

        nyquist = size // 2
        min_frequency = 35.0
        max_frequency = min(16000.0, sample_rate / 2)
        spectrum = []
        for band in range(len(self.live_spectrum)):
            low_frequency = min_frequency * (max_frequency / min_frequency) ** (
                band / len(self.live_spectrum))
            high_frequency = min_frequency * (max_frequency / min_frequency) ** (
                (band + 1) / len(self.live_spectrum))
            low = max(1, floor(low_frequency * size / sample_rate))
            high = min(nyquist, max(low + 1, floor(high_frequency * size / sample_rate)))
            magnitudes = [sqrt(real[index] ** 2 + imaginary[index] ** 2) * 2 / size
                          for index in range(low, high)]
            magnitude = max(magnitudes, default=0.0)
            level = max(0.0, min(1.0, (20 * log10(max(magnitude, 1e-5)) + 55) / 55))
            spectrum.append(max(level, self.live_spectrum[band] * 0.78))
        self.live_spectrum = spectrum
        self.update()

    @guarded
    def paintEvent(self, event):
        painter = QPainter(self)
        background = self.palette().color(QPalette.Base)
        painter.fillRect(self.rect(), background)
        dark = background.lightness() < 128
        line = QColor("#5a6972" if dark else "#b4c4c8")
        played = QColor("#53d6a2" if dark else "#07885c")
        upcoming = QColor("#7198ad" if dark else "#8daebc")
        width, height = self.width(), self.height()
        center = height // 2
        painter.setPen(line)
        painter.drawLine(0, center, width, center)
        if self.style == "reactive_wave":
            self._paint_reactive_wave(painter, width, height, dark)
        elif self.style in ("spectrum", "spectrum_mirror", "spectrum_line"):
            self._paint_spectrum(painter, width, height, dark)
        elif self.levels:
            step = width / len(self.levels)
            if self.style in ("line", "area"):
                wave = QPainterPath()
                wave.moveTo(0, center - self.levels[0] * (center - 2))
                for index, level in enumerate(self.levels):
                    wave.lineTo(index * step, center - level * (center - 2))
                wave.lineTo(width, center - self.levels[-1] * (center - 2))
                for index in range(len(self.levels) - 1, -1, -1):
                    wave.lineTo(index * step, center + self.levels[index] * (center - 2))
                wave.lineTo(0, center + self.levels[0] * (center - 2))
                wave.closeSubpath()
                painter.setRenderHint(QPainter.Antialiasing)
                if self.style == "area":
                    upcoming_fill = QColor(upcoming)
                    upcoming_fill.setAlpha(150)
                    played_fill = QColor(played)
                    played_fill.setAlpha(190)
                    painter.fillPath(wave, upcoming_fill)
                    painter.save()
                    painter.setClipRect(0, 0, round(width * self.progress), height)
                    painter.fillPath(wave, played_fill)
                    painter.restore()
                else:
                    painter.setPen(QPen(upcoming, 1.7))
                    painter.drawPath(wave)
                    painter.save()
                    painter.setClipRect(0, 0, round(width * self.progress), height)
                    painter.setPen(QPen(played, 1.9))
                    painter.drawPath(wave)
                    painter.restore()
            else:
                painter.setRenderHint(QPainter.Antialiasing, self.style == "dots")
                for index, level in enumerate(self.levels):
                    x = round(index * step)
                    color = (played if (index + 0.5) / len(self.levels) <= self.progress
                             else upcoming)
                    bar_height = max(1, round(level * (center - 2)))
                    if self.style == "dots":
                        radius = max(1.5, min(3.0, step / 2))
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(color)
                        painter.drawEllipse(QRectF(x + step / 2 - radius,
                                                   center - bar_height - radius,
                                                   radius * 2, radius * 2))
                        painter.drawEllipse(QRectF(x + step / 2 - radius,
                                                   center + bar_height - radius,
                                                   radius * 2, radius * 2))
                    elif self.style == "vertical":
                        painter.fillRect(QRectF(x, height - bar_height,
                                                max(1, step - 1), bar_height), color)
                    elif self.style == "mirror":
                        lane_center = height / 2
                        lane_height = height / 4 - 1
                        half_bar = max(1, round(level * lane_height))
                        painter.fillRect(QRectF(x, lane_center / 2 - half_bar,
                                                max(1, step - 1), half_bar), color)
                        painter.fillRect(QRectF(x, lane_center * 1.5,
                                                max(1, step - 1), half_bar), color)
                    else:
                        painter.fillRect(QRectF(x, center - bar_height,
                                                max(1, step - 1), bar_height * 2), color)
        if self.progress > 0:
            painter.setPen(played)
            x = min(width - 1, round(self.progress * width))
            painter.drawLine(x, 0, x, height)

    def _spectrum_color(self, index: int, dark: bool):
        hue = int(300 - index / max(1, len(self.live_spectrum) - 1) * 110)
        return QColor.fromHsv(hue, 220, 235 if dark else 175)

    def _paint_reactive_wave(self, painter: QPainter, width: int, height: int, dark: bool):
        if not self.live_wave:
            return
        accent = QColor("#e9fff5" if dark else "#08784f")
        gap = width / len(self.live_wave)
        center = height / 2
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(accent, max(2.0, min(4.0, gap * 0.62)),
                            Qt.SolidLine, Qt.RoundCap))
        for index, level in enumerate(self.live_wave):
            extent = max(1.5, level * (center - 2))
            x = round((index + 0.5) * gap)
            painter.drawLine(x, round(center - extent), x, round(center + extent))

    def _paint_spectrum(self, painter: QPainter, width: int, height: int, dark: bool):
        if not any(self.live_spectrum):
            return
        gap = width / len(self.live_spectrum)
        if self.style == "spectrum_line":
            path = QPainterPath()
            for index, level in enumerate(self.live_spectrum):
                point = (index + 0.5) * gap
                y = height - max(2, level * (height - 3))
                if index == 0:
                    path.moveTo(point, y)
                else:
                    path.lineTo(point, y)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor("#68ddb0" if dark else "#08784f"), 2.0))
            painter.drawPath(path)
            return

        for index, level in enumerate(self.live_spectrum):
            bar_height = max(1, round(level * (height - 2)))
            x = index * gap
            color = self._spectrum_color(index, dark)
            if self.style == "spectrum_mirror":
                center = height / 2
                extent = max(1, round(level * (center - 1)))
                painter.fillRect(QRectF(x, center - extent, max(1, gap - 1), extent), color)
                painter.fillRect(QRectF(x, center, max(1, gap - 1), extent), color)
            else:
                painter.fillRect(QRectF(x, height - bar_height,
                                        max(1, gap - 1), bar_height), color)


class OnAirPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(105)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 4, 9, 4)
        layout.setSpacing(1)
        header = QHBoxLayout()
        self.on_air_heading = QLabel("NO AR")
        self.on_air_heading.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.title = QLabel("Nenhuma faixa em reprodução")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
        header.addWidget(self.on_air_heading)
        header.addWidget(self.title, 1)
        layout.addLayout(header)
        self.waveform = Waveform()
        layout.addWidget(self.waveform, 1)
        times = QHBoxLayout()
        self.elapsed = QLabel("00:00")
        self.remaining = QLabel("−00:00")
        self.remaining.setAlignment(Qt.AlignRight)
        times.addWidget(self.elapsed)
        times.addWidget(self.remaining, 1)
        layout.addLayout(times)
        self._process: QProcess | None = None
        self._wave_data = bytearray()
        self._wave_overflow = False
        self._broadcast_active = False
        self._blink_red = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._toggle_on_air_blink)

    @guarded
    def set_broadcast_active(self, active: bool):
        if active == self._broadcast_active:
            return
        self._broadcast_active = active
        self._blink_red = active
        if active:
            self._blink_timer.start()
        else:
            self._blink_timer.stop()
        self._update_on_air_color()

    @guarded
    def _toggle_on_air_blink(self):
        self._blink_red = not self._blink_red
        self._update_on_air_color()

    def _update_on_air_color(self):
        if self._blink_red:
            dark = self.palette().color(QPalette.Window).lightness() < 128
            self.on_air_heading.setStyleSheet(
                f"color: {'#ff5b62' if dark else '#c5161d'}")
        else:
            self.on_air_heading.setStyleSheet("")

    @guarded
    def show_track(self, title: str, path: str):
        self._cancel_analysis()
        self.waveform.reset_live()
        self.title.setText(title)
        self.title.setToolTip(title)
        self.waveform.set_levels([])
        self.set_progress(0, 0)
        try:
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except (OSError, RuntimeError):
            return
        process = QProcess(self)
        process.setStandardErrorFile(QProcess.nullDevice())
        process.readyReadStandardOutput.connect(lambda: self._read_wave_data(process))
        process.finished.connect(lambda code, _status: self._finish_wave(process, code))
        self._process = process
        self._wave_data = bytearray()
        self._wave_overflow = False
        process.start(ffmpeg, ["-hide_banner", "-loglevel", "error", "-i", str(Path(path)),
                               "-vn", "-ac", "1", "-ar", "2000", "-f", "s16le", "-"])

    @guarded
    def clear_track(self):
        self._cancel_analysis()
        self.waveform.reset_live()
        self.title.setText("Nenhuma faixa em reprodução")
        self.title.setToolTip("")
        self.waveform.set_levels([])
        self.set_progress(0, 0)

    @guarded
    def set_progress(self, position_ms: int, duration_ms: int):
        self.elapsed.setText(minutes_seconds(position_ms))
        self.remaining.setText("−" + minutes_seconds(max(0, duration_ms - position_ms)))
        self.waveform.set_progress(position_ms / duration_ms if duration_ms > 0 else 0)

    @guarded
    def feed_audio_buffer(self, buffer):
        fmt = buffer.format()
        data = buffer.data()
        if data is not None:
            raw_data = bytes(data)
            if raw_data:
                self.waveform.feed_audio_buffer(raw_data, fmt.channelCount(), fmt.sampleRate())

    def _cancel_analysis(self):
        process = self._process
        self._process = None
        if process is not None:
            if process.state() != QProcess.NotRunning:
                process.kill()
            process.deleteLater()
        self._wave_data = bytearray()

    @guarded
    def _read_wave_data(self, process: QProcess):
        if process is not self._process:
            return
        self._wave_data.extend(bytes(process.readAllStandardOutput()))
        if len(self._wave_data) > MAX_WAVE_BYTES:
            self._wave_overflow = True
            process.kill()

    @guarded
    def _finish_wave(self, process: QProcess, exit_code: int):
        if process is not self._process:
            return
        self._read_wave_data(process)
        self._process = None
        process.deleteLater()
        if exit_code != 0 or self._wave_overflow or len(self._wave_data) < 2:
            self._wave_data = bytearray()
            return
        samples = array("h")
        samples.frombytes(self._wave_data[:len(self._wave_data) // 2 * 2])
        self._wave_data = bytearray()
        if sys.byteorder != "little":
            samples.byteswap()
        bars = 100
        levels = []
        for index in range(bars):
            start = index * len(samples) // bars
            end = max(start + 1, (index + 1) * len(samples) // bars)
            stride = max(1, (end - start) // 192)
            selected = samples[start:end:stride]
            if not selected:
                levels.append(0.0)
                continue
            rms = sqrt(sum(value * value for value in selected) / len(selected)) / 32768
            levels.append(rms)
        peak = max(levels) or 1
        self.waveform.set_levels([max(0.05, min(1.0, (value / peak) ** 0.8))
                                  for value in levels])
