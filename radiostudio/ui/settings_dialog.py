"""Application settings dialog and audio-output device selection."""

from __future__ import annotations

from ..runtime_safety import guarded

from PySide6.QtMultimedia import QAudioDevice, QMediaDevices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel,
    QDoubleSpinBox, QLineEdit, QMessageBox, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from ..config import APP_DISPLAY_NAME
from ..services.track_metadata import (DEFAULT_STATION_METADATA, PLACEHOLDERS, TrackMetadata,
                            format_broadcast_title, unknown_placeholders)
from .studio_widgets import WAVEFORM_STYLES
from ..services.audio_inputs import INPUT_CHANNELS, INPUT_LABELS, audio_device_key


def device_key(device: QAudioDevice) -> str:
    return bytes(device.id()).hex()


def resolve_output_device(saved_key: str) -> QAudioDevice:
    if saved_key:
        for device in QMediaDevices.audioOutputs():
            if device_key(device) == saved_key:
                return device
    return QAudioDevice()  # Follow the current Windows default output.


class SettingsDialog(QDialog):
    def __init__(self, main_key: str, preview_key: str, local_master_enabled: bool,
                 station_metadata: dict, current_track: TrackMetadata | None = None,
                 parent=None, minimize_to_tray: bool = True,
                 resume_on_startup: bool = True, tray_available: bool = True,
                 player_controls_position: str = "bottom",
                 waveform_style: str = "bars", auto_start_broadcast: bool = False,
                 auto_transitions_enabled: bool = False,
                 fade_in_ms: int = 0, fade_out_ms: int = 0, mix_ms: int = 0,
                 trim_silence: bool = False, silence_threshold_db: float = -42,
                 silence_hold_ms: int = 1200,
                 input_device_keys: dict | None = None,
                 mic_music_level_percent: int = 35, dynamic_mic_duck: bool = False,
                 speech_music_level_percent: int = 20, speech_threshold_db: float = -38):
        super().__init__(parent)
        self.setWindowTitle(f"Configurações — {APP_DISPLAY_NAME}")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        general_page = QWidget()
        general_layout = QVBoxLayout(general_page)
        general_form = QFormLayout()
        self.minimize_to_tray = QCheckBox("Ao fechar a janela, continuar na bandeja do Windows")
        self.minimize_to_tray.setChecked(minimize_to_tray)
        self.minimize_to_tray.setEnabled(tray_available)
        general_form.addRow("Bandeja", self.minimize_to_tray)
        self.resume_on_startup = QCheckBox(
            "Ao abrir, retomar a música que estava tocando ao sair")
        self.resume_on_startup.setChecked(resume_on_startup)
        general_form.addRow("Inicialização", self.resume_on_startup)
        self.auto_start_broadcast = QCheckBox(
            "Iniciar transmissão ao abrir se estava ativa ao sair")
        self.auto_start_broadcast.setChecked(auto_start_broadcast)
        general_form.addRow("Transmissão", self.auto_start_broadcast)
        self.player_controls_position = QComboBox()
        self.player_controls_position.addItem("Abaixo da playlist", "bottom")
        self.player_controls_position.addItem("No topo, abaixo de No ar", "top")
        selected_position = self.player_controls_position.findData(player_controls_position)
        self.player_controls_position.setCurrentIndex(max(0, selected_position))
        general_form.addRow("Controles do player", self.player_controls_position)
        self.waveform_style = QComboBox()
        for label, value in WAVEFORM_STYLES:
            self.waveform_style.addItem(label, value)
        self.waveform_style.setToolTip(
            "A onda reativa acompanha o sinal ao vivo; os modos de espectro mostram as frequências do áudio.")
        selected_style = self.waveform_style.findData(waveform_style)
        self.waveform_style.setCurrentIndex(max(0, selected_style))
        general_form.addRow("Visualizador de ondas", self.waveform_style)
        general_layout.addLayout(general_form)
        if not tray_available:
            note = QLabel("A bandeja do sistema não está disponível nesta sessão.")
            note.setWordWrap(True)
            general_layout.addWidget(note)
        general_layout.addStretch()
        tabs.addTab(general_page, "Geral")

        mix_page = QWidget()
        mix_layout = QVBoxLayout(mix_page)
        mix_form = QFormLayout()
        self.auto_transitions = QCheckBox(
            "Automatizar fade in, fade out e Mix com análise do áudio")
        self.auto_transitions.setChecked(auto_transitions_enabled)
        self.auto_transitions.setToolTip(
            "Analisa introdução e final das faixas em segundo plano para ajustar o Mix automaticamente.")
        mix_form.addRow("Transições", self.auto_transitions)
        self.fade_in = QSpinBox()
        self.fade_in.setRange(0, 30000)
        self.fade_in.setSingleStep(500)
        self.fade_in.setSuffix(" ms")
        self.fade_in.setValue(fade_in_ms)
        self.fade_out = QSpinBox()
        self.fade_out.setRange(0, 30000)
        self.fade_out.setSingleStep(500)
        self.fade_out.setSuffix(" ms")
        self.fade_out.setValue(fade_out_ms)
        self.mix_duration = QSpinBox()
        self.mix_duration.setRange(0, 30000)
        self.mix_duration.setSingleStep(500)
        self.mix_duration.setSuffix(" ms")
        self.mix_duration.setValue(mix_ms)
        self.trim_silence = QCheckBox("Antecipar a próxima faixa ao detectar silêncio no final")
        self.trim_silence.setChecked(trim_silence)
        self.silence_threshold = QDoubleSpinBox()
        self.silence_threshold.setRange(-80.0, -10.0)
        self.silence_threshold.setSuffix(" dB")
        self.silence_threshold.setValue(silence_threshold_db)
        self.silence_hold = QSpinBox()
        self.silence_hold.setRange(200, 10000)
        self.silence_hold.setSingleStep(100)
        self.silence_hold.setSuffix(" ms")
        self.silence_hold.setValue(silence_hold_ms)
        mix_form.addRow("Fade in por faixa", self.fade_in)
        mix_form.addRow("Fade out ao parar ou avançar", self.fade_out)
        mix_form.addRow("Mix / sobreposição ou limite máximo", self.mix_duration)
        mix_form.addRow("Corte de silêncio", self.trim_silence)
        mix_form.addRow("Limite de silêncio", self.silence_threshold)
        mix_form.addRow("Silêncio mínimo detectado", self.silence_hold)
        mix_layout.addLayout(mix_form)
        mix_help = QLabel(
            "No modo automático, o RadioStudio analisa a energia e os trechos silenciosos "
            "da introdução e do final, escolhe a duração da sobreposição e aplica uma curva "
            "de potência constante. O limite do Mix vale como teto (0 = até 8 s); até 1,2 s "
            "de silêncio inicial podem ser pulados. A análise roda em segundo plano e usa "
            "uma transição segura se não estiver disponível. Sem o modo automático, os tempos "
            "acima continuam fixos."
        )
        mix_help.setWordWrap(True)
        mix_layout.addWidget(mix_help)
        mix_layout.addStretch()
        tabs.addTab(mix_page, "Fades e Mix")

        audio_page = QWidget()
        audio_layout = QVBoxLayout(audio_page)
        title = QLabel("Dispositivos de áudio")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        audio_layout.addWidget(title)
        form = QFormLayout()
        self.main_device = QComboBox()
        self.preview_device = QComboBox()
        devices = QMediaDevices.audioOutputs()
        for combo, key in ((self.main_device, main_key), (self.preview_device, preview_key)):
            combo.addItem("Padrão do Windows", "")
            for device in devices:
                combo.addItem(device.description(), device_key(device))
            selected = combo.findData(key)
            combo.setCurrentIndex(selected if selected >= 0 else 0)
        form.addRow("Reprodução principal", self.main_device)
        form.addRow("Escuta / pré-escuta", self.preview_device)
        self.local_master = QCheckBox("Ouvir o áudio master neste computador")
        self.local_master.setChecked(local_master_enabled)
        form.addRow("Saída local", self.local_master)
        audio_layout.addLayout(form)
        description = QLabel(
            "Ao desativar a saída local, a playlist e o cart wall continuam na transmissão. "
            "A pré-escuta permanece disponível no dispositivo de escuta e não entra na transmissão."
        )
        description.setWordWrap(True)
        audio_layout.addWidget(description)
        if not devices:
            audio_layout.addWidget(QLabel("Nenhum dispositivo de saída foi encontrado pelo sistema."))
        audio_layout.addStretch()
        tabs.addTab(audio_page, "Áudio")

        input_page = QWidget()
        input_layout = QVBoxLayout(input_page)
        microphone_group = QGroupBox("Microfone")
        microphone_form = QFormLayout(microphone_group)
        self.input_devices = {}
        input_devices = QMediaDevices.audioInputs()
        saved_inputs = input_device_keys if isinstance(input_device_keys, dict) else {}
        for channel in INPUT_CHANNELS:
            combo = QComboBox()
            combo.addItem("Dispositivo padrão do Windows", "")
            for device in input_devices:
                combo.addItem(device.description(), audio_device_key(device))
            saved_key = str(saved_inputs.get(channel, "") or "")
            selected = combo.findData(saved_key)
            combo.setCurrentIndex(selected if selected >= 0 else 0)
            self.input_devices[channel] = combo
        microphone_form.addRow("Dispositivo", self.input_devices["microphone"])
        self.mic_music_level = QSpinBox()
        self.mic_music_level.setRange(0, 100)
        self.mic_music_level.setSuffix(" %")
        self.mic_music_level.setValue(max(0, min(100, int(mic_music_level_percent))))
        microphone_form.addRow("Nível da música com microfone ativo", self.mic_music_level)
        self.dynamic_mic_duck = QCheckBox("Abaixar mais a música enquanto houver fala")
        self.dynamic_mic_duck.setChecked(dynamic_mic_duck)
        microphone_form.addRow("Detecção de fala", self.dynamic_mic_duck)
        self.speech_music_level = QSpinBox()
        self.speech_music_level.setRange(0, 100)
        self.speech_music_level.setSuffix(" %")
        self.speech_music_level.setValue(max(0, min(100, int(speech_music_level_percent))))
        microphone_form.addRow("Nível da música durante a fala", self.speech_music_level)
        self.speech_threshold = QDoubleSpinBox()
        self.speech_threshold.setRange(-80.0, -10.0)
        self.speech_threshold.setSuffix(" dBFS")
        self.speech_threshold.setValue(max(-80.0, min(-10.0, float(speech_threshold_db))))
        microphone_form.addRow("Limite para detectar fala", self.speech_threshold)
        self.speech_music_level.setEnabled(dynamic_mic_duck)
        self.speech_threshold.setEnabled(dynamic_mic_duck)
        self.dynamic_mic_duck.toggled.connect(self.speech_music_level.setEnabled)
        self.dynamic_mic_duck.toggled.connect(self.speech_threshold.setEnabled)
        input_layout.addWidget(microphone_group)

        auxiliary_group = QGroupBox("Auxiliares")
        auxiliary_form = QFormLayout(auxiliary_group)
        for channel in INPUT_CHANNELS[1:]:
            auxiliary_form.addRow(INPUT_LABELS[channel], self.input_devices[channel])
        input_layout.addWidget(auxiliary_group)
        input_note = QLabel(
            "O microfone e até cinco auxiliares podem ser ativados no painel 🎙 Entradas. "
            "Os auxiliares entram no master sem abaixar a música. Dispositivos ocupados ou "
            "incompatíveis são sinalizados no painel sem interromper o programa.")
        input_note.setWordWrap(True)
        input_layout.addWidget(input_note)
        if not input_devices:
            input_layout.addWidget(QLabel("Nenhum dispositivo de entrada foi encontrado pelo sistema."))
        input_layout.addStretch()
        tabs.addTab(input_page, "Entradas")

        metadata_page = QWidget()
        metadata_layout = QVBoxLayout(metadata_page)
        metadata_title = QLabel("Metadados da estação e da transmissão")
        metadata_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        metadata_layout.addWidget(metadata_title)
        metadata_form = QFormLayout()
        values = {**DEFAULT_STATION_METADATA, **station_metadata}
        self.station_name = QLineEdit(values["name"])
        self.station_description = QLineEdit(values["description"])
        self.station_genre = QLineEdit(values["genre"])
        self.station_url = QLineEdit(values["url"])
        self.title_format = QLineEdit(values["title_format"])
        metadata_form.addRow("Nome da estação", self.station_name)
        metadata_form.addRow("Descrição", self.station_description)
        metadata_form.addRow("Gênero", self.station_genre)
        metadata_form.addRow("Site/URL", self.station_url)
        metadata_form.addRow("Título no ar", self.title_format)
        metadata_layout.addLayout(metadata_form)
        placeholder_help = QLabel("Placeholders: " + ", ".join("%" + name for name in PLACEHOLDERS))
        placeholder_help.setWordWrap(True)
        metadata_layout.addWidget(placeholder_help)
        self.preview_track = current_track or TrackMetadata(
            path="Exemplo.mp3", title="Título", artist="Artista", album="Álbum",
            genre="Pop", year="2026", track="1", duration=215)
        self.title_preview = QLabel()
        self.title_preview.setWordWrap(True)
        metadata_layout.addWidget(self.title_preview)
        self.title_format.textChanged.connect(self.update_title_preview)
        self.station_name.textChanged.connect(self.update_title_preview)
        self.update_title_preview()
        metadata_layout.addStretch()
        tabs.addTab(metadata_page, "Metadados")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def station_values(self) -> dict:
        return {"name": self.station_name.text().strip(),
                "description": self.station_description.text().strip(),
                "genre": self.station_genre.text().strip(),
                "url": self.station_url.text().strip(),
                "title_format": self.title_format.text().strip()}

    @guarded
    def update_title_preview(self):
        metadata = self.station_values()
        result = format_broadcast_title(metadata["title_format"], self.preview_track, metadata)
        self.title_preview.setText("Prévia: " + result)

    @guarded
    def accept(self):
        metadata = self.station_values()
        if not metadata["name"] or not metadata["title_format"]:
            QMessageBox.warning(self, "Metadados", "Informe o nome da estação e o formato do título.")
            return
        invalid = unknown_placeholders(metadata["title_format"])
        if invalid:
            QMessageBox.warning(self, "Placeholders", "Placeholders desconhecidos: " + ", ".join("%" + value for value in invalid))
            return
        super().accept()

    def values(self) -> tuple:
        return (self.main_device.currentData(), self.preview_device.currentData(),
                self.local_master.isChecked(), self.station_values(),
                self.minimize_to_tray.isChecked(), self.resume_on_startup.isChecked(),
                self.player_controls_position.currentData(), self.waveform_style.currentData(),
                self.auto_start_broadcast.isChecked(), self.auto_transitions.isChecked(),
                self.fade_in.value(), self.fade_out.value(),
                self.mix_duration.value(), self.trim_silence.isChecked(),
                self.silence_threshold.value(), self.silence_hold.value(),
                {channel: combo.currentData() for channel, combo in self.input_devices.items()},
                self.mic_music_level.value(), self.dynamic_mic_duck.isChecked(),
                self.speech_music_level.value(), self.speech_threshold.value())
