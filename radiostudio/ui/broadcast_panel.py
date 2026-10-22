"""Broadcast configuration panel for RadioStudio Python."""

from __future__ import annotations

import logging

from ..runtime_safety import guarded

from PySide6.QtCore import QSize, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..services.broadcast import (ENCODER_QUALITY_PRESETS, PortInUseError, SETTINGS_DIR,
                                  delete_password, new_profile, set_password)
from ..i18n import translate, translate_dialog_buttons
from .message_boxes import show_warning
from .icons import apply_app_icon, translated_icon_label, ui_icon


def spin(low: int, high: int, value: int) -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(low, high)
    widget.setValue(value)
    return widget


def bounded_int(value, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return min(high, max(low, parsed))


def _saved_bool(values: dict, key: str, default: bool = False) -> bool:
    """Fail closed on malformed stored flags while preserving missing defaults."""
    if key not in values:
        return default
    value = values[key]
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    return False


def start_broadcast_with_local_fallback(broadcast, profiles, local):
    """Try nearby embedded-server ports, then keep remote outputs independent."""
    local = dict(local or {})
    if not local.get("enabled"):
        broadcast.start(profiles, local)
        return local

    requested_port = bounded_int(local.get("port", 8090), 8090, 1, 65535)
    last_port = requested_port
    for port in range(requested_port, min(requested_port + 20, 65535) + 1):
        candidate = {**local, "port": port}
        try:
            broadcast.start(profiles, candidate)
        except PortInUseError:
            last_port = port
        else:
            return candidate

    local_error_message = translate(
        "Nenhuma porta livre entre {first_port} e {last_port}").format(
            first_port=requested_port, last_port=last_port)
    local_error = PortInUseError(local_error_message)
    remote_only = {**local, "enabled": False}
    try:
        broadcast.start(profiles, remote_only)
    except Exception as exc:
        remote_error = translate(
            "Saídas externas também não puderam iniciar: {error}").format(error=exc)
        raise RuntimeError(
            f"{local_error}; {remote_error}") from exc

    # Keep this failure visible in the transmission event log while allowing
    # successfully configured remote outputs to continue independently.
    broadcast.startup_errors["local"] = str(local_error)
    return remote_only


class ProfileDialog(QDialog):
    def __init__(self, parent=None, profile=None):
        super().__init__(parent)
        apply_app_icon(self)
        self.profile = dict(profile or new_profile())
        self.setWindowTitle(translate("Saída de transmissão"))
        self.resize(620, 600)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        identity = QGroupBox(translate("Perfil de saída"))
        identity_grid = QGridLayout(identity)
        identity_grid.setColumnStretch(1, 1)
        identity_grid.setColumnStretch(3, 1)
        identity_grid.addWidget(QLabel(translate("Nome")), 0, 0)
        self.name = QLineEdit(self.profile.get("name", ""))
        self.name.setPlaceholderText(translate("Ex.: Icecast principal"))
        identity_grid.addWidget(self.name, 0, 1)
        identity_grid.addWidget(QLabel(translate("Protocolo")), 0, 2)
        self.kind = QComboBox()
        self.kind.addItem(translate("Icecast 2 (HTTP PUT)"), "icecast")
        self.kind.addItem(translate("SHOUTcast (fonte v1/v2 compatível)"), "shoutcast")
        self.kind.setCurrentIndex(0 if self.profile["kind"] == "icecast" else 1)
        identity_grid.addWidget(self.kind, 0, 3)
        self.enabled = QCheckBox(translate("Ativo?"))
        self.enabled.setChecked(self.profile.get("enabled", True))
        identity_grid.addWidget(self.enabled, 1, 1, 1, 3)
        layout.addWidget(identity)

        self.protocol_help = QLabel()
        self.protocol_help.setWordWrap(True)
        self.protocol_help.setStyleSheet("padding: 0 4px;")
        layout.addWidget(self.protocol_help)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        connection_page = QWidget()
        connection_layout = QVBoxLayout(connection_page)
        connection_layout.setContentsMargins(8, 10, 8, 8)
        connection_layout.setSpacing(10)
        server_group = QGroupBox(translate("Servidor"))
        server_form = QFormLayout(server_group)
        server_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.host = QLineEdit(self.profile.get("host", ""))
        self.host.setPlaceholderText(translate("servidor.exemplo.com"))
        server_form.addRow(translate("Endereço"), self.host)
        self.port = spin(1, 65535, int(self.profile.get("port", 8000)))
        server_form.addRow(translate("Porta de origem"), self.port)
        self.tls = QCheckBox(translate("Usar TLS/SSL"))
        self.tls.setChecked(self.profile.get("tls", False))
        server_form.addRow(translate("Segurança"), self.tls)
        connection_layout.addWidget(server_group)

        source_group = QGroupBox(translate("Origem e identificação"))
        self.source_form = QFormLayout(source_group)
        self.source_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.mount = QLineEdit(self.profile.get("mount", "/live.mp3"))
        self.mount.setPlaceholderText(translate("/live.mp3"))
        self.source_form.addRow(translate("Mount"), self.mount)
        self.user = QLineEdit(self.profile.get("user", "source"))
        self.source_form.addRow(translate("Usuário de origem"), self.user)
        self.stream_id = spin(1, 999, int(self.profile.get("stream_id", 1)))
        self.source_form.addRow(translate("Stream ID"), self.stream_id)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText(
            translate("Deixe vazio para manter a senha salva") if profile
            else translate("Senha de origem"))
        self.source_form.addRow(translate("Senha de origem"), self.password)
        connection_layout.addWidget(source_group)

        admin_group = QGroupBox(translate("Acesso administrativo · opcional"))
        self.admin_form = QFormLayout(admin_group)
        self.admin_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.http_port = spin(0, 65535, int(self.profile.get("http_port", 0)))
        self.http_port.setSpecialValueText("Usar porta de origem")
        self.admin_form.addRow(translate("Porta HTTP/admin"), self.http_port)
        self.metadata_user = QLineEdit(self.profile.get("metadata_user", "admin"))
        self.admin_form.addRow(translate("Usuário admin"), self.metadata_user)
        self.metadata_password = QLineEdit()
        self.metadata_password.setEchoMode(QLineEdit.Password)
        self.metadata_password.setPlaceholderText(
            translate("Deixe vazio para manter; usada para atualizar o título no servidor"))
        self.admin_form.addRow(translate("Senha admin"), self.metadata_password)
        connection_layout.addWidget(admin_group)
        admin_help = QLabel(
            translate("A porta HTTP/admin só precisa ser diferente quando o painel do servidor usa outra porta."))
        admin_help.setWordWrap(True)
        connection_layout.addWidget(admin_help)
        connection_layout.addStretch(1)
        connection_scroll = QScrollArea()
        connection_scroll.setWidgetResizable(True)
        connection_scroll.setWidget(connection_page)
        tabs.addTab(connection_scroll, translate("Conexão"))

        stream_page = QWidget()
        stream_layout = QVBoxLayout(stream_page)
        stream_layout.setContentsMargins(8, 10, 8, 8)
        stream_form = QFormLayout()
        stream_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.bitrate = spin(32, 320, int(self.profile.get("bitrate", 192)))
        self.bitrate.setSuffix(translate(" kbps"))
        stream_form.addRow(translate("Bitrate do stream"), self.bitrate)
        self.encoder_quality = QComboBox()
        for label, value in ENCODER_QUALITY_PRESETS:
            self.encoder_quality.addItem(translate(label), value)
        quality = bounded_int(self.profile.get("encoder_quality"), 2, 0, 9)
        index = self.encoder_quality.findData(quality)
        self.encoder_quality.setCurrentIndex(index if index >= 0 else 1)
        self.encoder_quality.setToolTip(
            translate("Controla a complexidade do encoder MP3. Níveis menores priorizam qualidade e usam mais CPU."))
        stream_form.addRow(translate("Qualidade do encoder"), self.encoder_quality)
        stream_layout.addLayout(stream_form)

        metadata_group = QGroupBox(translate("Dados enviados ao servidor"))
        metadata_form = QFormLayout(metadata_group)
        metadata_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.station = QLineEdit(self.profile.get("station", ""))
        self.station.setPlaceholderText(translate("Herdar das configurações da estação"))
        metadata_form.addRow(translate("Nome da rádio"), self.station)
        self.genre = QLineEdit(self.profile.get("genre", ""))
        self.genre.setPlaceholderText(translate("Herdar das configurações da estação"))
        metadata_form.addRow(translate("Gênero"), self.genre)
        self.url = QLineEdit(self.profile.get("url", ""))
        self.url.setPlaceholderText(translate("Herdar das configurações da estação"))
        metadata_form.addRow(translate("Site"), self.url)
        self.description = QLineEdit(self.profile.get("description", ""))
        self.description.setPlaceholderText(translate("Herdar das configurações da estação"))
        metadata_form.addRow(translate("Descrição"), self.description)
        stream_layout.addWidget(metadata_group)
        metadata_help = QLabel(
            translate("Preencha apenas para personalizar este destino. Campos vazios usam os valores de Configurações > Metadados."))
        metadata_help.setWordWrap(True)
        stream_layout.addWidget(metadata_help)
        stream_layout.addStretch(1)
        tabs.addTab(stream_page, translate("Áudio e metadados"))

        self.kind.currentIndexChanged.connect(self.update_protocol_fields)
        self.update_protocol_fields()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        translate_dialog_buttons(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @guarded
    def update_protocol_fields(self, _index: int = -1):
        icecast = self.kind.currentData() == "icecast"
        self.protocol_help.setText(translate(
            "Icecast 2: envio HTTP PUT para um mount. Informe o usuário de origem e o mount."
            if icecast else
            "SHOUTcast: conexão de fonte compatível. O Stream ID identifica a transmissão em DNAS 2.4.7 ou mais recente."))
        for widget in (self.mount, self.user, self.stream_id):
            is_visible = (icecast if widget is not self.stream_id else not icecast)
            widget.setVisible(is_visible)
            label = self.source_form.labelForField(widget)
            if label:
                label.setVisible(is_visible)
        self.metadata_user.setVisible(icecast)
        label = self.admin_form.labelForField(self.metadata_user)
        if label:
            label.setVisible(icecast)

    @guarded
    def accept(self):
        if not self.host.text().strip() or not self.name.text().strip():
            show_warning(self, translate("Dados necessários"), translate("Informe nome e servidor."))
            return
        if not self.password.text() and not self.profile.get("saved", False):
            show_warning(self, translate("Senha necessária"), translate("Informe a senha de origem."))
            return
        if self.password.text():
            try:
                set_password(self.profile["id"], self.password.text())
            except Exception as exc:
                show_warning(self, translate("Credenciais"), translate(f"Não foi possível salvar a senha: {exc}"))
                return
        if self.metadata_password.text():
            try:
                set_password(self.profile["id"] + ":metadata", self.metadata_password.text())
            except Exception as exc:
                show_warning(self, translate("Credenciais"), translate(f"Não foi possível salvar a senha admin: {exc}"))
                return
        self.profile.update({"kind": self.kind.currentData(), "name": self.name.text().strip(),
                             "host": self.host.text().strip(), "port": self.port.value(),
                             "tls": self.tls.isChecked(), "http_port": self.http_port.value(),
                             "stream_id": self.stream_id.value(),
                             "metadata_user": self.metadata_user.text().strip() or "admin",
                             "mount": self.mount.text().strip() or "/live.mp3",
                             "user": self.user.text().strip() or "source", "bitrate": self.bitrate.value(),
                             "encoder_quality": int(self.encoder_quality.currentData()),
                             "station": self.station.text().strip(), "genre": self.genre.text().strip(),
                             "url": self.url.text().strip(), "description": self.description.text().strip(),
                             "enabled": self.enabled.isChecked()})
        self.profile.pop("saved", None)
        super().accept()


class BroadcastPanel(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.profiles = []
        self.output_states = {}
        self.connected_outputs = set()
        layout = QVBoxLayout(self)
        local_group = QGroupBox(translate("Servidor embutido"))
        grid = QGridLayout(local_group)
        self.local_enabled = QCheckBox(translate("Ativar servidor de escuta"))
        grid.addWidget(self.local_enabled, 0, 0, 1, 2)
        self.lan = QCheckBox(translate("Permitir acesso pela rede local"))
        grid.addWidget(self.lan, 0, 2, 1, 2)
        grid.addWidget(QLabel(translate("Porta")), 1, 0)
        self.local_port = spin(1, 65535, 8090)
        grid.addWidget(self.local_port, 1, 1)
        grid.addWidget(QLabel(translate("Ouvintes máximos")), 1, 2)
        self.max_listeners = spin(1, 1000, 10)
        grid.addWidget(self.max_listeners, 1, 3)
        grid.addWidget(QLabel(translate("MP3 (kbps)")), 2, 0)
        self.local_bitrate = spin(32, 320, 192)
        grid.addWidget(self.local_bitrate, 2, 1)
        grid.addWidget(QLabel(translate("Nome da rádio")), 2, 2)
        self.local_station = QLineEdit()
        self.local_station.setPlaceholderText(translate("Usar Configurações > Metadados"))
        grid.addWidget(self.local_station, 2, 3)
        grid.addWidget(QLabel(translate("Qualidade do encoder")), 3, 0)
        self.local_encoder_quality = QComboBox()
        for label, value in ENCODER_QUALITY_PRESETS:
            self.local_encoder_quality.addItem(translate(label), value)
        self.local_encoder_quality.setCurrentIndex(1)
        self.local_encoder_quality.setToolTip(
            translate("Níveis menores priorizam qualidade e usam mais CPU."))
        grid.addWidget(self.local_encoder_quality, 3, 1, 1, 3)
        self.local_url = QLabel(translate("Endereço: http://127.0.0.1:8090/live.mp3"))
        grid.addWidget(self.local_url, 4, 0, 1, 4)
        self.local_send_cover = QCheckBox(translate("Disponibilizar capa junto aos metadados do servidor embutido"))
        self.local_send_cover.setToolTip(
            translate("A capa atual fica disponível em /cover e o endereço aparece em /status.json. "
            "Icecast recebe título em texto. Arte no SHOUTcast DNAS v2 exige configuração "
            "de arquivo diretamente no servidor e não é enviada pelo RadioStudio."))
        grid.addWidget(self.local_send_cover, 5, 0, 1, 4)
        cover_note = QLabel(
            translate("A capa é exposta somente pelo servidor embutido (/cover e /status.json). "
            "Icecast recebe título em texto; arte no SHOUTcast DNAS v2 depende de "
            "configuração própria do servidor, não de envio pelo RadioStudio."))
        cover_note.setWordWrap(True)
        cover_note.setStyleSheet("color: palette(mid); font-size: 9pt;")
        grid.addWidget(cover_note, 6, 0, 1, 4)
        self.local_send_cover.toggled.connect(self.update_cover_delivery_setting)
        self.local_port.valueChanged.connect(self.update_url)
        self.lan.toggled.connect(self.update_url)
        layout.addWidget(local_group)

        output_group = QGroupBox(translate("Saídas para servidores externos"))
        output_layout = QVBoxLayout(output_group)
        buttons = QHBoxLayout()
        for label, handler in [("Adicionar", self.add_profile), ("Editar", self.edit_profile),
                               ("Excluir", self.delete_profile)]:
            button = QPushButton(translate(label))
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        output_layout.addLayout(buttons)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([translate("Ativo"), translate("Nome"), translate("Tipo"), translate("Servidor"), translate("Estado"), translate("Ouvintes")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self.edit_profile)
        output_layout.addWidget(self.table)
        layout.addWidget(output_group, 1)

        note = QLabel(translate("Mudanças nos perfis entram em vigor após parar e iniciar a transmissão."))
        layout.addWidget(note)

        controls = QHBoxLayout()
        self.start_button = QPushButton(translated_icon_label(
            "▶ Iniciar transmissão", "▶"))
        self.start_button.setIcon(ui_icon("broadcast"))
        self.start_button.setIconSize(QSize(22, 22))
        self.start_button.clicked.connect(self.start)
        controls.addWidget(self.start_button)
        self.stop_button = QPushButton(translated_icon_label(
            "⏹ Parar transmissão", "⏹"))
        self.stop_button.setIcon(ui_icon("stop"))
        self.stop_button.setIconSize(QSize(22, 22))
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.stop_button)
        self.summary = QLabel(translate("Transmissão desligada"))
        controls.addWidget(self.summary, 1)
        layout.addLayout(controls)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(1000)

    @guarded
    def update_url(self, *_signal_args):
        host = translate("IP desta máquina") if self.lan.isChecked() else "127.0.0.1"
        tls = (SETTINGS_DIR / "server_cert.pem").is_file() and (SETTINGS_DIR / "server_key.pem").is_file()
        scheme = "https" if tls else "http"
        self.local_url.setText(translate(f"Endereço: {scheme}://{host}:{self.local_port.value()}/live.mp3"))

    @guarded
    def update_cover_delivery_setting(self, enabled: bool):
        self.main_window.broadcast.set_cover_art_enabled(enabled)

    def local_config(self):
        return {"enabled": self.local_enabled.isChecked(), "lan": self.lan.isChecked(),
                "port": self.local_port.value(), "max_listeners": self.max_listeners.value(),
                "bitrate": self.local_bitrate.value(),
                "encoder_quality": int(self.local_encoder_quality.currentData()),
                "send_cover_art": self.local_send_cover.isChecked(),
                "station": self.local_station.text().strip()}

    def load_config(self, profiles, local):
        self.profiles = []
        for saved in profiles if isinstance(profiles, list) else []:
            if not isinstance(saved, dict) or not isinstance(saved.get("id"), str) or not saved["id"]:
                continue
            kind = saved.get("kind") if saved.get("kind") in ("icecast", "shoutcast") else "icecast"
            profile = {**new_profile(kind), **saved}
            profile["kind"] = kind
            profile["port"] = bounded_int(saved.get("port"), 8000, 1, 65535)
            profile["http_port"] = bounded_int(saved.get("http_port"), 0, 0, 65535)
            profile["stream_id"] = bounded_int(saved.get("stream_id"), 1, 1, 999)
            profile["bitrate"] = bounded_int(saved.get("bitrate"), 192, 32, 320)
            profile["encoder_quality"] = bounded_int(saved.get("encoder_quality"), 2, 0, 9)
            profile["enabled"] = _saved_bool(saved, "enabled", True)
            profile["tls"] = _saved_bool(saved, "tls", False)
            for key in ("name", "host", "mount", "user", "station", "genre", "url",
                        "description", "metadata_user"):
                profile[key] = str(profile.get(key) or "")
            profile["name"] = profile["name"] or kind.title()
            profile["mount"] = profile["mount"] or "/live.mp3"
            profile["user"] = profile["user"] or "source"
            profile["metadata_user"] = profile["metadata_user"] or "admin"
            self.profiles.append(profile)
        local = local if isinstance(local, dict) else {}
        self.local_enabled.setChecked(_saved_bool(local, "enabled", False))
        self.lan.setChecked(_saved_bool(local, "lan", False))
        self.local_port.setValue(bounded_int(local.get("port"), 8090, 1, 65535))
        self.max_listeners.setValue(bounded_int(local.get("max_listeners"), 10, 1, 1000))
        self.local_bitrate.setValue(bounded_int(local.get("bitrate"), 192, 32, 320))
        quality = bounded_int(local.get("encoder_quality"), 2, 0, 9)
        quality_index = self.local_encoder_quality.findData(quality)
        self.local_encoder_quality.setCurrentIndex(quality_index if quality_index >= 0 else 1)
        self.local_station.setText("" if local.get("station") == "RadioStudio Python"
                                   else str(local.get("station") or ""))
        self.local_send_cover.setChecked(_saved_bool(local, "send_cover_art", False))
        for profile in self.profiles:
            if profile.get("station") == "RadioStudio Python":
                profile["station"] = ""
            if profile.get("genre") == "Other":
                profile["genre"] = ""
        self.refresh_profiles()
        self.update_url()

    def refresh_profiles(self):
        self.table.setRowCount(len(self.profiles))
        for i, profile in enumerate(self.profiles):
            kind = {"icecast": "Icecast", "shoutcast": "SHOUTcast"}.get(
                profile.get("kind"), profile.get("kind", ""))
            for column, value in enumerate([translate("Sim" if profile.get("enabled") else "Não"),
                                             profile.get("name", ""), kind,
                                             f"{profile.get('host', '')}:{profile.get('port', '')}",
                                             translate("Parado"), "—"]):
                self.table.setItem(i, column, QTableWidgetItem(value))

    @guarded
    def add_profile(self):
        dialog = ProfileDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.profiles.append(dialog.profile)
            self.refresh_profiles()
            self.main_window.save_state()

    @guarded
    def edit_profile(self, _index=None):
        row = self.table.currentRow()
        if row < 0:
            return
        profile = dict(self.profiles[row])
        profile["saved"] = True
        dialog = ProfileDialog(self, profile)
        if dialog.exec() == QDialog.Accepted:
            self.profiles[row] = dialog.profile
            self.refresh_profiles()
            self.main_window.save_state()

    @guarded
    def delete_profile(self):
        row = self.table.currentRow()
        if row < 0:
            return
        profile = self.profiles.pop(row)
        for credential_id in (profile["id"], profile["id"] + ":metadata"):
            try:
                delete_password(credential_id)
            except Exception:
                logging.getLogger("radiostudio").exception(
                    "Falha ao excluir a credencial do perfil de transmissão")
        self.refresh_profiles()
        self.main_window.save_state()

    @guarded
    def start(self):
        self.main_window.save_state()
        try:
            station = self.main_window.station_metadata
            profiles = [{**profile,
                         **{key: profile.get(key) or station.get(global_key, "")
                            for key, global_key in (("station", "name"), ("genre", "genre"),
                                                    ("url", "url"), ("description", "description"))}}
                        for profile in self.profiles]
            local = self.local_config()
            local.update({"station": local["station"] or station["name"],
                          "genre": station["genre"], "url": station["url"],
                          "description": station["description"]})
            requested_port = local["port"]
            local = start_broadcast_with_local_fallback(
                self.main_window.broadcast, profiles, local)
            if (local.get("enabled") and local["port"] != requested_port):
                self.local_port.setValue(local["port"])
                self.main_window.save_state()
                message = (f"Porta {requested_port} ocupada; servidor embutido "
                           f"iniciado na porta {local['port']}")
                self.main_window.log_event("error", message)
                self.main_window.statusBar().showMessage(translate(message))
            if self.main_window.broadcast.title:
                self.main_window.broadcast.set_title(self.main_window.broadcast.title)
        except Exception as exc:
            self.main_window.log_event("error", f"Falha ao iniciar transmissão: {exc}")
            show_warning(self, translate("Transmissão"), translate(str(exc)))
            return
        self.output_states.clear()
        self.connected_outputs.clear()
        self.main_window.broadcast_was_running = True
        for profile_id, error in self.main_window.broadcast.startup_errors.items():
            profile = next((item for item in self.profiles if item["id"] == profile_id), None)
            label = profile["name"] if profile else profile_id
            self.main_window.log_event("error", f"{label}: {error}")
        if self.main_window.broadcast.local_server:
            server = self.main_window.broadcast.local_server
            self.main_window.log_event(
                "connected", f"Servidor embutido iniciado na porta {server.port}")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.refresh_status()
        self.main_window.on_broadcast_started()
        self.main_window.save_state()

    @guarded
    def stop(self):
        active_outputs = [output.name for output in self.main_window.broadcast.outputs
                          if output.profile_id and output.status == "Ativo"]
        local_active = self.main_window.broadcast.local_server is not None
        self.main_window.on_broadcast_stopped()
        self.main_window.broadcast.stop_transmission()
        self.main_window.broadcast_was_running = False
        for name in active_outputs:
            self.main_window.log_event("disconnected", f"Desconectado: {name}")
        if local_active:
            self.main_window.log_event("disconnected", "Servidor embutido encerrado")
        self.output_states.clear()
        self.connected_outputs.clear()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.refresh_status()
        self.main_window.save_state()

    @guarded
    def refresh_status(self):
        outputs = self.main_window.broadcast.outputs
        self.main_window.on_air.set_broadcast_active(
            any(output.status == "Ativo" for output in outputs))
        for output in outputs:
            key = output.profile_id or "local"
            state = (output.status, output.error)
            previous = self.output_states.get(key)
            if state != previous:
                if output.profile_id and output.status == "Ativo":
                    label = "Reconectado" if key in self.connected_outputs else "Conectado"
                    self.main_window.log_event("connected", f"{label}: {output.name}")
                    self.connected_outputs.add(key)
                elif output.status == "Reconectando" and output.error:
                    if output.profile_id and previous and previous[0] == "Ativo":
                        self.main_window.log_event("disconnected", f"Desconectado: {output.name}")
                    self.main_window.log_event("error", f"{output.name}: {output.error}")
                self.output_states[key] = state
        for i, profile in enumerate(self.profiles):
            status = translate("Parado")
            listener_count = "—"
            for output in outputs:
                if output.profile_id == profile["id"]:
                    status = translate(output.status)
                    if output.error:
                        status += f": {translate(output.error)}"
                    listener_count = str(output.listeners) if output.listeners is not None else "—"
                    break
            else:
                error = self.main_window.broadcast.startup_errors.get(profile["id"])
                if error:
                    status = translate(error)
            status_item = self.table.item(i, 4)
            if status_item is not None and status_item.text() != status:
                status_item.setText(status)
            listeners_item = self.table.item(i, 5)
            if listeners_item is not None and listeners_item.text() != listener_count:
                listeners_item.setText(listener_count)
        if outputs:
            active = sum(output.status == "Ativo" for output in outputs)
            listeners = self.main_window.broadcast.local_server.listeners if self.main_window.broadcast.local_server else 0
            summary = translate(
                f"{active}/{len(outputs)} saídas ativas · {listeners} ouvinte(s) locais")
        else:
            summary = translate("Transmissão desligada")
        if self.summary.text() != summary:
            self.summary.setText(summary)
