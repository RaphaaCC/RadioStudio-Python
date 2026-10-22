"""Application settings dialog and audio-output device selection."""

from __future__ import annotations

import secrets

from ..runtime_safety import guarded

from PySide6.QtMultimedia import QAudioDevice, QMediaDevices
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QDoubleSpinBox, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget, QScrollArea,
)

from ..config import DISCORD_APPLICATION_ID
from ..i18n import LANGUAGE_CHOICES, translate, translate_dialog_buttons
from .message_boxes import show_warning
from ..services.track_metadata import (DEFAULT_STATION_METADATA, PLACEHOLDERS, TrackMetadata,
                            format_broadcast_title, unknown_placeholders)
from .studio_widgets import WAVEFORM_STYLES
from ..services.audio_inputs import INPUT_CHANNELS, INPUT_LABELS, audio_device_key
from ..services.process_priority import PROCESS_PRIORITY_OPTIONS
from ..services.control_api import API_DEFAULT_PORT, api_connection_addresses, parse_allowed_origins
from ..services.discord_presence import (
    ACTIVITY_TYPES, STATUS_DISPLAY_TYPES, unknown_presence_placeholders,
    validate_public_cover_url, validate_public_radio_url,
)
from .icons import apply_app_icon, translated_icon_label, ui_icon


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
                 process_priority: str = "normal", backup_directory: str = "",
                 backup_periodic_enabled: bool = False,
                 backup_interval_seconds: int = 86400, backup_retention: int = 10,
                 backup_create_callback=None, backup_import_callback=None,
                 database_repair_callback=None, metadata_update_mode: str = "current",
                 metadata_update_interval_seconds: int = 30,
                 language_preference: str = "system",
                 control_api_config: dict | None = None,
                 control_api_username: str = "",
                 control_api_password_configured: bool = False,
                 control_api_token: str = ""):
        super().__init__(parent)
        apply_app_icon(self)
        self.setWindowTitle(translate("Configurações"))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        general_page = QWidget()
        general_layout = QVBoxLayout(general_page)

        startup_group = QGroupBox(translate("Inicialização"))
        startup_layout = QVBoxLayout(startup_group)
        self.minimize_to_tray = QCheckBox(translate("Ao fechar a janela, continuar na bandeja do Windows"))
        self.minimize_to_tray.setChecked(minimize_to_tray)
        self.minimize_to_tray.setEnabled(tray_available)
        startup_layout.addWidget(self.minimize_to_tray)
        self.resume_on_startup = QCheckBox(
            translate("Ao abrir, retomar a música que estava tocando ao sair"))
        self.resume_on_startup.setChecked(resume_on_startup)
        startup_layout.addWidget(self.resume_on_startup)
        self.auto_start_broadcast = QCheckBox(
            translate("Iniciar transmissão ao abrir se estava ativa ao sair"))
        self.auto_start_broadcast.setChecked(auto_start_broadcast)
        startup_layout.addWidget(self.auto_start_broadcast)
        if not tray_available:
            tray_note = QLabel(translate("A bandeja do sistema não está disponível nesta sessão."))
            tray_note.setWordWrap(True)
            startup_layout.addWidget(tray_note)
        general_layout.addWidget(startup_group)

        performance_group = QGroupBox(translate("Desempenho"))
        performance_layout = QVBoxLayout(performance_group)
        performance_form = QFormLayout()
        self.process_priority = QComboBox()
        for label, value in PROCESS_PRIORITY_OPTIONS:
            self.process_priority.addItem(translate(label), value)
        selected_priority = self.process_priority.findData(process_priority)
        if selected_priority < 0:
            selected_priority = self.process_priority.findData("normal")
        self.process_priority.setCurrentIndex(selected_priority)
        self.process_priority.setToolTip(
            translate("Altera a prioridade de agendamento de CPU do processo no Windows."))
        performance_form.addRow(translate("Prioridade do processo"), self.process_priority)
        performance_layout.addLayout(performance_form)
        self.process_priority_warning = QLabel(
            translate("⚠️ Tempo real pode deixar o Windows ou outros programas sem resposta. "
            "Use somente se necessário."))
        self.process_priority_warning.setWordWrap(True)
        self.process_priority_warning.setVisible(
            self.process_priority.currentData() == "realtime")
        self.process_priority.currentIndexChanged.connect(
            lambda _index: self.process_priority_warning.setVisible(
                self.process_priority.currentData() == "realtime"))
        performance_layout.addWidget(self.process_priority_warning)

        language_group = QGroupBox(translate("Idioma"))
        language_layout = QVBoxLayout(language_group)
        self.language_preference = QComboBox()
        for label, value in LANGUAGE_CHOICES:
            self.language_preference.addItem(translate(label), value)
        language_index = self.language_preference.findData(language_preference)
        self.language_preference.setCurrentIndex(max(0, language_index))
        language_layout.addWidget(self.language_preference)
        language_note = QLabel(
            translate("Ao alterar o idioma, o programa pedirá confirmação e reiniciará para aplicar a mudança."))
        language_note.setWordWrap(True)
        language_layout.addWidget(language_note)

        interface_group = QGroupBox(translate("Interface"))
        interface_layout = QVBoxLayout(interface_group)
        interface_form = QFormLayout()
        self.player_controls_position = QComboBox()
        self.player_controls_position.addItem(translate("Abaixo da playlist"), "bottom")
        self.player_controls_position.addItem(translate("No topo, abaixo de No ar"), "top")
        selected_position = self.player_controls_position.findData(player_controls_position)
        self.player_controls_position.setCurrentIndex(max(0, selected_position))
        interface_form.addRow(translate("Controles do player"), self.player_controls_position)
        self.waveform_style = QComboBox()
        for label, value in WAVEFORM_STYLES:
            self.waveform_style.addItem(translate(label), value)
        self.waveform_style.setToolTip(
            translate("A onda reativa acompanha o sinal ao vivo; os modos de espectro mostram as frequências do áudio."))
        selected_style = self.waveform_style.findData(waveform_style)
        self.waveform_style.setCurrentIndex(max(0, selected_style))
        interface_form.addRow(translate("Visualizador de ondas"), self.waveform_style)
        self.waveform_visualizers_enabled = QCheckBox(translate("Ativar visualizadores de onda"))
        self.waveform_visualizers_enabled.setChecked(waveform_visualizers_enabled)
        self.waveform_visualizers_enabled.toggled.connect(self.waveform_style.setEnabled)
        self.waveform_style.setEnabled(waveform_visualizers_enabled)
        interface_form.addRow(translate("Exibição"), self.waveform_visualizers_enabled)
        interface_layout.addLayout(interface_form)
        waveform_warning = QLabel(
            translate("⚠️ Visualizadores animados ou dinâmicos podem aumentar o uso de recursos e "
            "comprometer o desempenho em computadores mais fracos ou sensíveis."))
        waveform_warning.setWordWrap(True)
        interface_layout.addWidget(waveform_warning)
        general_layout.addWidget(interface_group)
        general_layout.addWidget(performance_group)
        general_layout.addWidget(language_group)
        general_layout.addStretch()
        tabs.addTab(general_page, translate("Geral"))

        api_page = QWidget()
        api_layout = QVBoxLayout(api_page)
        api_config = dict(control_api_config or {})
        self._api_initial_username = str(control_api_username or "")
        self._api_password_configured = bool(control_api_password_configured)
        self._api_token_changed = False
        api_intro = QLabel(translate(
            "Controle a reprodução e a fila por HTTP a partir de aplicativos, scripts ou painéis web na rede local."))
        api_intro.setWordWrap(True)
        api_layout.addWidget(api_intro)

        self.control_api_enabled = QCheckBox(translate("Ativar API REST"))
        self.control_api_enabled.setChecked(bool(api_config.get("enabled", False)))
        api_layout.addWidget(self.control_api_enabled)

        api_form = QFormLayout()

        self.control_api_port = QSpinBox()
        self.control_api_port.setRange(1, 65535)
        try:
            api_port = int(api_config.get("port", API_DEFAULT_PORT))
        except (TypeError, ValueError, OverflowError):
            api_port = API_DEFAULT_PORT
        self.control_api_port.setValue(min(65535, max(1, api_port)))
        api_form.addRow(translate("Porta"), self.control_api_port)

        self.control_api_auth_mode = QComboBox()
        self.control_api_auth_mode.addItem(translate("Token automático"), "token")
        self.control_api_auth_mode.addItem(translate("Login e senha"), "basic")
        auth_mode = str(api_config.get("auth_mode", "token"))
        auth_index = self.control_api_auth_mode.findData(auth_mode)
        self.control_api_auth_mode.setCurrentIndex(auth_index if auth_index >= 0 else 0)
        api_form.addRow(translate("Autenticação"), self.control_api_auth_mode)

        self.control_api_username = QLineEdit(self._api_initial_username)
        self.control_api_username.setMaxLength(128)
        api_form.addRow(translate("Nome de usuário"), self.control_api_username)

        self.control_api_password = QLineEdit()
        self.control_api_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.control_api_password.setPlaceholderText(
            translate("Deixe vazio para manter a senha salva.")
            if self._api_password_configured else "")
        self.control_api_password.setMaxLength(512)
        api_form.addRow(translate("Senha"), self.control_api_password)

        token_row = QHBoxLayout()
        self.control_api_token = QLineEdit(str(control_api_token or ""))
        self.control_api_token.setReadOnly(True)
        self.control_api_token.setEchoMode(QLineEdit.EchoMode.Password)
        token_row.addWidget(self.control_api_token, 1)
        self.control_api_generate_token = QPushButton(translate("Gerar token"))
        self.control_api_generate_token.clicked.connect(self.generate_control_api_token)
        token_row.addWidget(self.control_api_generate_token)
        self.control_api_copy_token = QPushButton(translate("Copiar token"))
        self.control_api_copy_token.clicked.connect(self.copy_control_api_token)
        token_row.addWidget(self.control_api_copy_token)
        api_form.addRow(translate("Token de acesso"), token_row)

        self.control_api_origins = QLineEdit()
        saved_origins = api_config.get("allowed_origins", [])
        if isinstance(saved_origins, (list, tuple)):
            self.control_api_origins.setText(", ".join(str(value) for value in saved_origins))
        elif isinstance(saved_origins, str):
            self.control_api_origins.setText(saved_origins)
        self.control_api_origins.setPlaceholderText(translate("http://192.168.1.20:3000"))
        api_form.addRow(translate("Origens CORS permitidas"), self.control_api_origins)
        api_layout.addLayout(api_form)

        cors_help = QLabel(translate(
            "Para painéis web, informe as origens exatas separadas por vírgula. Deixe vazio para clientes nativos e scripts."))
        cors_help.setWordWrap(True)
        api_layout.addWidget(cors_help)
        self.control_api_http_warning = QLabel(translate(
            "⚠️ HTTP não criptografa a conexão. Login, senha, token e comandos podem ser observados na rede; use apenas em uma rede confiável."))
        self.control_api_http_warning.setWordWrap(True)
        api_layout.addWidget(self.control_api_http_warning)
        api_firewall_note = QLabel(translate(
            "O RadioStudio não altera o Firewall do Windows. Se outros dispositivos não conseguirem conectar, confira as regras de entrada da rede."))
        api_firewall_note.setWordWrap(True)
        api_layout.addWidget(api_firewall_note)
        api_addresses_label = QLabel(translate("Endereços para conexão"))
        api_layout.addWidget(api_addresses_label)
        self.control_api_addresses = QLabel()
        self.control_api_addresses.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.control_api_addresses.setWordWrap(True)
        api_layout.addWidget(self.control_api_addresses)
        self.control_api_port.valueChanged.connect(self.update_control_api_addresses)
        self.control_api_auth_mode.currentIndexChanged.connect(
            self.update_control_api_controls)
        self.control_api_enabled.toggled.connect(self.update_control_api_controls)
        self.update_control_api_addresses()
        self.update_control_api_controls()
        if (self.control_api_enabled.isChecked()
                and self.control_api_auth_mode.currentData() == "token"
                and not self.control_api_token.text()):
            self.generate_control_api_token()
        api_layout.addStretch()
        tabs.addTab(api_page, translate("API"))

        backup_page = QWidget()
        backup_layout = QVBoxLayout(backup_page)
        backup_form = QFormLayout()
        backup_folder_row = QHBoxLayout()
        self.backup_directory = QLineEdit(str(backup_directory or ""))
        self.backup_directory.setPlaceholderText(translate("Selecione a pasta para os backups"))
        self.backup_directory.setToolTip(translate("Os arquivos de backup serão salvos nesta pasta."))
        self.backup_browse = QPushButton(translate("Selecionar…"))
        self.backup_browse.clicked.connect(self.choose_backup_directory)
        backup_folder_row.addWidget(self.backup_directory, 1)
        backup_folder_row.addWidget(self.backup_browse)
        backup_form.addRow(translate("Pasta de destino"), backup_folder_row)
        self.backup_periodic_enabled = QCheckBox(translate("Criar backups automaticamente"))
        self.backup_periodic_enabled.setChecked(bool(backup_periodic_enabled))
        backup_form.addRow(translate("Backups periódicos"), self.backup_periodic_enabled)
        self.backup_interval = QComboBox()
        for label, seconds in (("A cada 1 hora", 3600), ("A cada 6 horas", 21600),
                               ("A cada 12 horas", 43200), ("A cada 24 horas", 86400),
                               ("A cada 7 dias", 604800)):
            self.backup_interval.addItem(translate(label), seconds)
        try:
            selected_interval = self.backup_interval.findData(int(backup_interval_seconds))
        except (TypeError, ValueError, OverflowError):
            selected_interval = -1
        self.backup_interval.setCurrentIndex(selected_interval if selected_interval >= 0 else 3)
        self.backup_interval.setEnabled(self.backup_periodic_enabled.isChecked())
        self.backup_periodic_enabled.toggled.connect(self.backup_interval.setEnabled)
        backup_form.addRow(translate("Frequência"), self.backup_interval)
        self.backup_retention = QSpinBox()
        self.backup_retention.setRange(1, 500)
        self.backup_retention.setSuffix(translate(" backups"))
        try:
            retention_value = int(backup_retention)
        except (TypeError, ValueError, OverflowError):
            retention_value = 10
        self.backup_retention.setValue(max(1, min(500, retention_value)))
        self.backup_retention.setToolTip(
            translate("Após cada backup, os mais antigos criados pelo RadioStudio serão removidos para manter este limite."))
        backup_form.addRow(translate("Manter os últimos"), self.backup_retention)
        backup_layout.addLayout(backup_form)
        backup_note = QLabel(
            translate("O backup inclui configurações, playlist, programação, índice e histórico das bibliotecas; "
            "os arquivos de música não são copiados. "
            "As senhas do Gerenciador de Credenciais do Windows não são exportadas. "
            "Backups periódicos são executados enquanto o RadioStudio está aberto."))
        backup_note.setWordWrap(True)
        backup_layout.addWidget(backup_note)
        backup_actions = QHBoxLayout()
        self.backup_now = QPushButton(translated_icon_label(
            "💾 Criar backup agora", "💾"))
        self.backup_now.setIcon(ui_icon("backup"))
        self.backup_import = QPushButton(translated_icon_label(
            "📥 Importar configurações", "📥"))
        self.backup_import.setIcon(ui_icon("import"))
        backup_actions.addWidget(self.backup_now)
        backup_actions.addWidget(self.backup_import)
        backup_actions.addStretch()
        backup_layout.addLayout(backup_actions)
        self.database_repair = QPushButton(translated_icon_label(
            "🛠 Verificar e reparar banco de dados", "🛠"))
        self.database_repair.setIcon(ui_icon("repair"))
        self.database_repair.setToolTip(
            translate("Verifica o índice das bibliotecas e o histórico. Se estiver corrompido, tenta restaurar "
            "um backup ou recuperar os registros legíveis e reinicia o programa."))
        backup_layout.addWidget(self.database_repair)
        initial_backup_status = (
            translate("Backups periódicos ativados. A frequência começa a contar após salvar as configurações.")
            if backup_periodic_enabled else
            translate("Backups automáticos ficam desativados até você ativá-los."))
        self.backup_status = QLabel(initial_backup_status)
        self.backup_status.setObjectName("backup_status")
        self.backup_status.setWordWrap(True)
        backup_layout.addWidget(self.backup_status)
        backup_layout.addStretch()
        self._backup_create_callback = backup_create_callback
        self._backup_import_callback = backup_import_callback
        self._database_repair_callback = database_repair_callback
        self.backup_now.clicked.connect(self.create_manual_backup)
        self.backup_import.clicked.connect(self.import_settings_backup)
        self.database_repair.clicked.connect(self.request_database_repair)
        tabs.addTab(backup_page, translate("Backup e recuperação"))

        discord_page = QWidget()
        discord_page_layout = QVBoxLayout(discord_page)
        discord_scroll = QScrollArea()
        discord_scroll.setWidgetResizable(True)
        discord_content = QWidget()
        discord_layout = QVBoxLayout(discord_content)
        discord_intro = QLabel(
            translate("Conecte o aplicativo Discord instalado neste computador para mostrar a atividade "
            "do RadioStudio no seu perfil. O aplicativo já vem configurado; você não precisa "
            "criar nada no Developer Portal."))
        discord_intro.setWordWrap(True)
        discord_layout.addWidget(discord_intro)
        discord_form = QFormLayout()
        self.discord_app_id = QLineEdit(DISCORD_APPLICATION_ID)
        self.discord_app_id.setReadOnly(True)
        self.discord_app_id.setToolTip(translate("ID oficial do aplicativo RadioStudio Python Sirius"))
        discord_form.addRow(translate("ID do aplicativo"), self.discord_app_id)
        discord_layout.addLayout(discord_form)
        connection_row = QHBoxLayout()
        self.discord_verify = QPushButton(translated_icon_label(
            "🔌 Verificar conexão", "🔌"))
        self.discord_verify.setIcon(ui_icon("connection"))
        self.discord_status = QLabel(translate("Abra o Discord e verifique a conexão."))
        self.discord_status.setWordWrap(True)
        connection_row.addWidget(self.discord_verify)
        connection_row.addWidget(self.discord_status, 1)
        discord_layout.addLayout(connection_row)
        self.discord_disable_offline = QPushButton(translated_icon_label(
            "⏹ Desativar Rich Presence enquanto desconectado", "⏹"))
        self.discord_disable_offline.setIcon(ui_icon("stop"))
        self.discord_disable_offline.clicked.connect(self.disable_discord_while_offline)
        self.discord_disable_offline.hide()
        discord_layout.addWidget(self.discord_disable_offline)

        self.discord_options = QGroupBox(translate("Rich Presence"))
        discord_options_layout = QVBoxLayout(self.discord_options)
        self.discord_enabled = QCheckBox(translate("Ativar Rich Presence do RadioStudio"))
        self.discord_enabled.setChecked(discord_enabled)
        discord_options_layout.addWidget(self.discord_enabled)

        discord_activity_group = QGroupBox(translate("Atividade"))
        discord_activity_form = QFormLayout(discord_activity_group)
        self.discord_activity_name = QLineEdit(str(discord_activity_name or "")[:128])
        self.discord_activity_name.setMaxLength(128)
        self.discord_activity_name.setPlaceholderText(translate("RadioStudio Python Sirius"))
        discord_activity_form.addRow(translate("Nome exibido"), self.discord_activity_name)
        self.discord_activity_type = QComboBox()
        for label, value in (("Ouvindo", 2), ("Jogando", 0),
                             ("Assistindo", 3), ("Competindo", 5)):
            if value in ACTIVITY_TYPES:
                self.discord_activity_type.addItem(translate(label), value)
        selected_type = self.discord_activity_type.findData(discord_activity_type)
        self.discord_activity_type.setCurrentIndex(max(0, selected_type))
        discord_activity_form.addRow(translate("Tipo"), self.discord_activity_type)
        self.discord_status_display_type = QComboBox()
        for label, value in (("Nome do app", 0), ("Estado", 1), ("Detalhes", 2)):
            if value in STATUS_DISPLAY_TYPES:
                self.discord_status_display_type.addItem(translate(label), value)
        selected_display = self.discord_status_display_type.findData(
            discord_status_display_type)
        self.discord_status_display_type.setCurrentIndex(max(0, selected_display))
        discord_activity_form.addRow(translate("Texto em destaque"), self.discord_status_display_type)
        self.discord_share_track = QCheckBox(translate("Compartilhar dados da faixa atual"))
        self.discord_share_track.setChecked(discord_share_track)
        discord_activity_form.addRow(translate("Privacidade"), self.discord_share_track)
        self.discord_use_track_cover = QCheckBox(translate("Exibir a capa da faixa atual"))
        self.discord_use_track_cover.setChecked(discord_use_track_cover)
        discord_activity_form.addRow(translate("Imagem"), self.discord_use_track_cover)
        self.discord_cover_url = QLineEdit(str(discord_cover_url or ""))
        self.discord_cover_url.setPlaceholderText(translate("https://radio.example/cover"))
        self.discord_cover_url.setMaxLength(230)
        discord_activity_form.addRow(translate("URL pública da capa"), self.discord_cover_url)
        self.discord_use_track_cover.toggled.connect(self.update_discord_cover_url_enabled)
        self.discord_share_track.toggled.connect(self.update_discord_cover_url_enabled)
        self.discord_details_template = QLineEdit(str(discord_details_template or "")[:128])
        self.discord_details_template.setMaxLength(128)
        self.discord_details_template.setPlaceholderText(translate("🎵 %title"))
        discord_activity_form.addRow(translate("Linha principal"), self.discord_details_template)
        self.discord_state_template = QLineEdit(str(discord_state_template or "")[:128])
        self.discord_state_template.setMaxLength(128)
        self.discord_state_template.setPlaceholderText(translate("%artist · %station · %status"))
        discord_activity_form.addRow(translate("Linha secundária"), self.discord_state_template)
        self.discord_large_text = QLineEdit(str(discord_large_text or "")[:128])
        self.discord_large_text.setMaxLength(128)
        self.discord_large_text.setPlaceholderText(translate("RadioStudio Python Sirius"))
        discord_activity_form.addRow(translate("Texto ao passar sobre a marca"), self.discord_large_text)
        discord_options_layout.addWidget(discord_activity_group)

        self.discord_buttons_group = QGroupBox(translate("Botões do perfil (até 2)"))
        discord_buttons_form = QFormLayout(self.discord_buttons_group)
        self.discord_button1_label = QLineEdit(str(discord_button1_label or "")[:32])
        self.discord_button1_label.setMaxLength(32)
        self.discord_button1_label.setPlaceholderText(translate("Ouvir a rádio"))
        discord_buttons_form.addRow(translate("Texto do botão 1"), self.discord_button1_label)
        self.discord_public_url = QLineEdit(str(discord_public_url or ""))
        self.discord_public_url.setPlaceholderText(translate("https://sua-radio.example/live"))
        self.discord_public_url.setMaxLength(2048)
        discord_buttons_form.addRow(translate("Link do botão 1"), self.discord_public_url)
        self.discord_button2_label = QLineEdit(str(discord_button2_label or "")[:32])
        self.discord_button2_label.setMaxLength(32)
        self.discord_button2_label.setPlaceholderText(translate("Site da rádio"))
        discord_buttons_form.addRow(translate("Texto do botão 2"), self.discord_button2_label)
        self.discord_button2_url = QLineEdit(str(discord_button2_url or ""))
        self.discord_button2_url.setPlaceholderText(translate("https://sua-radio.example"))
        self.discord_button2_url.setMaxLength(2048)
        discord_buttons_form.addRow(translate("Link do botão 2"), self.discord_button2_url)
        discord_options_layout.addWidget(self.discord_buttons_group)
        template_help = QLabel(
            translate("Use %title, %artist, %album, %station e %status nas linhas. "
            "Desmarque a opção de privacidade para ocultar os dados da faixa."))
        template_help.setWordWrap(True)
        discord_options_layout.addWidget(template_help)
        discord_privacy_note = QLabel(
            translate("Os botões aparecem para outras pessoas, mas o Discord não os mostra no seu próprio "
            "perfil; teste com outra conta. Somente links públicos HTTP(S) são aceitos. "
            "A marca do RadioStudio é configurada pelo aplicativo. Para exibir capas, informe "
            "uma URL pública que sirva a capa atual; o Discord não consegue buscar arquivos "
            "locais nem endereços da rede privada. Se usar o servidor embutido, habilite a "
            "opção de capa da transmissão e publique o endpoint /cover em um domínio acessível."))
        discord_privacy_note.setWordWrap(True)
        discord_options_layout.addWidget(discord_privacy_note)
        self.discord_options.hide()
        discord_layout.addWidget(self.discord_options)
        discord_layout.addStretch()
        discord_scroll.setWidget(discord_content)
        discord_page_layout.addWidget(discord_scroll)
        tabs.addTab(discord_page, translate("Discord"))
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
            translate("Automatizar fade in, fade out e Mix com análise do áudio"))
        self.auto_transitions.setChecked(auto_transitions_enabled)
        self.auto_transitions.setToolTip(
            translate("Analisa introdução e final das faixas em segundo plano para ajustar o Mix automaticamente."))
        mix_form.addRow(translate("Transições"), self.auto_transitions)
        self.fade_in = QSpinBox()
        self.fade_in.setRange(0, 30000)
        self.fade_in.setSingleStep(500)
        self.fade_in.setSuffix(translate(" ms"))
        self.fade_in.setValue(fade_in_ms)
        self.fade_out = QSpinBox()
        self.fade_out.setRange(0, 30000)
        self.fade_out.setSingleStep(500)
        self.fade_out.setSuffix(translate(" ms"))
        self.fade_out.setValue(fade_out_ms)
        self.mix_duration = QSpinBox()
        self.mix_duration.setRange(0, 30000)
        self.mix_duration.setSingleStep(500)
        self.mix_duration.setSuffix(translate(" ms"))
        self.mix_duration.setValue(mix_ms)
        self.trim_silence = QCheckBox(translate("Antecipar a próxima faixa ao detectar silêncio no final"))
        self.trim_silence.setChecked(trim_silence)
        self.silence_threshold = QDoubleSpinBox()
        self.silence_threshold.setRange(-80.0, -10.0)
        self.silence_threshold.setSuffix(translate(" dB"))
        self.silence_threshold.setValue(silence_threshold_db)
        self.silence_hold = QSpinBox()
        self.silence_hold.setRange(200, 10000)
        self.silence_hold.setSingleStep(100)
        self.silence_hold.setSuffix(translate(" ms"))
        self.silence_hold.setValue(silence_hold_ms)
        mix_form.addRow(translate("Fade in por faixa"), self.fade_in)
        mix_form.addRow(translate("Fade out ao parar ou avançar"), self.fade_out)
        mix_form.addRow(translate("Mix / sobreposição ou limite máximo"), self.mix_duration)
        mix_form.addRow(translate("Corte de silêncio"), self.trim_silence)
        mix_form.addRow(translate("Limite de silêncio"), self.silence_threshold)
        mix_form.addRow(translate("Silêncio mínimo detectado"), self.silence_hold)
        mix_layout.addLayout(mix_form)
        mix_help = QLabel(
            translate("No modo automático, o RadioStudio analisa a energia e os trechos silenciosos "
            "da introdução e do final, escolhe a duração da sobreposição e aplica uma curva "
            "de potência constante. O limite do Mix vale como teto (0 = até 8 s); até 1,2 s "
            "de silêncio inicial podem ser pulados. A análise roda em segundo plano e usa "
            "uma transição segura se não estiver disponível. Sem o modo automático, os tempos "
            "acima continuam fixos.")
        )
        mix_help.setWordWrap(True)
        mix_layout.addWidget(mix_help)
        mix_layout.addStretch()
        tabs.addTab(mix_page, translate("Fades e Mix"))

        audio_page = QWidget()
        audio_layout = QVBoxLayout(audio_page)
        title = QLabel(translate("Dispositivos de áudio"))
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        audio_layout.addWidget(title)
        form = QFormLayout()
        self.main_device = QComboBox()
        self.preview_device = QComboBox()
        devices = QMediaDevices.audioOutputs()
        for combo, key in ((self.main_device, main_key), (self.preview_device, preview_key)):
            combo.addItem(translate("Padrão do Windows"), "")
            for device in devices:
                combo.addItem(device.description(), device_key(device))
            selected = combo.findData(key)
            combo.setCurrentIndex(selected if selected >= 0 else 0)
        form.addRow(translate("Saída de monitoramento"), self.main_device)
        form.addRow(translate("Saída da pré-escuta"), self.preview_device)
        self.local_master = QCheckBox(translate("Ativar monitoramento neste computador"))
        self.local_master.setChecked(local_master_enabled)
        form.addRow(translate("Saída local"), self.local_master)
        audio_layout.addLayout(form)
        description = QLabel(
            translate("Master Ao Vivo é o sinal enviado à transmissão e inclui playlist, Cart Wall, "
            "auxiliares e microfone. O Monitoramento envia esse sinal à saída selecionada "
            "e tem nível próprio: reduzir o Master Ao Vivo também reduz a escuta; reduzir "
            "somente o Monitoramento não altera a transmissão. Ao desativar o "
            "monitoramento, a transmissão continua ativa. A pré-escuta permanece separada "
            "e não entra na transmissão.")
        )
        description.setWordWrap(True)
        audio_layout.addWidget(description)
        if not devices:
            audio_layout.addWidget(QLabel(translate("Nenhum dispositivo de saída foi encontrado pelo sistema.")))
        audio_layout.addStretch()
        tabs.addTab(audio_page, translate("Áudio"))

        input_page = QWidget()
        input_layout = QVBoxLayout(input_page)
        microphone_group = QGroupBox(translate("Microfone"))
        microphone_form = QFormLayout(microphone_group)
        self.input_devices = {}
        input_devices = QMediaDevices.audioInputs()
        saved_inputs = input_device_keys if isinstance(input_device_keys, dict) else {}
        for channel in INPUT_CHANNELS:
            combo = QComboBox()
            combo.addItem(translate("Dispositivo padrão do Windows"), "")
            for device in input_devices:
                combo.addItem(device.description(), audio_device_key(device))
            saved_key = str(saved_inputs.get(channel, "") or "")
            selected = combo.findData(saved_key)
            combo.setCurrentIndex(selected if selected >= 0 else 0)
            self.input_devices[channel] = combo
        microphone_form.addRow(translate("Dispositivo"), self.input_devices["microphone"])
        self.mic_music_level = QSpinBox()
        self.mic_music_level.setRange(0, 100)
        self.mic_music_level.setSuffix(translate(" %"))
        self.mic_music_level.setValue(max(0, min(100, int(mic_music_level_percent))))
        microphone_form.addRow(translate("Nível da música com microfone ativo"), self.mic_music_level)
        self.dynamic_mic_duck = QCheckBox(translate("Abaixar mais a música enquanto houver fala"))
        self.dynamic_mic_duck.setChecked(dynamic_mic_duck)
        microphone_form.addRow(translate("Detecção de fala"), self.dynamic_mic_duck)
        self.speech_music_level = QSpinBox()
        self.speech_music_level.setRange(0, 100)
        self.speech_music_level.setSuffix(translate(" %"))
        self.speech_music_level.setValue(max(0, min(100, int(speech_music_level_percent))))
        microphone_form.addRow(translate("Nível da música durante a fala"), self.speech_music_level)
        self.speech_threshold = QDoubleSpinBox()
        self.speech_threshold.setRange(-80.0, -10.0)
        self.speech_threshold.setSuffix(translate(" dBFS"))
        self.speech_threshold.setValue(max(-80.0, min(-10.0, float(speech_threshold_db))))
        microphone_form.addRow(translate("Limite para detectar fala"), self.speech_threshold)
        self.speech_music_level.setEnabled(dynamic_mic_duck)
        self.speech_threshold.setEnabled(dynamic_mic_duck)
        self.dynamic_mic_duck.toggled.connect(self.speech_music_level.setEnabled)
        self.dynamic_mic_duck.toggled.connect(self.speech_threshold.setEnabled)
        input_layout.addWidget(microphone_group)

        auxiliary_group = QGroupBox(translate("Auxiliares"))
        auxiliary_form = QFormLayout(auxiliary_group)
        for channel in INPUT_CHANNELS[1:]:
            auxiliary_form.addRow(translate(INPUT_LABELS[channel]), self.input_devices[channel])
        input_layout.addWidget(auxiliary_group)
        input_note = QLabel(
            translate("O microfone e até cinco auxiliares podem ser ativados no painel 🎙 Entradas. "
            "Os auxiliares entram no master sem abaixar a música. Dispositivos ocupados ou "
            "incompatíveis são sinalizados no painel sem interromper o programa."))
        input_note.setWordWrap(True)
        input_layout.addWidget(input_note)
        if not input_devices:
            input_layout.addWidget(QLabel(translate("Nenhum dispositivo de entrada foi encontrado pelo sistema.")))
        input_layout.addStretch()
        tabs.addTab(input_page, translate("Entradas"))

        metadata_page = QWidget()
        metadata_layout = QVBoxLayout(metadata_page)
        metadata_title = QLabel(translate("Metadados da estação e da transmissão"))
        metadata_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        metadata_layout.addWidget(metadata_title)
        metadata_form = QFormLayout()
        values = {**DEFAULT_STATION_METADATA, **station_metadata}
        self.station_name = QLineEdit(values["name"])
        self.station_description = QLineEdit(values["description"])
        self.station_genre = QLineEdit(values["genre"])
        self.station_url = QLineEdit(values["url"])
        self.title_format = QLineEdit(values["title_format"])
        self.metadata_update_mode = QComboBox()
        self.metadata_update_mode.addItem(translate("Algoritmo atual"), "current")
        self.metadata_update_mode.addItem(translate("A cada troca de faixa"), "track_change")
        self.metadata_update_mode.addItem(translate("A cada X segundos"), "interval")
        selected_metadata_mode = self.metadata_update_mode.findData(metadata_update_mode)
        self.metadata_update_mode.setCurrentIndex(
            selected_metadata_mode if selected_metadata_mode >= 0 else 0)
        self.metadata_update_interval = QSpinBox()
        self.metadata_update_interval.setRange(5, 3600)
        self.metadata_update_interval.setSuffix(translate(" segundos"))
        try:
            metadata_interval = int(metadata_update_interval_seconds)
        except (TypeError, ValueError, OverflowError):
            metadata_interval = 30
        self.metadata_update_interval.setValue(max(5, min(3600, metadata_interval)))
        metadata_form.addRow(translate("Nome da estação"), self.station_name)
        metadata_form.addRow(translate("Descrição"), self.station_description)
        metadata_form.addRow(translate("Gênero"), self.station_genre)
        metadata_form.addRow(translate("Site/URL"), self.station_url)
        metadata_form.addRow(translate("Título no ar"), self.title_format)
        metadata_form.addRow(translate("Frequência de envio"), self.metadata_update_mode)
        metadata_form.addRow(translate("Intervalo"), self.metadata_update_interval)
        self.metadata_update_interval.setEnabled(
            self.metadata_update_mode.currentData() == "interval")
        self.metadata_update_mode.currentIndexChanged.connect(
            lambda _index: self.metadata_update_interval.setEnabled(
                self.metadata_update_mode.currentData() == "interval"))
        metadata_layout.addLayout(metadata_form)
        placeholder_help = QLabel(
            translate("Placeholders") + ": " + ", ".join("%" + name for name in PLACEHOLDERS))
        placeholder_help.setWordWrap(True)
        metadata_layout.addWidget(placeholder_help)
        metadata_schedule_help = QLabel(
            translate("Escolha uma opção: o algoritmo atual envia quando o título é definido; "
            "a troca de faixa envia quando uma nova faixa começa; no intervalo, o título "
            "atual é republicado a cada período escolhido."))
        metadata_schedule_help.setWordWrap(True)
        metadata_layout.addWidget(metadata_schedule_help)
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
        tabs.addTab(metadata_page, translate("Metadados"))

        # Keep tabs in a predictable workflow order, with infrequently used
        # recovery actions at the end.
        for target_index, page in enumerate((
                general_page, api_page, audio_page, input_page, mix_page,
                metadata_page, discord_page, backup_page)):
            current_index = tabs.indexOf(page)
            if current_index != target_index:
                tabs.tabBar().moveTab(current_index, target_index)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        translate_dialog_buttons(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def station_values(self) -> dict:
        return {"name": self.station_name.text().strip(),
                "description": self.station_description.text().strip(),
                "genre": self.station_genre.text().strip(),
                "url": self.station_url.text().strip(),
                "title_format": self.title_format.text().strip()}

    def control_api_settings(self) -> dict:
        return {
            "enabled": self.control_api_enabled.isChecked(),
            "port": self.control_api_port.value(),
            "auth_mode": self.control_api_auth_mode.currentData(),
            "allowed_origins": list(parse_allowed_origins(
                self.control_api_origins.text())),
        }

    def control_api_credentials(self) -> dict:
        return {
            "username": self.control_api_username.text().strip(),
            "password": self.control_api_password.text(),
            "token": self.control_api_token.text(),
            "token_changed": self._api_token_changed,
        }

    def update_control_api_controls(self, *_args) -> None:
        basic_auth = self.control_api_auth_mode.currentData() == "basic"
        if (not basic_auth and self.control_api_enabled.isChecked()
                and not self.control_api_token.text()):
            self.generate_control_api_token()
        self.control_api_username.setEnabled(basic_auth)
        self.control_api_password.setEnabled(basic_auth)
        self.control_api_token.setEnabled(not basic_auth)
        self.control_api_generate_token.setEnabled(not basic_auth)
        self.control_api_copy_token.setEnabled(
            not basic_auth and bool(self.control_api_token.text()))

    def update_control_api_addresses(self, *_args) -> None:
        self.control_api_addresses.setText("\n".join(
            api_connection_addresses(self.control_api_port.value())))

    def generate_control_api_token(self) -> None:
        self.control_api_token.setText(secrets.token_urlsafe(32))
        self._api_token_changed = True
        self.update_control_api_controls()

    def copy_control_api_token(self) -> None:
        token = self.control_api_token.text()
        app = QApplication.instance()
        if token and app is not None:
            app.clipboard().setText(token)

    def choose_backup_directory(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, translate("Pasta para backups"), self.backup_directory.text().strip())
        if folder:
            self.backup_directory.setText(folder)

    @guarded
    def create_manual_backup(self) -> None:
        folder = self.backup_directory.text().strip()
        if not folder:
            show_warning(self, translate("Backup"), translate("Selecione uma pasta de destino primeiro."))
            return
        if self._backup_create_callback is None:
            show_warning(self, translate("Backup"), translate("O serviço de backup não está disponível."))
            return
        self.backup_status.setText(translate("Criando backup em segundo plano…"))
        if not self._backup_create_callback(folder, self.backup_retention.value()):
            self.backup_status.setText(translate("Já existe uma operação de backup em andamento."))

    @guarded
    def import_settings_backup(self) -> None:
        backup_file, _ = QFileDialog.getOpenFileName(
            self, translate("Importar configurações"), self.backup_directory.text().strip(),
            translate("Backups do RadioStudio (*.zip);;Todos os arquivos (*)"))
        if backup_file and self._backup_import_callback is not None:
            self._backup_import_callback(backup_file)

    @guarded
    def request_database_repair(self) -> None:
        if self._database_repair_callback is None:
            show_warning(self, translate("Banco de dados"), translate("O serviço de recuperação não está disponível."))
            return
        self._database_repair_callback()

    def verify_discord_connection(self) -> None:
        if self._discord_service is None:
            self.set_discord_status(False, "O serviço de Rich Presence não está disponível.")
            return
        self.discord_verify.setEnabled(False)
        self.discord_verify.setText(translate("Verificando…"))
        self._discord_service.configure(
            DISCORD_APPLICATION_ID, enabled=False, verify=True)

    def set_discord_status(self, available: bool, status: str) -> None:
        same_client = bool(self._discord_service
                           and self._discord_service.client_id == DISCORD_APPLICATION_ID)
        verified = bool(available and same_client)
        self.discord_options.setVisible(verified)
        self.discord_disable_offline.setVisible(not verified and self.discord_enabled.isChecked())
        if verified:
            self.discord_status.setText("✅ " + translate(status))
        else:
            self.discord_status.setText(translate(status))
        checking = not available and status.startswith("Verificando")
        self.discord_verify.setEnabled(not checking)
        self.discord_verify.setText(translate("Verificando…") if checking else
                                    translated_icon_label("🔌 Verificar conexão", "🔌"))

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
        self.discord_status.setText(translate("Rich Presence desativada."))

    @guarded
    def update_title_preview(self, _text: str = ""):
        metadata = self.station_values()
        result = format_broadcast_title(metadata["title_format"], self.preview_track, metadata)
        self.title_preview.setText(translate(f"{translate('Prévia:')} {result}"))

    @guarded
    def accept(self):
        metadata = self.station_values()
        if not metadata["name"] or not metadata["title_format"]:
            show_warning(self, translate("Metadados"), translate("Informe o nome da estação e o formato do título."))
            return
        invalid = unknown_placeholders(metadata["title_format"])
        if invalid:
            show_warning(
                self, translate("Placeholders"), translate("Placeholders desconhecidos:")
                + " " + ", ".join("%" + value for value in invalid))
            return
        if (self.backup_periodic_enabled.isChecked()
                and not self.backup_directory.text().strip()):
            show_warning(
                self, translate("Backups periódicos"), translate("Selecione a pasta de destino dos backups."))
            return
        try:
            parse_allowed_origins(self.control_api_origins.text())
        except ValueError:
            show_warning(self, translate("API REST"), translate(
                "Origem CORS inválida. Use somente protocolo, host e porta."))
            return
        if self.control_api_enabled.isChecked():
            if self.control_api_auth_mode.currentData() == "basic":
                username = self.control_api_username.text().strip()
                if not username:
                    show_warning(self, translate("API REST"),
                                 translate("Informe um nome de usuário para a autenticação."))
                    return
                if ":" in username:
                    show_warning(self, translate("API REST"),
                                 translate("O nome de usuário não pode conter dois-pontos."))
                    return
                saved_password_matches = (
                    self._api_password_configured and username == self._api_initial_username)
                if not self.control_api_password.text() and not saved_password_matches:
                    show_warning(self, translate("API REST"),
                                 translate("Informe uma senha para esse nome de usuário."))
                    return
            elif not self.control_api_token.text():
                self.generate_control_api_token()
        try:
            validate_public_radio_url(self.discord_public_url.text())
            validate_public_radio_url(self.discord_button2_url.text())
            cover_url = validate_public_cover_url(self.discord_cover_url.text())
            if (self.discord_enabled.isChecked() and self.discord_options.isVisible()
                    and self.discord_share_track.isChecked()
                    and self.discord_use_track_cover.isChecked() and not cover_url):
                raise ValueError("Informe a URL pública que fornece a capa atual.")
        except ValueError as exc:
            show_warning(self, translate("Link público"), translate(str(exc)))
            return
        invalid_presence_tokens = sorted(set(
            unknown_presence_placeholders(self.discord_details_template.text())
            + unknown_presence_placeholders(self.discord_state_template.text())))
        if invalid_presence_tokens:
            show_warning(
                self, translate("Rich Presence"), translate("Placeholders desconhecidos:") + " "
                + ", ".join("%" + token for token in invalid_presence_tokens))
            return
        if self.discord_enabled.isChecked() and self.discord_options.isVisible():
            if not self._discord_service or not self._discord_service.available \
                    or self._discord_service.client_id != DISCORD_APPLICATION_ID:
                show_warning(
                    self, translate("Discord"), translate("Verifique a conexão com o Discord antes de ativar a Rich Presence."))
                return
        super().accept()

    def values(self) -> tuple:
        return (self.main_device.currentData(), self.preview_device.currentData(),
                self.local_master.isChecked(), self.station_values(),
                self.metadata_update_mode.currentData(),
                self.metadata_update_interval.value(),
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
                self.process_priority.currentData(), self.backup_directory.text().strip(),
                self.backup_periodic_enabled.isChecked(), self.backup_interval.currentData(),
                self.backup_retention.value(), self.language_preference.currentData())
