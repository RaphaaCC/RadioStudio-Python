"""Application settings dialog and audio-output device selection."""

from __future__ import annotations

from ..runtime_safety import guarded

from PySide6.QtMultimedia import QAudioDevice, QMediaDevices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QDoubleSpinBox, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget, QScrollArea,
)

from ..config import DISCORD_APPLICATION_ID
from ..services.track_metadata import (DEFAULT_STATION_METADATA, PLACEHOLDERS, TrackMetadata,
                            format_broadcast_title, unknown_placeholders)
from .studio_widgets import WAVEFORM_STYLES
from ..services.audio_inputs import INPUT_CHANNELS, INPUT_LABELS, audio_device_key
from ..services.process_priority import PROCESS_PRIORITY_OPTIONS
from ..services.discord_presence import (
    ACTIVITY_TYPES, STATUS_DISPLAY_TYPES, unknown_presence_placeholders,
    validate_public_cover_url, validate_public_radio_url,
)
from .icons import apply_app_icon


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
                 waveform_style: str = "bars", waveform_visualizers_enabled: bool = True,
                 auto_start_broadcast: bool = False,
                 auto_transitions_enabled: bool = False,
                 fade_in_ms: int = 0, fade_out_ms: int = 0, mix_ms: int = 0,
                 trim_silence: bool = False, silence_threshold_db: float = -42,
                 silence_hold_ms: int = 1200,
                 input_device_keys: dict | None = None,
                 mic_music_level_percent: int = 35, dynamic_mic_duck: bool = False,
                 speech_music_level_percent: int = 20, speech_threshold_db: float = -38,
                 discord_client_id: str = "", discord_enabled: bool = False,
                 discord_share_track: bool = True, discord_public_url: str = "",
                 discord_use_track_cover: bool = False, discord_cover_url: str = "",
                 discord_large_image: str = "radiostudio", discord_service=None,
                 discord_activity_name: str = "RadioStudio Python Sirius",
                 discord_activity_type: int = 2, discord_status_display_type: int = 0,
                 discord_details_template: str = "🎵 %title",
                 discord_state_template: str = "%artist · %station · %status",
                 discord_large_text: str = "RadioStudio Python Sirius",
                 discord_button1_label: str = "Ouvir a rádio",
                 discord_button2_label: str = "", discord_button2_url: str = "",
                 process_priority: str = "normal"):
        super().__init__(parent)
        apply_app_icon(self)
        self.setWindowTitle("Configurações")
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
        self.process_priority = QComboBox()
        for label, value in PROCESS_PRIORITY_OPTIONS:
            self.process_priority.addItem(label, value)
        selected_priority = self.process_priority.findData(process_priority)
        if selected_priority < 0:
            selected_priority = self.process_priority.findData("normal")
        self.process_priority.setCurrentIndex(selected_priority)
        self.process_priority.setToolTip(
            "Altera a prioridade de agendamento de CPU do processo no Windows.")
        general_form.addRow("Prioridade do processo", self.process_priority)
        self.process_priority_warning = QLabel(
            "⚠️ Tempo real pode deixar o Windows ou outros programas sem resposta. "
            "Use somente se necessário.")
        self.process_priority_warning.setWordWrap(True)
        self.process_priority_warning.setVisible(
            self.process_priority.currentData() == "realtime")
        self.process_priority.currentIndexChanged.connect(
            lambda _index: self.process_priority_warning.setVisible(
                self.process_priority.currentData() == "realtime"))
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
        self.waveform_visualizers_enabled = QCheckBox("Ativar visualizadores de onda")
        self.waveform_visualizers_enabled.setChecked(waveform_visualizers_enabled)
        self.waveform_visualizers_enabled.toggled.connect(self.waveform_style.setEnabled)
        self.waveform_style.setEnabled(waveform_visualizers_enabled)
        general_form.addRow("Exibição", self.waveform_visualizers_enabled)
        general_layout.addLayout(general_form)
        general_layout.addWidget(self.process_priority_warning)
        waveform_warning = QLabel(
            "⚠️ Visualizadores animados ou dinâmicos podem aumentar o uso de recursos e "
            "comprometer o desempenho em computadores mais fracos ou sensíveis.")
        waveform_warning.setWordWrap(True)
        general_layout.addWidget(waveform_warning)
        if not tray_available:
            note = QLabel("A bandeja do sistema não está disponível nesta sessão.")
            note.setWordWrap(True)
            general_layout.addWidget(note)
        general_layout.addStretch()
        tabs.addTab(general_page, "Geral")

        discord_page = QWidget()
        discord_page_layout = QVBoxLayout(discord_page)
        discord_scroll = QScrollArea()
        discord_scroll.setWidgetResizable(True)
        discord_content = QWidget()
        discord_layout = QVBoxLayout(discord_content)
        discord_intro = QLabel(
            "Conecte o aplicativo Discord instalado neste computador para mostrar a atividade "
            "do RadioStudio no seu perfil. O aplicativo já vem configurado; você não precisa "
            "criar nada no Developer Portal.")
        discord_intro.setWordWrap(True)
        discord_layout.addWidget(discord_intro)
        discord_form = QFormLayout()
        self.discord_app_id = QLineEdit(DISCORD_APPLICATION_ID)
        self.discord_app_id.setReadOnly(True)
        self.discord_app_id.setToolTip("ID oficial do aplicativo RadioStudio Python Sirius")
        discord_form.addRow("ID do aplicativo", self.discord_app_id)
        discord_layout.addLayout(discord_form)
        connection_row = QHBoxLayout()
        self.discord_verify = QPushButton("🔌 Verificar conexão")
        self.discord_status = QLabel("Abra o Discord e verifique a conexão.")
        self.discord_status.setWordWrap(True)
        connection_row.addWidget(self.discord_verify)
        connection_row.addWidget(self.discord_status, 1)
        discord_layout.addLayout(connection_row)
        self.discord_disable_offline = QPushButton("⏹ Desativar Rich Presence enquanto desconectado")
        self.discord_disable_offline.clicked.connect(self.disable_discord_while_offline)
        self.discord_disable_offline.hide()
        discord_layout.addWidget(self.discord_disable_offline)

        self.discord_options = QGroupBox("Rich Presence")
        discord_options_layout = QVBoxLayout(self.discord_options)
        self.discord_enabled = QCheckBox("Ativar Rich Presence do RadioStudio")
        self.discord_enabled.setChecked(discord_enabled)
        discord_options_layout.addWidget(self.discord_enabled)

        discord_activity_group = QGroupBox("Atividade")
        discord_activity_form = QFormLayout(discord_activity_group)
        self.discord_activity_name = QLineEdit(str(discord_activity_name or "")[:128])
        self.discord_activity_name.setMaxLength(128)
        self.discord_activity_name.setPlaceholderText("RadioStudio Python Sirius")
        discord_activity_form.addRow("Nome exibido", self.discord_activity_name)
        self.discord_activity_type = QComboBox()
        for label, value in (("Ouvindo", 2), ("Jogando", 0),
                             ("Assistindo", 3), ("Competindo", 5)):
            if value in ACTIVITY_TYPES:
                self.discord_activity_type.addItem(label, value)
        selected_type = self.discord_activity_type.findData(discord_activity_type)
        self.discord_activity_type.setCurrentIndex(max(0, selected_type))
        discord_activity_form.addRow("Tipo", self.discord_activity_type)
        self.discord_status_display_type = QComboBox()
        for label, value in (("Nome do app", 0), ("Estado", 1), ("Detalhes", 2)):
            if value in STATUS_DISPLAY_TYPES:
                self.discord_status_display_type.addItem(label, value)
        selected_display = self.discord_status_display_type.findData(
            discord_status_display_type)
        self.discord_status_display_type.setCurrentIndex(max(0, selected_display))
        discord_activity_form.addRow("Texto em destaque", self.discord_status_display_type)
        self.discord_share_track = QCheckBox("Compartilhar dados da faixa atual")
        self.discord_share_track.setChecked(discord_share_track)
        discord_activity_form.addRow("Privacidade", self.discord_share_track)
        self.discord_use_track_cover = QCheckBox("Exibir a capa da faixa atual")
        self.discord_use_track_cover.setChecked(discord_use_track_cover)
        discord_activity_form.addRow("Imagem", self.discord_use_track_cover)
        self.discord_cover_url = QLineEdit(str(discord_cover_url or ""))
        self.discord_cover_url.setPlaceholderText("https://radio.example/cover")
        self.discord_cover_url.setMaxLength(230)
        discord_activity_form.addRow("URL pública da capa", self.discord_cover_url)
        self.discord_use_track_cover.toggled.connect(self.update_discord_cover_url_enabled)
        self.discord_share_track.toggled.connect(self.update_discord_cover_url_enabled)
        self.discord_details_template = QLineEdit(str(discord_details_template or "")[:128])
        self.discord_details_template.setMaxLength(128)
        self.discord_details_template.setPlaceholderText("🎵 %title")
        discord_activity_form.addRow("Linha principal", self.discord_details_template)
        self.discord_state_template = QLineEdit(str(discord_state_template or "")[:128])
        self.discord_state_template.setMaxLength(128)
        self.discord_state_template.setPlaceholderText("%artist · %station · %status")
        discord_activity_form.addRow("Linha secundária", self.discord_state_template)
        self.discord_large_text = QLineEdit(str(discord_large_text or "")[:128])
        self.discord_large_text.setMaxLength(128)
        self.discord_large_text.setPlaceholderText("RadioStudio Python Sirius")
        discord_activity_form.addRow("Texto ao passar sobre a marca", self.discord_large_text)
        discord_options_layout.addWidget(discord_activity_group)

        self.discord_buttons_group = QGroupBox("Botões do perfil (até 2)")
        discord_buttons_form = QFormLayout(self.discord_buttons_group)
        self.discord_button1_label = QLineEdit(str(discord_button1_label or "")[:32])
        self.discord_button1_label.setMaxLength(32)
        self.discord_button1_label.setPlaceholderText("Ouvir a rádio")
        discord_buttons_form.addRow("Texto do botão 1", self.discord_button1_label)
        self.discord_public_url = QLineEdit(str(discord_public_url or ""))
        self.discord_public_url.setPlaceholderText("https://sua-radio.example/live")
        self.discord_public_url.setMaxLength(2048)
        discord_buttons_form.addRow("Link do botão 1", self.discord_public_url)
        self.discord_button2_label = QLineEdit(str(discord_button2_label or "")[:32])
        self.discord_button2_label.setMaxLength(32)
        self.discord_button2_label.setPlaceholderText("Site da rádio")
        discord_buttons_form.addRow("Texto do botão 2", self.discord_button2_label)
        self.discord_button2_url = QLineEdit(str(discord_button2_url or ""))
        self.discord_button2_url.setPlaceholderText("https://sua-radio.example")
        self.discord_button2_url.setMaxLength(2048)
        discord_buttons_form.addRow("Link do botão 2", self.discord_button2_url)
        discord_options_layout.addWidget(self.discord_buttons_group)
        template_help = QLabel(
            "Use %title, %artist, %album, %station e %status nas linhas. "
            "Desmarque a opção de privacidade para ocultar os dados da faixa.")
        template_help.setWordWrap(True)
        discord_options_layout.addWidget(template_help)
        discord_privacy_note = QLabel(
            "Os botões aparecem para outras pessoas, mas o Discord não os mostra no seu próprio "
            "perfil; teste com outra conta. Somente links públicos HTTP(S) são aceitos. "
            "A marca do RadioStudio é configurada pelo aplicativo. Para exibir capas, informe "
            "uma URL pública que sirva a capa atual; o Discord não consegue buscar arquivos "
            "locais nem endereços da rede privada. Se usar o servidor embutido, habilite a "
            "opção de capa da transmissão e publique o endpoint /cover em um domínio acessível.")
        discord_privacy_note.setWordWrap(True)
        discord_options_layout.addWidget(discord_privacy_note)
        self.discord_options.hide()
        discord_layout.addWidget(self.discord_options)
        discord_layout.addStretch()
        discord_scroll.setWidget(discord_content)
        discord_page_layout.addWidget(discord_scroll)
        tabs.addTab(discord_page, "Discord")
        self._discord_service = discord_service
        self._initial_discord_app_id = DISCORD_APPLICATION_ID
        self.discord_verify.clicked.connect(self.verify_discord_connection)
        self.discord_enabled.toggled.connect(self.set_discord_controls_enabled)
        self.discord_enabled.toggled.connect(self.update_discord_offline_action)
        self.set_discord_controls_enabled(discord_enabled)
        if discord_service is not None:
            discord_service.availability_changed.connect(self.set_discord_status)
            if (discord_service.available
                    and discord_service.client_id == self._initial_discord_app_id):
                self.set_discord_status(True, discord_service.status)
            else:
                self.set_discord_status(False, discord_service.status)

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

    def verify_discord_connection(self) -> None:
        if self._discord_service is None:
            self.set_discord_status(False, "O serviço de Rich Presence não está disponível.")
            return
        self.discord_verify.setEnabled(False)
        self.discord_verify.setText("Verificando…")
        self._discord_service.configure(DISCORD_APPLICATION_ID, enabled=False)

    def set_discord_status(self, available: bool, status: str) -> None:
        same_client = bool(self._discord_service
                           and self._discord_service.client_id == DISCORD_APPLICATION_ID)
        verified = bool(available and same_client)
        self.discord_options.setVisible(verified)
        self.discord_disable_offline.setVisible(not verified and self.discord_enabled.isChecked())
        if verified:
            self.discord_status.setText("✅ " + status)
        else:
            self.discord_status.setText(status)
        checking = not available and status.startswith("Verificando")
        self.discord_verify.setEnabled(not checking)
        self.discord_verify.setText("⏳ Verificando…" if checking else "🔌 Verificar conexão")

    def set_discord_controls_enabled(self, enabled: bool) -> None:
        for widget in (
                self.discord_share_track, self.discord_use_track_cover,
                self.discord_activity_name,
                self.discord_activity_type, self.discord_status_display_type,
                self.discord_details_template, self.discord_state_template,
                self.discord_large_text, self.discord_button1_label,
                self.discord_public_url, self.discord_button2_label,
                self.discord_button2_url, self.discord_cover_url):
            widget.setEnabled(enabled)
        self.update_discord_cover_url_enabled()

    def update_discord_cover_url_enabled(self, _checked: bool | None = None) -> None:
        can_share_track = (self.discord_enabled.isChecked()
                           and self.discord_share_track.isChecked())
        self.discord_use_track_cover.setEnabled(can_share_track)
        self.discord_cover_url.setEnabled(
            can_share_track and self.discord_use_track_cover.isChecked())

    def update_discord_offline_action(self, _enabled: bool) -> None:
        connected = bool(self._discord_service and self._discord_service.available
                         and self._discord_service.client_id == DISCORD_APPLICATION_ID)
        self.discord_disable_offline.setVisible(not connected and self.discord_enabled.isChecked())

    def disable_discord_while_offline(self) -> None:
        self.discord_enabled.setChecked(False)
        self.discord_status.setText("Rich Presence desativada.")

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
        try:
            validate_public_radio_url(self.discord_public_url.text())
            validate_public_radio_url(self.discord_button2_url.text())
            cover_url = validate_public_cover_url(self.discord_cover_url.text())
            if (self.discord_enabled.isChecked() and self.discord_options.isVisible()
                    and self.discord_share_track.isChecked()
                    and self.discord_use_track_cover.isChecked() and not cover_url):
                raise ValueError("Informe a URL pública que fornece a capa atual.")
        except ValueError as exc:
            QMessageBox.warning(self, "Link público", str(exc))
            return
        invalid_presence_tokens = sorted(set(
            unknown_presence_placeholders(self.discord_details_template.text())
            + unknown_presence_placeholders(self.discord_state_template.text())))
        if invalid_presence_tokens:
            QMessageBox.warning(
                self, "Rich Presence", "Placeholders desconhecidos: "
                + ", ".join("%" + token for token in invalid_presence_tokens))
            return
        if self.discord_enabled.isChecked() and self.discord_options.isVisible():
            if not self._discord_service or not self._discord_service.available \
                    or self._discord_service.client_id != DISCORD_APPLICATION_ID:
                QMessageBox.warning(
                    self, "Discord", "Verifique a conexão com o Discord antes de ativar a Rich Presence.")
                return
        super().accept()

    def values(self) -> tuple:
        return (self.main_device.currentData(), self.preview_device.currentData(),
                self.local_master.isChecked(), self.station_values(),
                self.minimize_to_tray.isChecked(), self.resume_on_startup.isChecked(),
                self.player_controls_position.currentData(), self.waveform_style.currentData(),
                self.waveform_visualizers_enabled.isChecked(),
                self.auto_start_broadcast.isChecked(), self.auto_transitions.isChecked(),
                self.fade_in.value(), self.fade_out.value(),
                self.mix_duration.value(), self.trim_silence.isChecked(),
                self.silence_threshold.value(), self.silence_hold.value(),
                {channel: combo.currentData() for channel, combo in self.input_devices.items()},
                self.mic_music_level.value(), self.dynamic_mic_duck.isChecked(),
                self.speech_music_level.value(), self.speech_threshold.value(),
                DISCORD_APPLICATION_ID, self.discord_enabled.isChecked(),
                self.discord_share_track.isChecked(),
                self.discord_use_track_cover.isChecked(), self.discord_cover_url.text().strip(),
                self.discord_public_url.text().strip(),
                "radiostudio", self.discord_activity_name.text().strip(),
                self.discord_activity_type.currentData(),
                self.discord_status_display_type.currentData(),
                self.discord_details_template.text().strip(),
                self.discord_state_template.text().strip(),
                self.discord_large_text.text().strip(),
                self.discord_button1_label.text().strip(),
                self.discord_button2_label.text().strip(),
                self.discord_button2_url.text().strip(),
                self.process_priority.currentData())
