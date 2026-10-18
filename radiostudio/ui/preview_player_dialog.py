"""Independent controls for auditioning a playlist track."""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from ..services.track_metadata import read_track_metadata
from ..services.audio_decode_fallback import AudioDecodeFallback
from ..services.audio_paths import same_audio_path
from ..runtime_safety import guarded
from .icons import apply_app_icon


class PreviewPlayerDialog(QDialog):
    def __init__(self, player: QMediaPlayer, audio: QAudioOutput, parent=None):
        super().__init__(parent)
        apply_app_icon(self)
        self.player = player
        self.audio = audio
        self._seeking = False
        self.audio_decode_fallback = AudioDecodeFallback(self)
        self._decoded_path = ""
        self._fallback_attempted_source = ""
        self.setWindowTitle("🎧 Pré-escuta")
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self.title_label = QLabel("Nenhuma faixa selecionada")
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(self.title_label)

        self.details_label = QLabel("Selecione uma faixa na fila para começar.")
        self.details_label.setWordWrap(True)
        layout.addWidget(self.details_label)

        timeline = QHBoxLayout()
        self.position_label = QLabel("00:00")
        self.position_label.setMinimumWidth(48)
        self.position_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setEnabled(False)
        self.duration_label = QLabel("00:00")
        self.duration_label.setMinimumWidth(48)
        timeline.addWidget(self.position_label)
        timeline.addWidget(self.seek_slider, 1)
        timeline.addWidget(self.duration_label)
        layout.addLayout(timeline)

        controls = QHBoxLayout()
        self.play_pause_button = QPushButton("▶️ Reproduzir")
        self.play_pause_button.clicked.connect(self.toggle_play_pause)
        self.stop_button = QPushButton("⏹ Parar")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        controls.addStretch()
        controls.addWidget(self.play_pause_button)
        controls.addWidget(self.stop_button)
        controls.addStretch()
        layout.addLayout(controls)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("🔊 Volume da pré-escuta"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(round(audio.volume() * 100))
        self.volume_slider.valueChanged.connect(lambda value: audio.setVolume(value / 100))
        volume_row.addWidget(self.volume_slider, 1)
        layout.addLayout(volume_row)

        self.status_label = QLabel("Parada")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.seek_slider.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.playbackStateChanged.connect(self.update_state)
        self.player.mediaStatusChanged.connect(self.update_media_status)
        self.player.errorOccurred.connect(self.show_error)

    @guarded
    def load_track(self, path: str) -> None:
        metadata = read_track_metadata(path)
        self.title_label.setText(metadata.title or Path(path).stem)
        details = " — ".join(value for value in (metadata.artist, metadata.album) if value)
        self.details_label.setText(details or Path(path).name)
        self.setWindowTitle(f"🎧 Pré-escuta — {metadata.title or Path(path).stem}")
        if not same_audio_path(self.player.source().toLocalFile(), path):
            self.audio_decode_fallback.cancel(self.player)
            old_decoded = self._decoded_path
            self._decoded_path = ""
            self._fallback_attempted_source = ""
            self.player.setSource(QUrl.fromLocalFile(path))
            if old_decoded:
                self.audio_decode_fallback.release(old_decoded)
            self.seek_slider.setRange(0, 0)
            self.position_label.setText("00:00")
            self.duration_label.setText("00:00")
        self.show()
        self.raise_()
        self.activateWindow()
        self.player.play()
        self.update_state(self.player.playbackState())

    @guarded
    def toggle_play_pause(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        elif self.player.source().isValid():
            if not self._decoded_path:
                self._fallback_attempted_source = ""
            self.player.play()

    @guarded
    def stop(self) -> None:
        self.audio_decode_fallback.cancel(self.player)
        if not self._decoded_path:
            self._fallback_attempted_source = ""
        self.player.stop()
        self.seek_slider.setValue(0)
        self.position_label.setText("00:00")

    @guarded
    def update_position(self, position: int) -> None:
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(position)
        self.position_label.setText(self._time_text(position))

    @guarded
    def update_duration(self, duration: int) -> None:
        self.seek_slider.setRange(0, max(0, duration))
        self.seek_slider.setEnabled(duration > 0)
        self.duration_label.setText(self._time_text(duration))

    @guarded
    def update_state(self, state) -> None:
        if state == QMediaPlayer.PlayingState:
            self.play_pause_button.setText("⏸️ Pausar")
            self.status_label.setText("Pré-escuta em reprodução")
            self.stop_button.setEnabled(True)
        elif state == QMediaPlayer.PausedState:
            self.play_pause_button.setText("▶️ Continuar")
            self.status_label.setText("Pré-escuta pausada")
            self.stop_button.setEnabled(True)
        else:
            self.play_pause_button.setText("▶️ Reproduzir")
            self.status_label.setText("Parada")
            self.stop_button.setEnabled(self.player.source().isValid())

    @guarded
    def update_media_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia:
            self.status_label.setText("Faixa concluída")
        elif status == QMediaPlayer.InvalidMedia:
            source = self._decoded_path or self.player.source().toLocalFile()
            if (not self._decoded_path and source
                    and not same_audio_path(self._fallback_attempted_source, source)):
                self._fallback_attempted_source = source
                self.status_label.setText("Preparando decodificação compatível…")
                self.audio_decode_fallback.convert(
                    self.player, source, self.decode_fallback_ready)
            else:
                self.status_label.setText(f"Formato não reproduzível: {Path(source).name}")

    @guarded
    def decode_fallback_ready(self, source, decoded_path, error) -> None:
        if not same_audio_path(self.player.source().toLocalFile(), source):
            if decoded_path:
                self.audio_decode_fallback.release(decoded_path, delay_ms=0)
            return
        if not decoded_path:
            self.status_label.setText(f"Não foi possível decodificar: {error}")
            return
        self._decoded_path = decoded_path
        self.player.setSource(QUrl.fromLocalFile(decoded_path))
        self.player.play()

    @guarded
    def show_error(self, _error, message: str) -> None:
        if message:
            self.status_label.setText(f"Erro na pré-escuta: {message}")

    @guarded
    def shutdown_fallback(self) -> None:
        self.audio_decode_fallback.cancel(self.player)
        self.player.stop()
        self.player.setSource(QUrl())
        if self._decoded_path:
            self.audio_decode_fallback.release(self._decoded_path, delay_ms=0)
            self._decoded_path = ""
        self.audio_decode_fallback.shutdown()

    @staticmethod
    def _time_text(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        return f"{seconds // 60:02}:{seconds % 60:02}"

    @guarded
    def closeEvent(self, event) -> None:
        self.stop()
        self.player.setSource(QUrl())
        if self._decoded_path:
            self.audio_decode_fallback.release(self._decoded_path, delay_ms=0)
            self._decoded_path = ""
        self.audio_decode_fallback.shutdown()
        super().closeEvent(event)
