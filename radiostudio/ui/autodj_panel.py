"""Libraries, weekly AutoDJ programming, and searchable play history UI."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QTime, QThreadPool, QTimer

from ..services.autodj import DAY_NAMES, FILTER_FIELDS, active_program
from ..i18n import translate, translate_dialog_buttons
from .message_boxes import ask_question, show_warning
from ..runtime_safety import guarded
from .icons import apply_app_icon, translated_icon_label, ui_icon


def _text(value, default=""):
    return str(value) if value is not None else default


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


class _ScanSignals(QObject):
    finished = Signal(str, object)


class _ScanWorker(QRunnable):
    def __init__(self, library, library_id):
        super().__init__()
        self.library = library
        self.library_id = library_id
        self.signals = _ScanSignals()

    def run(self):
        try:
            result = self.library.scan_library(self.library_id)
        except Exception as exc:  # worker exceptions must come back to the UI thread
            result = exc
        self.signals.finished.emit(self.library_id, result)


class ProgramDialog(QDialog):
    def __init__(self, libraries, program=None, parent=None, *, collections=(),
                 groups=(), tags=()):
        super().__init__(parent)
        apply_app_icon(self)
        self.setWindowTitle(translate("Programação AutoDJ"))
        self.resize(570, 610)
        program = program or {}
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(_text(program.get("name"), translate("Novo bloco")))
        form.addRow(translate("Nome do bloco"), self.name)
        self.enabled = QCheckBox(translate("Bloco ativo"))
        self.enabled.setChecked(bool(program.get("enabled", True)))
        form.addRow(translate("Estado"), self.enabled)
        self.start = self._time_edit(_text(program.get("start", "00:00"), "00:00"))
        self.end = self._time_edit(_text(program.get("end", "01:00"), "01:00"))
        hours = QHBoxLayout()
        hours.addWidget(QLabel(translate("Das"))); hours.addWidget(self.start)
        hours.addWidget(QLabel(translate("até"))); hours.addWidget(self.end); hours.addStretch()
        form.addRow(translate("Intervalo"), hours)
        self.priority = QSpinBox(); self.priority.setRange(-100, 100)
        self.priority.setValue(_integer(program.get("priority", 0)))
        form.addRow(translate("Prioridade (maior vence)"), self.priority)
        root.addLayout(form)

        day_box = QGroupBox(translate("Dias da semana"))
        day_layout = QHBoxLayout(day_box)
        saved_days = set()
        raw_days = program.get("days", list(range(7)))
        if isinstance(raw_days, (list, tuple, set)):
            for day in raw_days:
                try:
                    day = int(day)
                except (TypeError, ValueError):
                    continue
                if 0 <= day < 7:
                    saved_days.add(day)
        self.days = []
        for index, label in enumerate(DAY_NAMES):
            check = QCheckBox(translate(label)); check.setChecked(index in saved_days)
            day_layout.addWidget(check); self.days.append(check)
        root.addWidget(day_box)

        library_box = QGroupBox(translate("Pastas indexadas da Biblioteca"))
        library_layout = QVBoxLayout(library_box)
        library_help = QLabel(translate(
            "Marque as pastas indexadas que este bloco deve usar. Se não marcar nenhuma pasta nem uma fonte na tabela abaixo, todas as pastas habilitadas serão usadas."))
        library_help.setWordWrap(True)
        library_layout.addWidget(library_help)
        self.library_list = QListWidget()
        raw_libraries = program.get("library_ids", [])
        saved_libraries = ({str(value) for value in raw_libraries}
                           if isinstance(raw_libraries, (list, tuple, set)) else set())
        available_library_ids = {str(library.get("id")) for library in libraries}
        for library in libraries:
            label = f"{library['name']} — {library['root']}"
            if not library.get("enabled", 1):
                label += f" ({translate('Desativado')})"
            item = QListWidgetItem(label)
            item.setIcon(ui_icon("libraries"))
            item.setData(Qt.UserRole, library["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if str(library["id"]) in saved_libraries else Qt.Unchecked)
            self.library_list.addItem(item)
        for library_id in sorted(saved_libraries - available_library_ids):
            item = QListWidgetItem(
                translate("Pasta indisponível ({id})").format(id=library_id))
            item.setData(Qt.UserRole, library_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.library_list.addItem(item)
        library_layout.addWidget(self.library_list)
        root.addWidget(library_box, 1)

        sources_box = QGroupBox(translate("Fontes do bloco e pesos relativos"))
        sources_layout = QVBoxLayout(sources_box)
        sources_help = QLabel(translate(
            "Marque coleções, grupos, etiquetas ou pastas para usar como fontes deste bloco. Elas podem ser usadas sozinhas ou combinadas com as pastas marcadas acima; os pesos definem a frequência relativa das faixas."))
        sources_help.setWordWrap(True)
        sources_layout.addWidget(sources_help)
        self.source_table = QTableWidget(0, 4)
        self.source_table.setHorizontalHeaderLabels([
            translate("Usar"), translate("Tipo"), translate("Origem"), translate("Peso")])
        self.source_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.source_table.setMaximumHeight(150)
        self.source_table.horizontalHeader().setStretchLastSection(True)
        self.source_rows = []
        raw_sources = program.get("sources", [])
        saved_sources = {}
        if isinstance(raw_sources, (list, tuple)):
            for source in raw_sources:
                if isinstance(source, dict):
                    saved_sources[(str(source.get("type", "")), str(source.get("id", "")))] = source
        source_options = []
        source_options.extend((
            "library", str(item["id"]),
            f"{item['name']} ({translate('Desativado')})" if not item.get("enabled", 1)
            else item["name"], 1.0)
            for item in libraries)
        source_options.extend(("collection", str(item["id"]), item["name"], 1.0)
                              for item in collections)
        source_options.extend(("group", str(item["id"]), item["name"],
                               float(item.get("weight", 1))) for item in groups)
        source_options.extend(("tag", str(item), str(item), 1.0) for item in tags)
        source_type_labels = {"library": "Pasta indexada", "collection": "Coleção",
                              "group": "Grupo de rotação", "tag": "Etiqueta"}
        available_source_keys = {(kind, source_id)
                                 for kind, source_id, _name, _weight in source_options}
        for (kind, source_id), saved in saved_sources.items():
            if (kind not in source_type_labels
                    or (kind, source_id) in available_source_keys):
                continue
            try:
                default_weight = float(saved.get("weight", 1))
            except (TypeError, ValueError, OverflowError):
                default_weight = 1.0
            unavailable = translate("Fonte indisponível ({type}: {id})").format(
                type=translate(source_type_labels[kind]), id=source_id)
            source_options.append((kind, source_id, unavailable, default_weight))
        self.source_table.setRowCount(len(source_options))
        for row, (kind, source_id, name, default_weight) in enumerate(source_options):
            saved = saved_sources.get((kind, source_id))
            enabled = QTableWidgetItem()
            enabled.setFlags(enabled.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            enabled.setCheckState(Qt.Checked if saved else Qt.Unchecked)
            enabled.setData(Qt.UserRole, {"type": kind, "id": source_id})
            self.source_table.setItem(row, 0, enabled)
            self.source_table.setItem(row, 1, QTableWidgetItem(translate(source_type_labels[kind])))
            self.source_table.setItem(row, 2, QTableWidgetItem(str(name)))
            weight = QDoubleSpinBox()
            weight.setRange(0.01, 100.0)
            weight.setDecimals(2)
            weight.setSingleStep(0.25)
            try:
                weight_value = float(saved.get("weight", default_weight) if saved else default_weight)
            except (TypeError, ValueError, OverflowError):
                weight_value = default_weight
            weight.setValue(max(0.01, min(100.0, weight_value)))
            self.source_table.setCellWidget(row, 3, weight)
            self.source_rows.append((enabled, weight))
        sources_layout.addWidget(self.source_table)
        root.addWidget(sources_box)

        filters_box = QGroupBox(translate("Filtros de metadados"))
        filters = QFormLayout(filters_box)
        old_filters = program.get("filters", {})
        if not isinstance(old_filters, dict):
            old_filters = {}
        self.genres = QLineEdit(_text(old_filters.get("genres"))); self.genres.setPlaceholderText(translate("Rock, Pop, Jazz"))
        self.artists = QLineEdit(_text(old_filters.get("artists"))); self.artists.setPlaceholderText(translate("Artistas exatos, separados por vírgula"))
        self.include = QLineEdit(_text(old_filters.get("include_keywords"))); self.include.setPlaceholderText(translate("Todas as palavras precisam aparecer"))
        self.exclude = QLineEdit(_text(old_filters.get("exclude_keywords"))); self.exclude.setPlaceholderText(translate("Qualquer uma destas palavras elimina a faixa"))
        filters.addRow(translate("Gêneros"), self.genres)
        filters.addRow(translate("Artistas"), self.artists)
        filters.addRow(translate("Título/álbum/artista contém"), self.include)
        filters.addRow(translate("Excluir palavra-chave"), self.exclude)
        year_row = QHBoxLayout()
        self.year_from = QSpinBox(); self.year_from.setRange(0, 9999); self.year_from.setSpecialValueText("Sem mínimo")
        self.year_to = QSpinBox(); self.year_to.setRange(0, 9999); self.year_to.setSpecialValueText("Sem máximo")
        self.year_from.setValue(_integer(old_filters.get("year_from", 0)))
        self.year_to.setValue(_integer(old_filters.get("year_to", 0)))
        year_row.addWidget(self.year_from); year_row.addWidget(QLabel(translate("até"))); year_row.addWidget(self.year_to); year_row.addStretch()
        filters.addRow(translate("Ano"), year_row)
        self.field_checks = {}
        fields_row = QGridLayout()
        old_fields = old_filters.get("keyword_fields", ["title", "album", "artist", "albumartist", "genre", "composer", "year"])
        if not isinstance(old_fields, (list, tuple, set)):
            old_fields = []
        labels = {"title": "Título", "artist": "Artista", "album": "Álbum", "albumartist": "Artista do álbum",
                  "genre": "Gênero", "composer": "Compositor", "year": "Ano", "path": "Nome/caminho"}
        for index, field in enumerate(FILTER_FIELDS):
            check = QCheckBox(translate(labels[field])); check.setChecked(field in old_fields)
            fields_row.addWidget(check, index // 4, index % 4); self.field_checks[field] = check
        filters.addRow(translate("Buscar palavras em"), fields_row)
        root.addWidget(filters_box)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        translate_dialog_buttons(buttons)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _time_edit(value):
        edit = QTimeEdit()
        try:
            hour, minute = map(int, value.split(":", 1))
            candidate = QTime(hour, minute)
            edit.setTime(candidate if candidate.isValid() else QTime(0, 0))
        except (ValueError, AttributeError):
            edit.setTime(QTime(0, 0))
        edit.setDisplayFormat("HH:mm")
        return edit

    def accept(self):
        if not self.name.text().strip():
            show_warning(self, translate("Programação"), translate("Informe um nome para o bloco."))
            return
        if self.start.time() == self.end.time():
            show_warning(self, translate("Programação"), translate("O início e o fim precisam ser diferentes."))
            return
        if not any(check.isChecked() for check in self.days):
            show_warning(self, translate("Programação"), translate("Selecione pelo menos um dia da semana."))
            return
        if self.year_from.value() and self.year_to.value() and self.year_from.value() > self.year_to.value():
            show_warning(self, translate("Programação"), translate("O ano inicial não pode ser maior que o ano final."))
            return
        super().accept()

    def value(self, previous=None):
        selected = [self.library_list.item(i).data(Qt.UserRole)
                    for i in range(self.library_list.count())
                    if self.library_list.item(i).checkState() == Qt.Checked]
        selected_sources = []
        for enabled, weight in self.source_rows:
            if enabled.checkState() == Qt.Checked:
                source = dict(enabled.data(Qt.UserRole))
                source["weight"] = weight.value()
                selected_sources.append(source)
        filters = {
            "genres": self.genres.text().strip(), "artists": self.artists.text().strip(),
            "include_keywords": self.include.text().strip(), "exclude_keywords": self.exclude.text().strip(),
            "year_from": self.year_from.value(), "year_to": self.year_to.value(),
            "keyword_fields": [field for field, check in self.field_checks.items() if check.isChecked()],
        }
        return {"id": (previous or {}).get("id") or uuid.uuid4().hex,
                "enabled": self.enabled.isChecked(), "name": self.name.text().strip(),
                "start": self.start.time().toString("HH:mm"), "end": self.end.time().toString("HH:mm"),
                "days": [index for index, check in enumerate(self.days) if check.isChecked()],
                "priority": self.priority.value(), "library_ids": selected,
                "sources": selected_sources, "filters": filters}


class AutoDJPanel(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.library = main_window.music_library
        self.workers = set()
        self.scanning_libraries = set()
        self.pool = QThreadPool.globalInstance()
        self.history_tab_index = None
        self._hidden_library_pages = []
        self._history_refresh_timer = QTimer(self)
        self._history_refresh_timer.setSingleShot(True)
        self._history_refresh_timer.timeout.connect(self.refresh_history_incrementally)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        self.toggle = QPushButton(
            translated_icon_label("⏹ AutoDJ desligado", "⏹"))
        self.toggle.setIcon(ui_icon("autodj"))
        self.toggle.setIconSize(QSize(22, 22))
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self.main_window.set_autodj_enabled)
        header.addWidget(self.toggle)
        self.status = QLabel(translate("Configure pastas indexadas e blocos de programação"))
        self.status.setWordWrap(True)
        header.addWidget(self.status, 1)
        self.fill_button = QPushButton(
            translated_icon_label("🎲 Completar fila", "🎲"))
        self.fill_button.setIcon(ui_icon("queue_fill"))
        self.fill_button.setIconSize(QSize(20, 20))
        self.fill_button.clicked.connect(
            lambda _checked=False: self.main_window.fill_autodj_queue(force=True))
        header.addWidget(self.fill_button)
        root.addLayout(header)

        policy_box = QGroupBox(translate("Regras de repetição"))
        policy = QHBoxLayout(policy_box)
        policy.addWidget(QLabel(translate("Evitar artista por")))
        self.artist_cooldown = QSpinBox(); self.artist_cooldown.setRange(0, 10080); self.artist_cooldown.setSuffix(translate(" min"))
        self.artist_cooldown.setToolTip(translate("0 desativa esta regra"))
        policy.addWidget(self.artist_cooldown)
        policy.addWidget(QLabel(translate("Faixa por")))
        self.track_cooldown = QSpinBox(); self.track_cooldown.setRange(0, 43200); self.track_cooldown.setSuffix(translate(" min"))
        self.track_cooldown.setToolTip(translate("0 desativa esta regra"))
        policy.addWidget(self.track_cooldown)
        policy.addWidget(QLabel(translate("Faixas à frente")))
        self.queue_ahead = QSpinBox(); self.queue_ahead.setRange(1, 30)
        policy.addWidget(self.queue_ahead); policy.addStretch()
        for control in (self.artist_cooldown, self.track_cooldown, self.queue_ahead):
            control.valueChanged.connect(self.save_settings)
        root.addWidget(policy_box)
        self.remove_played = QCheckBox(translated_icon_label(
            "🧹 Remover da fila as faixas anteriores à atual", "🧹"))
        self.remove_played.setToolTip(
            translate("Ao começar uma faixa, remove as linhas anteriores da Now Playing. "
            "A faixa atual, as próximas e o histórico de reprodução são preservados."))
        self.remove_played.toggled.connect(self.save_settings)
        root.addWidget(self.remove_played)

        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(18, 18))
        root.addWidget(self.tabs, 1)
        self._build_libraries_tab()
        self._build_program_tab()
        self._build_history_tab()
        self.tabs.currentChanged.connect(self._tab_changed)
        self.load_settings(main_window.autodj_config)
        self.refresh_libraries()
        self.refresh_programs()
        self.refresh_history()

    def _build_libraries_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        buttons = QHBoxLayout()
        for label, marker, handler, icon in (
                ("➕ Adicionar pasta", "➕", self.add_library, "add_folder"),
                ("🔄 Examinar", "🔄", self.scan_selected, "refresh"),
                ("🗑 Remover", "🗑", self.remove_selected, "remove")):
            button = QPushButton(translated_icon_label(label, marker))
            button.setIcon(ui_icon(icon))
            button.clicked.connect(handler); buttons.addWidget(button)
        layout.addLayout(buttons)
        folders_help = QLabel(translate(
            "A Biblioteca reúne as faixas das pastas indexadas e ativas. Desativar uma pasta a remove do catálogo e da seleção do AutoDJ; os arquivos permanecem no disco."))
        folders_help.setWordWrap(True)
        layout.addWidget(folders_help)
        self.library_table = QTableWidget(0, 4)
        self.library_table.setHorizontalHeaderLabels([
            translate("Ativa"), translate("Pasta indexada"),
            translate("Faixas"), translate("Caminho")])
        self.library_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.library_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.library_table.horizontalHeader().setStretchLastSection(True)
        self.library_table.setColumnWidth(0, 55); self.library_table.setColumnWidth(1, 120)
        self.library_table.setColumnWidth(2, 55)
        self.library_table.itemChanged.connect(self.library_toggled)
        layout.addWidget(self.library_table)
        page.setParent(self)
        page.hide()
        self._hidden_library_pages.append(page)

    def _build_program_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        description = QLabel(translate(
            "Sem um bloco de programação ativo, o AutoDJ usa o catálogo inteiro da Biblioteca: todas as pastas indexadas e habilitadas para o AutoDJ. Em cada bloco, você pode limitar as faixas a pastas indexadas, coleções, grupos ou etiquetas; os filtros refinam essa seleção."))
        description.setWordWrap(True); layout.addWidget(description)
        buttons = QHBoxLayout()
        for label, marker, handler, icon in (
                ("➕ Novo bloco", "➕", self.add_program, "add"),
                ("✏️ Editar", "✏️", self.edit_program, "edit"),
                ("🗑 Excluir", "🗑", self.delete_program, "remove")):
            button = QPushButton(translated_icon_label(label, marker))
            button.setIcon(ui_icon(icon))
            button.clicked.connect(handler); buttons.addWidget(button)
        layout.addLayout(buttons)
        self.program_table = QTableWidget(0, 6)
        self.program_table.setHorizontalHeaderLabels([translate("Ativo"), translate("Bloco"), translate("Dias"), translate("Horário"), translate("Prioridade"), translate("Fontes e filtros")])
        self.program_table.horizontalHeader().setStretchLastSection(True)
        self.program_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.program_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.program_table)
        self.tabs.addTab(page, ui_icon("autodj"),
                         translated_icon_label("🗓 Programação", "🗓"))

    def _build_history_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        header = QHBoxLayout(); header.addWidget(QLabel(translate("Histórico persistente: cada faixa iniciada salva horário e snapshot dos metadados.")))
        header.addStretch()
        clear_button = QPushButton(translated_icon_label("🧹 Limpar histórico", "🧹"))
        clear_button.setIcon(ui_icon("clear"))
        clear_button.clicked.connect(self.clear_history); header.addWidget(clear_button)
        layout.addLayout(header)
        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels([translate("Quando"), translate("Título"), translate("Artista"), translate("Álbum"), translate("Gênero"), translate("Ano"), translate("Arquivo")])
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.setSortingEnabled(False)
        for column, width in enumerate((145, 165, 130, 130, 100, 60)):
            self.history_table.setColumnWidth(column, width)
        layout.addWidget(self.history_table)
        page.setParent(self)
        page.hide()
        self._hidden_library_pages.append(page)

    @guarded
    def _tab_changed(self, index):
        if index == self.history_tab_index:
            self.refresh_history(incremental=self.history_table.rowCount() > 0)

    @guarded
    def schedule_history_refresh(self):
        library_panel = getattr(self.main_window, "library_panel", None)
        if library_panel is not None:
            library_panel.schedule_history_refresh()
            return
        # Keep QTableWidget work out of the reentrant media-state callback.
        if self.tabs.currentIndex() == self.history_tab_index and self.isVisible():
            self._history_refresh_timer.start(0)

    @guarded
    def refresh_history_incrementally(self):
        self.refresh_history(incremental=True)

    @guarded
    def showEvent(self, event):
        super().showEvent(event)
        if self.tabs.currentIndex() == self.history_tab_index:
            self.refresh_history(incremental=self.history_table.rowCount() > 0)

    @guarded
    def load_settings(self, config):
        config = config if isinstance(config, dict) else {}
        for control in (self.artist_cooldown, self.track_cooldown, self.queue_ahead):
            control.blockSignals(True)
        self.remove_played.blockSignals(True)
        self.artist_cooldown.setValue(_integer(config.get("artist_cooldown_minutes", 60), 60))
        self.track_cooldown.setValue(_integer(config.get("track_cooldown_minutes", 360), 360))
        self.queue_ahead.setValue(_integer(config.get("queue_ahead", 4), 4))
        self.remove_played.setChecked(bool(config.get("remove_played_from_queue", False)))
        for control in (self.artist_cooldown, self.track_cooldown, self.queue_ahead):
            control.blockSignals(False)
        self.remove_played.blockSignals(False)
        self.toggle.blockSignals(True)
        self.toggle.setChecked(bool(config.get("enabled", False)))
        self.toggle.blockSignals(False)
        self._set_toggle_label(bool(config.get("enabled", False)))

    def _set_toggle_label(self, enabled):
        source = "🟢 AutoDJ ligado" if enabled else "⏹ AutoDJ desligado"
        marker = "🟢" if enabled else "⏹"
        self.toggle.setText(translated_icon_label(source, marker))
        self.toggle.setIcon(ui_icon("autodj"))

    def save_settings(self, *_):
        self.main_window.autodj_config.update({
            "enabled": self.toggle.isChecked(),
            "artist_cooldown_minutes": self.artist_cooldown.value(),
            "track_cooldown_minutes": self.track_cooldown.value(),
            "queue_ahead": self.queue_ahead.value(),
            "remove_played_from_queue": self.remove_played.isChecked(),
        })
        if self.remove_played.isChecked():
            self.main_window.trim_played_queue(persist=False)
        self.main_window.save_state()

    def set_enabled(self, enabled):
        self._set_toggle_label(enabled)

    def set_status(self, text):
        text = str(text)
        display_text = translate(text)
        if self.status.text() != display_text:
            self.status.setText(display_text)

    @guarded
    def refresh_libraries(self):
        library_panel = getattr(self.main_window, "library_panel", None)
        if library_panel is not None:
            library_panel.refresh_libraries()
            self.refresh_programs()
            return
        libraries = self.library.libraries()
        self.library_table.blockSignals(True)
        self.library_table.setRowCount(len(libraries))
        for row, lib in enumerate(libraries):
            enabled = QTableWidgetItem("✓" if lib["enabled"] else "")
            enabled.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            enabled.setCheckState(Qt.Checked if lib["enabled"] else Qt.Unchecked)
            enabled.setData(Qt.UserRole, lib["id"])
            self.library_table.setItem(row, 0, enabled)
            name = QTableWidgetItem(lib["name"]); name.setData(Qt.UserRole, lib["id"])
            self.library_table.setItem(row, 1, name)
            self.library_table.setItem(row, 2, QTableWidgetItem(str(lib["track_count"])))
            self.library_table.setItem(row, 3, QTableWidgetItem(lib["root"]))
        self.library_table.blockSignals(False)
        self.refresh_programs()

    @guarded
    def library_toggled(self, item):
        if item.column() != 0:
            return
        library_id = item.data(Qt.UserRole)
        self.library.update_library(library_id, enabled=item.checkState() == Qt.Checked)
        self.refresh_programs(); self.main_window.save_state()
        self.main_window.autodj_last_signature = None
        self.main_window.fill_autodj_queue(bypass_throttle=True)

    @guarded
    def add_library(self):
        folder = QFileDialog.getExistingDirectory(self, translate("Adicionar pasta à Biblioteca"))
        if not folder:
            return
        default_name = Path(folder).name or translate("Pasta")
        name, accepted = QInputDialog.getText(
            self, translate("Nome da pasta na Biblioteca"), translate("Nome:"), text=default_name)
        if not accepted:
            return
        try:
            library_id = self.library.add_library(name, folder)
        except Exception as exc:
            show_warning(self, translate("Biblioteca"), translate(str(exc))); return
        self.refresh_libraries()
        self._start_scan(library_id)

    def selected_library_ids(self):
        return {item.data(Qt.UserRole) for item in self.library_table.selectedItems()
                if item.data(Qt.UserRole)}

    @guarded
    def scan_selected(self):
        ids = self.selected_library_ids()
        if not ids:
            ids = {lib["id"] for lib in self.library.libraries()}
        for library_id in ids:
            self._start_scan(library_id)

    def _start_scan(self, library_id):
        if library_id in self.scanning_libraries:
            return
        worker = _ScanWorker(self.library, library_id)
        self.workers.add(worker)
        self.scanning_libraries.add(library_id)
        worker.signals.finished.connect(self._scan_finished)
        self.set_status("Indexando pasta da Biblioteca em segundo plano…")
        # Library scans can parse many tags; keep them below interactive work
        # so the queue and playback controls stay responsive on busy PCs.
        self.pool.start(worker, priority=-1)

    @guarded
    def _scan_finished(self, library_id, result):
        self.scanning_libraries.discard(library_id)
        for worker in list(self.workers):
            if worker.library_id == library_id:
                self.workers.discard(worker)
        if isinstance(result, Exception):
            self.main_window.log_event(
                "error", translate("Falha ao indexar pasta da Biblioteca: {result}").format(
                    result=result))
            self.set_status(translate("Falha ao examinar pasta: {result}").format(result=result))
        else:
            added, unchanged, errors = result
            self.set_status(translate(
                "Exame concluído: {added} faixa(s) atualizada(s), {unchanged} sem alterações, {errors} erro(s)."
            ).format(added=added, unchanged=unchanged, errors=errors))
            self.main_window.log_event("info", translate(
                "Pasta examinada na Biblioteca: {added} faixa(s) atualizada(s), {errors} erro(s)."
            ).format(added=added, errors=errors))
        self.refresh_libraries(); self.main_window.save_state()
        self.main_window.autodj_last_signature = None
        self.main_window.fill_autodj_queue(bypass_throttle=True)

    @guarded
    def remove_selected(self):
        ids = self.selected_library_ids()
        if not ids:
            return
        answer = ask_question(
            self, translate("Remover pastas da Biblioteca"),
            translate("Remover pasta(s) da Biblioteca e seus índices? O histórico de reprodução será preservado."))
        if answer != QMessageBox.Yes:
            return
        for library_id in ids:
            self.library.remove_library(library_id)
        # Keep deleted IDs in program selections. Empty selections mean “all
        # enabled libraries”; pruning the last selected ID would silently
        # widen a scheduled block to the entire music library. The editor shows
        # missing IDs and lets the user remove or replace them explicitly.
        self.refresh_libraries(); self.main_window.save_state()
        self.main_window.autodj_last_signature = None
        self.main_window.fill_autodj_queue(bypass_throttle=True)

    @guarded
    def refresh_programs(self):
        programs = self.main_window.autodj_programs
        libraries = {str(lib["id"]): lib for lib in self.library.libraries()}
        collections = {str(item["id"]): item for item in self.library.collections()}
        groups = {str(item["id"]): item for item in self.library.rotation_groups()}
        self.program_table.setRowCount(len(programs))
        for row, program in enumerate(programs):
            if not isinstance(program, dict):
                continue
            active = QTableWidgetItem("✓" if program.get("enabled", True) else "")
            active.setData(Qt.UserRole, program.get("id"))
            self.program_table.setItem(row, 0, active)
            self.program_table.setItem(
                row, 1, QTableWidgetItem(str(program.get("name") or translate("Bloco"))))
            day_indices = set()
            raw_days = program.get("days", [])
            if not isinstance(raw_days, (list, tuple, set)):
                raw_days = []
            for day in raw_days:
                try:
                    day = int(day)
                except (TypeError, ValueError, OverflowError):
                    continue
                if 0 <= day < 7:
                    day_indices.add(day)
            days = ", ".join(translate(DAY_NAMES[d]) for d in sorted(day_indices))
            self.program_table.setItem(row, 2, QTableWidgetItem(days))
            self.program_table.setItem(row, 3, QTableWidgetItem(f"{program.get('start')}–{program.get('end')}"))
            filters = program.get("filters", {})
            if not isinstance(filters, dict):
                filters = {}
            bits = []
            for key, label in (("genres", "Gênero"), ("artists", "Artistas"),
                               ("include_keywords", "Contém")):
                value = filters.get(key)
                if value:
                    bits.append(f"{translate(label)}: {value}")
            library_ids = program.get("library_ids", [])
            if not isinstance(library_ids, (list, tuple, set)):
                library_ids = []
            requested_ids = list(dict.fromkeys(
                str(value) for value in library_ids if value is not None))
            folder_names = []
            for library_id in requested_ids:
                library = libraries.get(library_id)
                if library:
                    name = str(library.get("name") or library_id)
                    if not library.get("enabled", 1):
                        name += f" ({translate('Desativado')})"
                else:
                    name = translate("Pasta indisponível ({id})").format(id=library_id)
                folder_names.append(name)

            sources = program.get("sources", [])
            if not isinstance(sources, (list, tuple)):
                sources = []
            source_labels = {
                "library": "Pasta indexada", "collection": "Coleção",
                "group": "Grupo de rotação", "tag": "Etiqueta",
            }
            available_tags = set(self.library.unique_tags())
            source_names = []
            for source in sources:
                if not isinstance(source, dict):
                    continue
                kind = str(source.get("type", ""))
                source_id = str(source.get("id", ""))
                if kind not in source_labels or not source_id:
                    continue
                unavailable = False
                if kind == "library":
                    item = libraries.get(source_id)
                    if item:
                        name = str(item.get("name") or source_id)
                        if not item.get("enabled", 1):
                            name += f" ({translate('Desativado')})"
                    else:
                        unavailable = True
                elif kind == "collection":
                    item = collections.get(source_id)
                    if item:
                        name = str(item.get("name") or source_id)
                    else:
                        unavailable = True
                elif kind == "group":
                    item = groups.get(source_id)
                    if item:
                        name = str(item.get("name") or source_id)
                    else:
                        unavailable = True
                elif kind == "tag":
                    name = source_id
                    unavailable = source_id not in available_tags
                else:
                    continue
                try:
                    weight = max(0.01, min(100.0, float(source.get("weight", 1))))
                    weight_text = f"{weight:g}"
                except (TypeError, ValueError, OverflowError):
                    weight_text = "1"
                source_label = (
                    translate("Fonte indisponível ({type}: {id})").format(
                        type=translate(source_labels[kind]), id=source_id)
                    if unavailable else f"{translate(source_labels[kind])}: {name}")
                source_names.append(f"{source_label} ({translate('Peso')} {weight_text})")

            if folder_names:
                bits.append(f"{translate('Pastas')}: {', '.join(folder_names)}")
            if source_names:
                bits.append(f"{translate('Fontes')}: {', '.join(source_names)}")
            if not requested_ids and not source_names:
                bits.append(
                    f"{translate('Pastas')}: {translate('Todas as pastas habilitadas para o AutoDJ')}")
            self.program_table.setItem(row, 4, QTableWidgetItem(str(program.get("priority", 0))))
            self.program_table.setItem(row, 5, QTableWidgetItem(" • ".join(bits)))
        self.program_table.resizeColumnsToContents()

    @guarded
    def add_program(self):
        dialog = ProgramDialog(
            self.library.libraries(), parent=self,
            collections=self.library.collections(), groups=self.library.rotation_groups(),
            tags=self.library.unique_tags())
        if dialog.exec() == QDialog.Accepted:
            self.main_window.autodj_programs.append(dialog.value())
            self.refresh_programs(); self.main_window.save_state()
            self.main_window.autodj_last_signature = None
            self.main_window.fill_autodj_queue(bypass_throttle=True)

    def _selected_program_row(self):
        return self.program_table.currentRow()

    @guarded
    def edit_program(self):
        row = self._selected_program_row()
        if row < 0:
            return
        current = self.main_window.autodj_programs[row]
        dialog = ProgramDialog(
            self.library.libraries(), current, self,
            collections=self.library.collections(), groups=self.library.rotation_groups(),
            tags=self.library.unique_tags())
        if dialog.exec() == QDialog.Accepted:
            self.main_window.autodj_programs[row] = dialog.value(current)
            self.refresh_programs(); self.main_window.save_state()
            self.main_window.autodj_last_signature = None
            self.main_window.fill_autodj_queue(bypass_throttle=True)

    @guarded
    def delete_program(self):
        row = self._selected_program_row()
        if row >= 0:
            del self.main_window.autodj_programs[row]
            self.refresh_programs(); self.main_window.save_state()
            self.main_window.autodj_last_signature = None
            self.main_window.fill_autodj_queue(bypass_throttle=True)

    @guarded
    def refresh_history(self, incremental=False):
        library_panel = getattr(getattr(self, "main_window", None), "library_panel", None)
        if library_panel is not None:
            library_panel.refresh_history()
            return
        # Track starts are recorded from playback callbacks. Rebuilding up to
        # 1,000 rows (7,000 table items) while the history tab is hidden can
        # stall the UI at every song change, so refresh lazily on demand. A
        # modal dialog can cover a visible AutoDJ panel; defer table work until
        # the dialog closes rather than doing it behind the dialog.
        if self.tabs.currentIndex() != self.history_tab_index or not self.isVisible():
            return
        application = QApplication.instance()
        active_modal = getattr(application, "activeModalWidget", None)
        if callable(active_modal) and active_modal() is not None:
            self._history_refresh_timer.start(1000)
            return
        self._history_refresh_timer.stop()
        latest_id = None
        if incremental and self.history_table.rowCount():
            first_item = self.history_table.item(0, 0)
            try:
                latest_id = int(first_item.data(Qt.UserRole + 1)) if first_item else None
            except (TypeError, ValueError, OverflowError):
                latest_id = None
        if latest_id is not None and hasattr(self.library, "history_after_id"):
            rows = self.library.history_after_id(latest_id, 1000)
        else:
            rows = self.library.recent_history(1000)

        def set_entry(row, entry):
            try:
                when = datetime.fromtimestamp(float(entry["played_at"])).astimezone().strftime(
                    "%d/%m/%Y %H:%M:%S")
            except (OverflowError, OSError, TypeError, ValueError):
                when = "Data inválida"
            values = (when, entry["title"], entry["artist"], entry["album"], entry["genre"],
                      entry["year"], entry["path"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                if column == 0:
                    item.setData(Qt.UserRole, entry["played_at"])
                    if "id" in entry:
                        item.setData(Qt.UserRole + 1, entry["id"])
                self.history_table.setItem(row, column, item)

        if incremental and latest_id is not None and self.history_table.rowCount():
            if not rows:
                return
            first_item = self.history_table.item(0, 0)
            try:
                new_rows = [entry for entry in rows if int(entry["id"]) > latest_id]
                latest_played_at = float(first_item.data(Qt.UserRole))
                if any(float(entry["played_at"]) < latest_played_at
                       for entry in new_rows):
                    # Keep the full refresh as the fallback for imported or
                    # corrected history whose timestamp is older than the top row.
                    new_rows = None
            except (KeyError, TypeError, ValueError, OverflowError):
                new_rows = None
            if new_rows is not None:
                for entry in reversed(new_rows):
                    self.history_table.insertRow(0)
                    set_entry(0, entry)
                    if self.history_table.rowCount() > 1000:
                        self.history_table.removeRow(self.history_table.rowCount() - 1)
                return
            # An imported or corrected timestamp can place a newer ID below
            # existing rows. Re-fetch the full display window before rebuilding.
            rows = self.library.recent_history(1000)

        self.history_table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            set_entry(row, entry)

    @guarded
    def clear_history(self):
        if ask_question(self, translate("Limpar histórico"), translate("Apagar o histórico de músicas tocadas? As regras de repetição perderão essa referência.")) != QMessageBox.Yes:
            return
        self.library.clear_history()
        self.refresh_history(); self.main_window.save_state()
