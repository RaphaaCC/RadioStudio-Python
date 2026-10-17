"""Capture, normalize, mix, and locally monitor microphone and auxiliary inputs."""

from __future__ import annotations

import logging
import math
import struct
import time
from array import array

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import (QAudio, QAudioDevice, QAudioFormat, QAudioSink,
                                  QAudioSource, QMediaDevices)

from .broadcast import CHANNELS, PCM_BYTES_PER_FRAME, PCMBuffer, SAMPLE_RATE


INPUT_CHANNELS = ("microphone", "aux1", "aux2", "aux3", "aux4", "aux5")
INPUT_LABELS = {"microphone": "Microfone", **{f"aux{i}": f"Auxiliar {i}" for i in range(1, 6)}}
_SAMPLE_BYTES = {
    QAudioFormat.UInt8: 1,
    QAudioFormat.Int16: 2,
    QAudioFormat.Int32: 4,
    QAudioFormat.Float: 4,
}


def audio_device_key(device: QAudioDevice) -> str:
    return bytes(device.id()).hex()


def decode_audio_samples(data: bytes, sample_format) -> list[float]:
    """Decode Qt's common PCM formats into normalized floating point samples."""
    if sample_format == QAudioFormat.Int16:
        width, code, scale = 2, "h", 32768.0
    elif sample_format == QAudioFormat.Int32:
        width, code, scale = 4, "i", 2147483648.0
    elif sample_format == QAudioFormat.Float:
        width, code, scale = 4, "f", 1.0
    elif sample_format == QAudioFormat.UInt8:
        return [(value - 128) / 128.0 for value in data]
    else:
        raise ValueError("Formato de amostra de áudio não suportado")
    count = len(data) // width
    if not count:
        return []
    values = struct.unpack("<" + code * count, data[:count * width])
    normalized = []
    for value in values:
        value = value / scale if scale != 1.0 else value
        normalized.append(max(-1.0, min(1.0, value)) if math.isfinite(value) else 0.0)
    return normalized


def _stereo_frames(samples: list[float], channels: int) -> list[tuple[float, float]]:
    channels = max(1, channels)
    frames = len(samples) // channels
    if channels == 1:
        return [(samples[i], samples[i]) for i in range(frames)]
    return [(samples[i * channels], samples[i * channels + 1]) for i in range(frames)]


def _resample(frames: list[tuple[float, float]], source_rate: int,
              target_rate: int) -> list[tuple[float, float]]:
    if not frames or source_rate <= 0 or target_rate <= 0:
        return []
    if source_rate == target_rate:
        return frames
    output_count = max(1, round(len(frames) * target_rate / source_rate))
    if len(frames) == 1:
        return [frames[0]] * output_count
    ratio = source_rate / target_rate
    result = []
    for index in range(output_count):
        position = min(len(frames) - 1, index * ratio)
        left_index = int(position)
        right_index = min(len(frames) - 1, left_index + 1)
        fraction = position - left_index
        left, right = frames[left_index], frames[right_index]
        result.append((left[0] + (right[0] - left[0]) * fraction,
                       left[1] + (right[1] - left[1]) * fraction))
    return result


def _encode_samples(frames: list[tuple[float, float]], sample_format,
                    channels: int) -> bytes:
    channels = max(1, channels)
    flattened = []
    for left, right in frames:
        values = ((left + right) / 2.0,) if channels == 1 else (left, right) + (0.0,) * (channels - 2)
        flattened.extend(max(-1.0, min(1.0, value)) for value in values)
    if sample_format == QAudioFormat.Int16:
        values = [max(-32768, min(32767, round(value * 32767))) for value in flattened]
        return struct.pack("<" + "h" * len(values), *values) if values else b""
    if sample_format == QAudioFormat.Int32:
        values = [max(-2147483648, min(2147483647, round(value * 2147483647)))
                  for value in flattened]
        return struct.pack("<" + "i" * len(values), *values) if values else b""
    if sample_format == QAudioFormat.Float:
        return struct.pack("<" + "f" * len(flattened), *flattened) if flattened else b""
    if sample_format == QAudioFormat.UInt8:
        return bytes(max(0, min(255, round(value * 128 + 128))) for value in flattened)
    raise ValueError("Formato de saída de áudio não suportado")


