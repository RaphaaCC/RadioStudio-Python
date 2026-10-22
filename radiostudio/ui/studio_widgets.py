"""Compact clock and on-air waveform for the main window."""

from __future__ import annotations

from ..runtime_safety import guarded
from ..i18n import current_language, translate

from array import array
from datetime import datetime
from functools import lru_cache
import logging
from math import cos, floor, log10, pi, sin, sqrt
from pathlib import Path
import sys
import time
from collections import deque

import imageio_ffmpeg
from PySide6.QtCore import QLocale, QProcess, Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


MAX_WAVE_BYTES = 16 * 1024 * 1024
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
LIVE_WAVEFORM_STYLES = frozenset({"reactive_wave", "spectrum", "spectrum_mirror", "spectrum_line"})
LIVE_WAVEFORM_MIN_INTERVAL = 1 / 30
LIVE_SPECTRUM_MIN_INTERVAL = 1 / 15


@lru_cache(maxsize=8)
def _hann_window(size: int) -> tuple[float, ...]:
    """Reuse the fixed analysis window instead of recalculating thousands of cosines."""
    if size <= 1:
        return (1.0,) * max(0, size)
    scale = 2 * pi / (size - 1)
    return tuple(0.5 - 0.5 * cos(scale * index) for index in range(size))