def convert_input_to_master(data: bytes, audio_format: QAudioFormat) -> bytes:
    """Convert a device chunk to 44.1 kHz, stereo, signed 16-bit PCM."""
    samples = decode_audio_samples(data, audio_format.sampleFormat())
    frames = _stereo_frames(samples, audio_format.channelCount())
    frames = _resample(frames, audio_format.sampleRate(), SAMPLE_RATE)
    return _encode_samples(frames, QAudioFormat.Int16, CHANNELS)


def convert_master_to_device(data: bytes, audio_format: QAudioFormat) -> bytes:
    """Convert master PCM to the selected output device's preferred PCM format."""
    samples = decode_audio_samples(data, QAudioFormat.Int16)
    frames = _stereo_frames(samples, CHANNELS)
    frames = _resample(frames, SAMPLE_RATE, audio_format.sampleRate())
    return _encode_samples(frames, audio_format.sampleFormat(), audio_format.channelCount())


def pcm_rms_db(data: bytes) -> float:
    samples = array("h")
    samples.frombytes(data[:len(data) - (len(data) % 2)])
    if not samples:
        return -120.0
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768.0
    return 20.0 * math.log10(max(rms, 1e-6))


def mix_pcm_chunks(chunks: list[bytes], gain: float = 1.0) -> bytes:
    mixed = [0] * (PCM_BYTES_PER_FRAME // 2)
    for chunk in chunks:
        samples = array("h")
        samples.frombytes(chunk[:PCM_BYTES_PER_FRAME])
        for index, sample in enumerate(samples):
            mixed[index] += int(sample * gain)
    return array("h", (max(-32768, min(32767, value)) for value in mixed)).tobytes()


class AudioInputController(QObject):
    """Owns independent Qt capture sources and an optional local monitor sink."""

    status_changed = Signal(str, str)
    speech_activity_changed = Signal(bool)

    def __init__(self, broadcast, parent=None):
        super().__init__(parent)
        self.broadcast = broadcast
        self.active: dict[str, dict] = {}
        self.input_device_keys = {channel: "" for channel in INPUT_CHANNELS}
        self.monitor_device_key = ""
        self.monitor_enabled = True
        self.monitor_gain = 0.8
        self.speech_threshold_db = -38.0
        self.speech_hold_ms = 450
        self.monitor_sink = None
        self.monitor_io = None
        self.monitor_format = None
        self.monitor_pending = bytearray()
        self.monitor_buffers: dict[str, PCMBuffer] = {}
        self._monitor_error = ""
        self._monitor_retry_after = 0.0
        self._last_speech = 0.0
        self._mic_speaking = False
        self._monitor_deadline = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self._pump)

    def configure(self, input_device_keys: dict | None = None, monitor_device_key: str | None = None,
                  monitor_enabled: bool | None = None, monitor_gain: float | None = None,
                  speech_threshold_db: float | None = None):
        if input_device_keys is not None:
            previous = self.input_device_keys
            new_keys = {key: str(input_device_keys.get(key, "") or "") for key in INPUT_CHANNELS}
            self.input_device_keys = new_keys
            for channel in tuple(self.active):
                if previous.get(channel, "") != new_keys[channel]:
                    self.stop(channel, report=False)
                    self.start(channel)
        if monitor_device_key is not None and monitor_device_key != self.monitor_device_key:
            self.monitor_device_key = monitor_device_key
            self._stop_monitor()
            self._monitor_error = ""
            self._monitor_retry_after = 0.0
        if monitor_enabled is not None:
            self.monitor_enabled = bool(monitor_enabled)
            self._monitor_retry_after = 0.0
            self._monitor_error = ""
            if not self.monitor_enabled:
                self._stop_monitor()
            elif self.active:
                self._ensure_monitor()
        if monitor_gain is not None:
            self.monitor_gain = max(0.0, min(1.0, float(monitor_gain)))
        if speech_threshold_db is not None:
            self.speech_threshold_db = max(-80.0, min(-10.0, float(speech_threshold_db)))

    def start(self, channel: str) -> bool:
        if channel not in INPUT_CHANNELS:
            raise ValueError("Entrada de áudio desconhecida")
        if channel in self.active:
            return True
        device = self._resolve_input(self.input_device_keys.get(channel, ""))
        if device.isNull():
            message = ("O dispositivo selecionado não está disponível"
                       if self.input_device_keys.get(channel) else
                       "Nenhum dispositivo de entrada disponível")
            self.status_changed.emit(channel, message)
            return False
        audio_format = QAudioFormat()
        audio_format.setSampleRate(SAMPLE_RATE)
        audio_format.setChannelCount(CHANNELS)
        audio_format.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(audio_format):
            audio_format = device.preferredFormat()
        if (not audio_format.sampleRate() or not audio_format.channelCount()
                or audio_format.sampleFormat() not in _SAMPLE_BYTES):
            self.status_changed.emit(channel, f"Formato não suportado: {device.description()}")
            return False
        source = None
        try:
            source = QAudioSource(device, audio_format, self)
            source.setBufferSize(max(4096, audio_format.sampleRate()
                                     * audio_format.channelCount()
                                     * _SAMPLE_BYTES[audio_format.sampleFormat()] // 4))
            source.stateChanged.connect(
                lambda state, key=channel, item=source: self._source_state_changed(key, item, state))
            io_device = source.start()
            if io_device is None or source.state() == QAudio.State.StoppedState:
                error = (self._error_text(source.error()) if source.error() != QAudio.NoError
                         else "o dispositivo não iniciou")
                source.stop()
                source.deleteLater()
                self.status_changed.emit(channel, f"Não foi possível abrir {device.description()}: {error}")
                return False
            self.active[channel] = {"source": source, "io": io_device, "format": audio_format,
                                    "device": device, "pending": bytearray(),
                                    "raw_pending": bytearray(),
                                    "input_frame_bytes": _SAMPLE_BYTES[audio_format.sampleFormat()]
                                    * audio_format.channelCount()}
            self.monitor_buffers[channel] = PCMBuffer()
            self.broadcast.remove_source(f"input:{channel}")
            self.timer.start()
            if self.monitor_enabled:
                self._ensure_monitor()
            self.status_changed.emit(channel, f"Ativo · {device.description()}")
            return True
        except Exception as exc:
            if source is not None:
                try:
                    source.stop()
                    source.deleteLater()
                except Exception:
                    logging.getLogger("radiostudio").exception("Falha ao liberar captura de áudio")
            self.status_changed.emit(channel, f"Falha ao iniciar: {exc}")
            return False

    def stop(self, channel: str, report: bool = True):
        item = self.active.pop(channel, None)
        self.monitor_buffers.pop(channel, None)
        self.broadcast.remove_source(f"input:{channel}")
        if item:
            try:
                item["source"].stop()
                item["source"].deleteLater()
            except Exception:
                logging.getLogger("radiostudio").exception("Falha ao parar %s", INPUT_LABELS[channel])
        if not self.active:
            self.timer.stop()
            self._stop_monitor()
        if channel == "microphone":
            self._set_speech(False)
        if report:
            self.status_changed.emit(channel, "Desativado")

    def stop_all(self):
        for channel in tuple(self.active):
            self.stop(channel, report=False)
        self._stop_monitor()

    def set_monitor_gain(self, gain: float):
        self.monitor_gain = max(0.0, min(1.0, float(gain)))

    def _resolve_input(self, key: str) -> QAudioDevice:
        if key:
            for device in QMediaDevices.audioInputs():
                if audio_device_key(device) == key:
                    return device
            return QAudioDevice()
        return QMediaDevices.defaultAudioInput()

    def _resolve_output(self) -> QAudioDevice:
        if self.monitor_device_key:
            for device in QMediaDevices.audioOutputs():
                if audio_device_key(device) == self.monitor_device_key:
                    return device
        return QMediaDevices.defaultAudioOutput()

    @staticmethod
    def _error_text(error) -> str:
        return {QAudio.NoError: "sem detalhe", QAudio.OpenError: "dispositivo ocupado ou inacessível",
                QAudio.IOError: "erro de leitura de áudio", QAudio.UnderrunError: "captura interrompida",
                QAudio.FatalError: "erro de áudio"}.get(error, str(error))

    def _source_state_changed(self, channel: str, source: QAudioSource, state):
        item = self.active.get(channel)
        if item is None or item["source"] is not source:
            return
        if state == QAudio.State.StoppedState:
            message = (self._error_text(source.error()) if source.error() != QAudio.NoError
                       else "o dispositivo interrompeu a captura")
            self.stop(channel, report=False)
            self.status_changed.emit(channel, f"Captura parou: {message}")

    def _pump(self):
        for channel, item in tuple(self.active.items()):
            try:
                available = item["io"].bytesAvailable()
                if available <= 0:
                    continue
                raw = bytes(item["io"].read(min(available, 262144)))
                if not raw:
                    continue
                item["raw_pending"].extend(raw)
                frame_bytes = item["input_frame_bytes"]
                aligned = len(item["raw_pending"]) - len(item["raw_pending"]) % frame_bytes
                if not aligned:
                    continue
                input_data = bytes(item["raw_pending"][:aligned])
                del item["raw_pending"][:aligned]
                pcm = convert_input_to_master(input_data, item["format"])
                if not pcm:
                    continue
                item["pending"].extend(pcm)
                while len(item["pending"]) >= PCM_BYTES_PER_FRAME:
                    frame = bytes(item["pending"][:PCM_BYTES_PER_FRAME])
                    del item["pending"][:PCM_BYTES_PER_FRAME]
                    # The monitor slider only controls local listening. Live inputs
                    # must reach the program bus at unity just like the main deck.
                    self.broadcast.push_audio(f"input:{channel}", frame, 1.0)
                    buffer = self.monitor_buffers.get(channel)
                    if self.monitor_enabled and buffer is not None:
                        buffer.append(frame)
                    if (channel == "microphone"
                            and pcm_rms_db(frame) >= self.speech_threshold_db):
                        self._last_speech = time.monotonic()
                        self._set_speech(True)
            except Exception as exc:
                logging.getLogger("radiostudio").exception("Falha ao processar %s", INPUT_LABELS[channel])
                self.stop(channel, report=False)
                self.status_changed.emit(channel, f"Falha de áudio: {exc}")
        if self._mic_speaking and time.monotonic() - self._last_speech > self.speech_hold_ms / 1000:
            self._set_speech(False)
        now = time.monotonic()
        if now >= self._monitor_deadline:
            self._monitor_deadline = now + 0.02
            self._pump_monitor()

    def _set_speech(self, speaking: bool):
        if speaking != self._mic_speaking:
            self._mic_speaking = speaking
            self.speech_activity_changed.emit(speaking)

    def _ensure_monitor(self):
        if self.monitor_sink or not self.monitor_enabled or not self.active:
            return
        if time.monotonic() < self._monitor_retry_after:
            return
        device = self._resolve_output()
        if device.isNull():
            self._report_monitor_error("Sem dispositivo para monitorar as entradas")
            return
        audio_format = device.preferredFormat()
        if (not audio_format.sampleRate() or not audio_format.channelCount()
                or audio_format.sampleFormat() not in _SAMPLE_BYTES):
            self._report_monitor_error("Formato do dispositivo de escuta não suportado")
            return
        try:
            sink = QAudioSink(device, audio_format, self)
            sink.setBufferSize(max(4096, audio_format.sampleRate()
                                   * audio_format.channelCount()
                                   * _SAMPLE_BYTES[audio_format.sampleFormat()] // 5))
            sink.stateChanged.connect(
                lambda state, item=sink: self._monitor_state_changed(item, state))
            io_device = sink.start()
            if io_device is None or sink.state() == QAudio.State.StoppedState:
                message = (self._error_text(sink.error()) if sink.error() != QAudio.NoError
                           else "o dispositivo não iniciou")
                sink.stop()
                sink.deleteLater()
                self._report_monitor_error(f"Escuta local indisponível: {message}")
                return
            self.monitor_sink, self.monitor_io, self.monitor_format = sink, io_device, audio_format
            self.monitor_pending.clear()
            self._monitor_error = ""
            self._monitor_retry_after = 0.0
        except Exception as exc:
            logging.getLogger("radiostudio").exception("Falha ao iniciar monitor de entradas")
            self._report_monitor_error(f"Escuta local indisponível: {exc}")

    def _report_monitor_error(self, message: str):
        self._monitor_retry_after = time.monotonic() + 2.0
        if message != self._monitor_error:
            self._monitor_error = message
            self.status_changed.emit("monitor", message)

    def _monitor_state_changed(self, sink: QAudioSink, state):
        if self.monitor_sink is not sink or state != QAudio.State.StoppedState:
            return
        message = (self._error_text(sink.error()) if sink.error() != QAudio.NoError
                   else "o dispositivo de escuta parou")
        self._stop_monitor()
        self._report_monitor_error(f"Escuta local interrompida: {message}")

    def _pump_monitor(self):
        if not self.monitor_enabled or not self.active:
            return
        self._ensure_monitor()
        if not self.monitor_sink or not self.monitor_io or not self.monitor_format:
            return
        try:
            chunks = []
            for channel, buffer in tuple(self.monitor_buffers.items()):
                if buffer.length >= PCM_BYTES_PER_FRAME:
                    chunks.append(buffer.take(PCM_BYTES_PER_FRAME))
            if chunks:
                frame = mix_pcm_chunks(chunks, self.monitor_gain)
                converted = convert_master_to_device(frame, self.monitor_format)
                if converted:
                    self.monitor_pending.extend(converted)
            available = self.monitor_sink.bytesFree()
            width = _SAMPLE_BYTES[self.monitor_format.sampleFormat()]
            frame_bytes = width * max(1, self.monitor_format.channelCount())
            to_write = min(len(self.monitor_pending), available)
            to_write -= to_write % frame_bytes
            if to_write > 0:
                written = self.monitor_io.write(bytes(self.monitor_pending[:to_write]))
                if written > 0:
                    del self.monitor_pending[:written]
            if len(self.monitor_pending) > SAMPLE_RATE * CHANNELS * 4:
                del self.monitor_pending[:-SAMPLE_RATE * CHANNELS * 2]
        except Exception as exc:
            logging.getLogger("radiostudio").exception("Falha ao monitorar entradas de áudio")
            self._report_monitor_error(f"Escuta local interrompida: {exc}")
            self._stop_monitor()

    def _stop_monitor(self):
        sink, self.monitor_sink = self.monitor_sink, None
        self.monitor_io = None
        self.monitor_format = None
        self.monitor_pending.clear()
        for buffer in self.monitor_buffers.values():
            buffer.take(buffer.length)
        if sink:
            try:
                sink.stop()
                sink.deleteLater()
            except Exception:
                logging.getLogger("radiostudio").exception("Falha ao encerrar monitor de entradas")