@lru_cache(maxsize=8)
def _spectrum_bin_ranges(size: int, sample_rate: int,
                         band_count: int) -> tuple[tuple[int, int], ...]:
    """Cache the frequency-to-FFT-bin map for the fixed live-analysis sizes."""
    nyquist = size // 2
    min_frequency = 35.0
    max_frequency = min(16000.0, sample_rate / 2)
    ratio = max_frequency / min_frequency
    ranges = []
    for band in range(band_count):
        low = max(1, floor(min_frequency * ratio ** (band / band_count)
                           * size / sample_rate))
        high = min(nyquist, max(
            low + 1,
            floor(min_frequency * ratio ** ((band + 1) / band_count)
                  * size / sample_rate)))
        ranges.append((low, high))
    return tuple(ranges)


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
        self._date_text = None
        self._time_text = None
        self._hour_text = None
        self._hour_style = None
        self.update_time()

    @guarded
    def update_time(self):
        now = datetime.now()
        locale = QLocale(current_language())
        weekday = locale.dayName(now.isoweekday(), QLocale.FormatType.ShortFormat)
        month = locale.monthName(now.month, QLocale.FormatType.ShortFormat)
        date_text = f"{weekday} {now.day:02} {month} {now.year}"
        if date_text != self._date_text:
            self._date_text = date_text
            self.date_label.setText(date_text)
        time_text = now.strftime("%H:%M:%S")
        if time_text != self._time_text:
            self._time_text = time_text
            self.time_label.setText(time_text)
        remaining = 3600 - now.minute * 60 - now.second
        hour_text = translate(
            f"Próxima hora em {remaining // 60:02}:{remaining % 60:02}")
        if hour_text != self._hour_text:
            self._hour_text = hour_text
            self.hour_label.setText(hour_text)
        dark = self.palette().color(QPalette.Window).lightness() < 128
        hour_style = f"color: {'#70ddb0' if dark else '#08784f'}"
        if hour_style != self._hour_style:
            self._hour_style = hour_style
            self.hour_label.setStyleSheet(hour_style)


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
        self._last_reactive_update = 0.0
        self._last_spectrum_update = 0.0
        self.setMinimumHeight(32)

    def set_levels(self, levels: list[float]):
        self.levels = levels
        self.update()

    def reset_live(self):
        self.live_wave = []
        self.live_spectrum = [0.0] * len(self.live_spectrum)
        self._samples.clear()
        self._samples_since_spectrum = 0
        self._last_reactive_update = 0.0
        self._last_spectrum_update = 0.0
        self.update()

    @guarded
    def set_progress(self, progress: float):
        progress = max(0.0, min(1.0, progress))
        # Position callbacks can arrive several times per second. Ignore moves
        # smaller than half a screen pixel; painting those frames changes no
        # visible output and keeps the progress display light on low-end PCs.
        if abs(progress - self.progress) * max(1, self.width()) < 0.5:
            return
        self.progress = progress
        self.update()

    def set_style(self, style: str):
        style = style if style in {value for _, value in WAVEFORM_STYLES} else "bars"
        if style != self.style:
            self._samples.clear()
            self.live_wave = []
            self.live_spectrum = [0.0] * len(self.live_spectrum)
        self.style = style
        self._samples_since_spectrum = 0
        self._last_reactive_update = 0.0
        self._last_spectrum_update = 0.0
        self.update()

    @guarded
    def feed_audio_buffer(self, data: bytes, channels: int, sample_rate: int):
        if self.style not in {"reactive_wave", "spectrum", "spectrum_mirror", "spectrum_line"}:
            return
        if channels < 1 or sample_rate < 1:
            return
        # Audio forwarding is already handled before this optional observer.
        # Skip even PCM conversion/downmix when a modal dialog covers the panel.
        application = QApplication.instance()
        active_modal = getattr(application, "activeModalWidget", None)
        if callable(active_modal) and active_modal() is not None:
            return
        samples = array("h")
        samples.frombytes(data[:len(data) // 2 * 2])
        if sys.byteorder != "little":
            samples.byteswap()
        frames = len(samples) // max(1, channels)
        if not frames:
            return
        if channels == 1:
            # Avoid allocating a temporary Python list for every audio buffer;
            # buffers arrive frequently while a live visualizer is selected.
            self._samples.extend(sample / 32768 for sample in samples)
        elif channels == 2:
            # Pair the two channel arrays in C and keep only the small generator
            # loop in Python; this path runs for every live stereo audio buffer.
            scale = 1.0 / (2 * 32768)
            self._samples.extend(
                (left + right) * scale
                for left, right in zip(samples[::2], samples[1::2]))
        else:
            scale = 1.0 / (channels * 32768)
            for start in range(0, frames * channels, channels):
                total = 0
                for channel in range(channels):
                    total += samples[start + channel]
                self._samples.append(total * scale)

        if self.style == "reactive_wave":
            now = time.monotonic()
            if now - self._last_reactive_update >= LIVE_WAVEFORM_MIN_INTERVAL:
                self._last_reactive_update = now
                self._update_reactive_wave()
        elif self.style in ("spectrum", "spectrum_mirror", "spectrum_line"):
            self._samples_since_spectrum += frames
            now = time.monotonic()
            if (len(self._samples) >= 2048 and self._samples_since_spectrum >= 2048
                    and now - self._last_spectrum_update >= LIVE_SPECTRUM_MIN_INTERVAL):
                self._samples_since_spectrum = 0
                self._last_spectrum_update = now
                self._update_spectrum(list(self._samples)[-2048:], sample_rate)

    @guarded
    def _update_reactive_wave(self):
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

    @guarded
    def _update_spectrum(self, samples: list[float], sample_rate: int):
        size = len(samples)
        if size < 2 or sample_rate <= 0:
            return
        window = _hann_window(size)
        fft_values = [complex(sample * window[index], 0.0)
                      for index, sample in enumerate(samples)]

        j = 0
        for i in range(1, size):
            bit = size >> 1
            while j & bit:
                j ^= bit
                bit >>= 1
            j ^= bit
            if i < j:
                fft_values[i], fft_values[j] = fft_values[j], fft_values[i]

        length = 2
        while length <= size:
            step = complex(cos(-2 * pi / length), sin(-2 * pi / length))
            half = length // 2
            for start in range(0, size, length):
                twiddle = 1.0 + 0.0j
                for offset in range(half):
                    even = start + offset
                    odd = even + half
                    odd_value = twiddle * fft_values[odd]
                    even_value = fft_values[even]
                    fft_values[odd] = even_value - odd_value
                    fft_values[even] = even_value + odd_value
                    twiddle *= step
            length *= 2

        spectrum = []
        for band, (low, high) in enumerate(_spectrum_bin_ranges(
                size, sample_rate, len(self.live_spectrum))):
            magnitude = max((abs(fft_values[index]) for index in range(low, high)),
                            default=0.0) * 2 / size
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
    track_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(105)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(9, 4, 9, 4)
        outer.setSpacing(8)
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        header = QHBoxLayout()
        self.on_air_heading = QLabel(translate("NO AR"))
        self.on_air_heading.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.title = QLabel(translate("Nenhuma faixa em reprodução"))
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
        header.addWidget(self.on_air_heading)
        header.addWidget(self.title, 1)
        layout.addLayout(header)
        self.waveform = Waveform()
        layout.addWidget(self.waveform, 1)
        times = QHBoxLayout()
        self.elapsed = QLabel(translate("00:00"))
        self.remaining = QLabel(translate("−00:00"))
        self.remaining.setAlignment(Qt.AlignRight)
        times.addWidget(self.elapsed)
        times.addWidget(self.remaining, 1)
        layout.addLayout(times)
        outer.addWidget(content, 1)
        self.cover_art = CoverArtLabel(88, self, "da faixa no ar")
        outer.addWidget(self.cover_art, 0, Qt.AlignVCenter)
        self._process: QProcess | None = None
        # A canceled analysis can still be stopping while the next track starts
        # its own FFmpeg job. Keep every child reachable through shutdown.
        self._wave_processes: dict[int, QProcess] = {}
        self._wave_data = bytearray()
        self._wave_overflow = False
        self._broadcast_active = False
        self._blink_red = False
        self._track_path = ""
        self._waveform_visualizers_enabled = True
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
        self._track_path = str(path)
        self.cover_art.set_cover_image(None)
        self.track_changed.emit(self._track_path)
        self.waveform.reset_live()
        self.title.setText(title)
        self.title.setToolTip(title)
        self.waveform.set_levels([])
        self.set_progress(0, 0)
        if not self._waveform_visualizers_enabled:
            return
        if self.waveform.style in LIVE_WAVEFORM_STYLES:
            return
        if self.isVisible():
            self._start_waveform_analysis(path)

    @guarded
    def set_waveform_style(self, style: str):
        self.waveform.set_style(style)
        if not self._waveform_visualizers_enabled:
            self._cancel_analysis()
            self.waveform.set_levels([])
            return
        if style in LIVE_WAVEFORM_STYLES:
            self._cancel_analysis()
            self.waveform.set_levels([])
        elif self._track_path:
            self._start_waveform_analysis(self._track_path)

    @guarded
    def set_waveform_visualizers_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self._waveform_visualizers_enabled:
            return
        self._waveform_visualizers_enabled = enabled
        if not enabled:
            self._cancel_analysis()
            self.waveform.reset_live()
            self.waveform.set_levels([])
            self.waveform.hide()
            return
        self.waveform.show()
        if self._track_path and self.waveform.style not in LIVE_WAVEFORM_STYLES:
            self._start_waveform_analysis(self._track_path)

    @guarded
    def _start_waveform_analysis(self, path: str):
        # Static analysis decodes the whole track in a child FFmpeg process.
        # It has no value while the panel is hidden (for example, in the tray).
        if (not self._waveform_visualizers_enabled or not self.isVisible()
                or self.waveform.style in LIVE_WAVEFORM_STYLES):
            return
        # Switching between static visualization styles does not change the
        # envelope being decoded. Keep the in-flight job instead of launching
        # another FFmpeg decode for the same current track.
        if self._process is not None:
            return
        try:
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except (OSError, RuntimeError):
            return
        process = QProcess(self)
        process.setStandardErrorFile(QProcess.nullDevice())
        process.readyReadStandardOutput.connect(lambda: self._read_wave_data(process))
        process.finished.connect(lambda code, _status: self._finish_wave(process, code))
        process.errorOccurred.connect(
            lambda error: self._wave_process_error(process, error))
        self._wave_processes[id(process)] = process
        self._process = process
        self._wave_data = bytearray()
        self._wave_overflow = False
        process.start(ffmpeg, ["-hide_banner", "-loglevel", "error", "-i", str(Path(path)),
                               "-vn", "-threads", "1", "-ac", "1", "-ar", "200",
                               "-f", "s16le", "-"])

    @guarded
    def clear_track(self):
        self._cancel_analysis()
        self._track_path = ""
        self.cover_art.set_cover_image(None)
        self.track_changed.emit("")
        self.waveform.reset_live()
        self.title.setText(translate("Nenhuma faixa em reprodução"))
        self.title.setToolTip(translate(""))
        self.waveform.set_levels([])
        self.set_progress(0, 0)

    def shutdown_analysis(self):
        self.clear_track()
        # Include analyses canceled by earlier track/style changes, not only
        # the newest process stored in _process. Otherwise destroying this
        # parent can destroy an older QProcess while FFmpeg is still exiting.
        for process in tuple(self._wave_processes.values()):
            if process.state() != QProcess.NotRunning:
                process.kill()
                if not process.waitForFinished(2000):
                    process.kill()
                    process.waitForFinished(2000)
            if process.state() == QProcess.NotRunning:
                self._wave_processes.pop(id(process), None)
                process.deleteLater()
            else:
                logging.getLogger("radiostudio").error(
                    "O processo FFmpeg do visualizador não encerrou ao fechar")

    @guarded
    def set_progress(self, position_ms: int, duration_ms: int):
        elapsed = minutes_seconds(position_ms)
        remaining = "−" + minutes_seconds(max(0, duration_ms - position_ms))
        if self.elapsed.text() != elapsed:
            self.elapsed.setText(elapsed)
        if self.remaining.text() != remaining:
            self.remaining.setText(remaining)
        if self._waveform_visualizers_enabled:
            self.waveform.set_progress(position_ms / duration_ms if duration_ms > 0 else 0)

    @guarded
    def feed_audio_buffer(self, buffer):
        if not self._waveform_visualizers_enabled:
            return
        fmt = buffer.format()
        data = buffer.data()
        if data is not None and len(data):
            # QAudioBuffer exposes a read-only memoryview; let the visualizer
            # consume it directly instead of copying the same PCM block first.
            self.waveform.feed_audio_buffer(data, fmt.channelCount(), fmt.sampleRate())

    @guarded
    def showEvent(self, event):
        super().showEvent(event)
        if (self._waveform_visualizers_enabled and self._track_path
                and not self.waveform.levels and self._process is None
                and self.waveform.style not in LIVE_WAVEFORM_STYLES):
            path = self._track_path
            QTimer.singleShot(
                0, lambda target=path: self._start_waveform_analysis(target)
                if self.isVisible() and target == self._track_path else None)

    @guarded
    def hideEvent(self, event):
        if self._process is not None:
            self._cancel_analysis()
        super().hideEvent(event)

    def _cancel_analysis(self):
        process = self._process
        self._process = None
        if process is not None:
            if process.state() != QProcess.NotRunning:
                # QProcess must stay alive until the asynchronous kill has completed.
                # Deleting it here can leave FFmpeg running and emit
                # "QProcess: Destroyed while process ... is still running".
                process.kill()
            else:
                self._wave_processes.pop(id(process), None)
                process.deleteLater()
        self._wave_data = bytearray()

    @guarded
    def _wave_process_error(self, process: QProcess, error):
        if error == QProcess.FailedToStart:
            self._wave_processes.pop(id(process), None)
            if process is self._process:
                self._process = None
                self._wave_data = bytearray()
            # FailedToStart does not emit finished, so release the process here.
            process.deleteLater()

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
        self._wave_processes.pop(id(process), None)
        if process is not self._process:
            # Canceled analyses finish asynchronously; they still need cleanup.
            process.deleteLater()
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


class CoverArtLabel(QLabel):
    """Square cover preview that preserves the source image aspect ratio."""

    cover_clicked = Signal(object)

    def __init__(self, size: int = 104, parent=None, description: str = "do álbum"):
        super().__init__(parent)
        self._description = description
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QLabel { background: palette(base); color: palette(mid); "
            "border: 1px solid palette(mid); border-radius: 3px; }"
        )
        self._image = None
        self.setText(translate("♪\nSem capa"))
        self.setCursor(Qt.ArrowCursor)
        self.setToolTip(translate("Nenhuma capa disponível"))

    def set_cover_image(self, image):
        self._image = image if image is not None and not image.isNull() else None
        if self._image is None:
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip(translate("Nenhuma capa disponível"))
        else:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(translate(f"Clique para visualizar a capa {self._description}"))
        self._update_pixmap()

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint())
                and self._image is not None):
            self.cover_clicked.emit(self._image.copy())
        super().mouseReleaseEvent(event)

    def _update_pixmap(self):
        if self._image is None:
            self.setPixmap(QPixmap())
            self.setText(translate("♪\nSem capa"))
            return
        rect = self.contentsRect()
        width, height = rect.width(), rect.height()
        if width <= 0 or height <= 0:
            return
        scaled = QPixmap.fromImage(self._image).scaled(
            width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (scaled.width() - width) // 2)
        y = max(0, (scaled.height() - height) // 2)
        self.setText(translate(""))
        self.setPixmap(scaled.copy(x, y, width, height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_pixmap()
