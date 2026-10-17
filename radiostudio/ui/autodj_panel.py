"""Libraries, weekly AutoDJ programming, and searchable play history UI."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QTime, QThreadPool

from ..services.autodj import DAY_NAMES, FILTER_FIELDS, active_program
from ..runtime_safety import guarded
from .icons import apply_app_icon


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
    def __init__(self, libraries, program=None, parent=None):
        super().__init__(parent)
        apply_app_icon(self)
        self.setWindowTitle("Programação AutoDJ")
        self.resize(570, 610)
        program = program or {}
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(_text(program.get("name", "Novo bloco"), "Novo bloco"))
        form.addRow("Nome do bloco", self.name)
        self.enabled = QCheckBox("Bloco ativo")
        self.enabled.setChecked(bool(program.get("enabled", True)))
        form.addRow("Estado", self.enabled)
        self.start = self._time_edit(_text(program.get("start", "00:00"), "00:00"))
        self.end = self._time_edit(_text(program.get("end", "01:00"), "01:00"))
        hours = QHBoxLayout()
        hours.addWidget(QLabel("Das")); hours.addWidget(self.start)
        hours.addWidget(QLabel("até")); hours.addWidget(self.end); hours.addStretch()
        form.addRow("Intervalo", hours)
        self.priority = QSpinBox(); self.priority.setRange(-100, 100)
        self.priority.setValue(_integer(program.get("priority", 0)))
        form.addRow("Prioridade (maior vence)", self.priority)
        root.addLayout(form)

        day_box = QGroupBox("Dias da semana")
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
            check = QCheckBox(label); check.setChecked(index in saved_days)
            day_layout.addWidget(check); self.days.append(check)
        root.addWidget(day_box)

        library_box = QGroupBox("Bibliotecas (sem seleção = todas habilitadas)")
        library_layout = QVBoxLayout(library_box)
        self.library_list = QListWidget()
        raw_libraries = program.get("library_ids", [])
        saved_libraries = ({str(value) for value in raw_libraries}
                           if isinstance(raw_libraries, (list, tuple, set)) else set())
        for library in libraries:
            if not library.get("enabled", 1):
                continue
            item = QListWidgetItem(f"📚 {library['name']} — {library['root']}")
            item.setData(Qt.UserRole, library["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if library["id"] in saved_libraries else Qt.Unchecked)
            self.library_list.addItem(item)
        library_layout.addWidget(self.library_list)
        root.addWidget(library_box, 1)

        filters_box = QGroupBox("Filtros de metadados")
        filters = QFormLayout(filters_box)
        old_filters = program.get("filters", {})
        if not isinstance(old_filters, dict):
            old_filters = {}
        self.genres = QLineEdit(_text(old_filters.get("genres"))); self.genres.setPlaceholderText("Rock, Pop, Jazz")
        self.artists = QLineEdit(_text(old_filters.get("artists"))); self.artists.setPlaceholderText("Artistas exatos, separados por vírgula")
        self.include = QLineEdit(_text(old_filters.get("include_keywords"))); self.include.setPlaceholderText("Todas as palavras precisam aparecer")
        self.exclude = QLineEdit(_text(old_filters.get("exclude_keywords"))); self.exclude.setPlaceholderText("Qualquer uma destas palavras elimina a faixa")
        filters.addRow("Gêneros", self.genres)
        filters.addRow("Artistas", self.artists)
        filters.addRow("Título/álbum/artista contém", self.include)
        filters.addRow("Excluir palavra-chave", self.exclude)
        year_row = QHBoxLayout()
        self.year_from = QSpinBox(); self.year_from.setRange(0, 9999); self.year_from.setSpecialValueText("Sem mínimo")
        self.year_to = QSpinBox(); self.year_to.setRange(0, 9999); self.year_to.setSpecialValueText("Sem máximo")
        self.year_from.setValue(_integer(old_filters.get("year_from", 0)))
        self.year_to.setValue(_integer(old_filters.get("year_to", 0)))
        year_row.addWidget(self.year_from); year_row.addWidget(QLabel("até")); year_row.addWidget(self.year_to); year_row.addStretch()
        filters.addRow("Ano", year_row)
        self.field_checks = {}
        fields_row = QGridLayout()
        old_fields = old_filters.get("keyword_fields", ["title", "album", "artist", "albumartist", "genre", "composer", "year"])
        if not isinstance(old_fields, (list, tuple, set)):
            old_fields = []
        labels = {"title": "Título", "artist": "Artista", "album": "Álbum", "albumartist": "Artista do álbum",
                  "genre": "Gênero", "composer": "Compositor", "year": "Ano", "path": "Nome/caminho"}
        for index, field in enumerate(FILTER_FIELDS):
            check = QCheckBox(labels[field]); check.setChecked(field in old_fields)
            fields_row.addWidget(check, index // 4, index % 4); self.field_checks[field] = check
        filters.addRow("Buscar palavras em", fields_row)
        root.addWidget(filters_box)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
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
            QMessageBox.warning(self, "Programação", "Informe um nome para o bloco.")
            return
        if self.start.time() == self.end.time():
            QMessageBox.warning(self, "Programação", "O início e o fim precisam ser diferentes.")
            return
        if not any(check.isChecked() for check in self.days):
            QMessageBox.warning(self, "Programação", "Selecione pelo menos um dia da semana.")
            return
        if self.year_from.value() and self.year_to.value() and self.year_from.value() > self.year_to.value():
            QMessageBox.warning(self, "Programação", "O ano inicial não pode ser maior que o ano final.")
            return
        super().accept()

    def value(self, previous=None):
        selected = [self.library_list.item(i).data(Qt.UserRole)
                    for i in range(self.library_list.count())
                    if self.library_list.item(i).checkState() == Qt.Checked]
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
                "priority": self.priority.value(), "library_ids": selected, "filters": filters}


class AutoDJPanel(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.library = main_window.music_library
        self.workers = set()
        self.scanning_libraries = set()
        self.pool = QThreadPool.globalInstance()
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        self.toggle = QPushButton("⏹ AutoDJ desligado")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self.main_window.set_autodj_enabled)
        header.addWidget(self.toggle)
        self.status = QLabel("Configure bibliotecas e blocos musicais")
        self.status.setWordWrap(True)
        header.addWidget(self.status, 1)
        self.fill_button = QPushButton("🎲 Completar fila")
        self.fill_button.clicked.connect(self.main_window.fill_autodj_queue)
        header.addWidget(self.fill_button)
        root.addLayout(header)

        policy_box = QGroupBox("Regras de repetição")
        policy = QHBoxLayout(policy_box)
        policy.addWidget(QLabel("Evitar artista por"))
        self.artist_cooldown = QSpinBox(); self.artist_cooldown.setRange(0, 10080); self.artist_cooldown.setSuffix(" min")
        self.artist_cooldown.setToolTip("0 desativa esta regra")
        policy.addWidget(self.artist_cooldown)
        policy.addWidget(QLabel("Faixa por"))
        self.track_cooldown = QSpinBox(); self.track_cooldown.setRange(0, 43200); self.track_cooldown.setSuffix(" min")
        self.track_cooldown.setToolTip("0 desativa esta regra")
        policy.addWidget(self.track_cooldown)
        policy.addWidget(QLabel("Faixas à frente"))
        self.queue_ahead = QSpinBox(); self.queue_ahead.setRange(1, 30)
        policy.addWidget(self.queue_ahead); policy.addStretch()
        for control in (self.artist_cooldown, self.track_cooldown, self.queue_ahead):
            control.valueChanged.connect(self.save_settings)
        root.addWidget(policy_box)
        self.remove_played = QCheckBox("🧹 Remover da fila as faixas anteriores à atual")
        self.remove_played.setToolTip(
            "Ao começar uma faixa, remove as linhas anteriores da Now Playing. "
            "A faixa atual, as próximas e o histórico de reprodução são preservados.")
        self.remove_played.toggled.connect(self.save_settings)
        root.addWidget(self.remove_played)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self._build_libraries_tab()
        self._build_program_tab()
        self._build_history_tab()
        self.load_settings(main_window.autodj_config)
        self.refresh_libraries()
        self.refresh_programs()
        self.refresh_history()

    def _build_libraries_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        buttons = QHBoxLayout()
        for label, handler in (("➕ Adicionar pasta", self.add_library),
                               ("🔄 Examinar", self.scan_selected),
                               ("🗑 Remover", self.remove_selected)):
            button = QPushButton(label); button.clicked.connect(handler); buttons.addWidget(button)
        layout.addLayout(buttons)
        self.library_table = QTableWidget(0, 4)
        self.library_table.setHorizontalHeaderLabels(["AutoDJ", "Biblioteca", "Faixas", "Pasta"])
        self.library_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.library_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.library_table.horizontalHeader().setStretchLastSection(True)
        self.library_table.setColumnWidth(0, 55); self.library_table.setColumnWidth(1, 120)
        self.library_table.setColumnWidth(2, 55)
        self.library_table.itemChanged.connect(self.library_toggled)
        layout.addWidget(self.library_table)
        self.tabs.addTab(page, "📚 Bibliotecas")

    def _build_program_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        description = QLabel("Os blocos ativos escolhem a música pelo horário local; fora deles, o AutoDJ mistura todas as bibliotecas habilitadas.")
        description.setWordWrap(True); layout.addWidget(description)
        buttons = QHBoxLayout()
        for label, handler in (("➕ Novo bloco", self.add_program), ("✏️ Editar", self.edit_program),
                               ("🗑 Excluir", self.delete_program)):
            button = QPushButton(label); button.clicked.connect(handler); buttons.addWidget(button)
        layout.addLayout(buttons)
        self.program_table = QTableWidget(0, 6)
        self.program_table.setHorizontalHeaderLabels(["Ativo", "Bloco", "Dias", "Horário", "Prioridade", "Filtros / bibliotecas"])
        self.program_table.horizontalHeader().setStretchLastSection(True)
        self.program_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.program_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.program_table)
        self.tabs.addTab(page, "🗓 Programação")

    def _build_history_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        header = QHBoxLayout(); header.addWidget(QLabel("Histórico persistente: cada faixa iniciada salva horário e snapshot dos metadados."))
        header.addStretch()
        clear_button = QPushButton("🧹 Limpar histórico")
        clear_button.clicked.connect(self.clear_history); header.addWidget(clear_button)
        layout.addLayout(header)
        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(["Quando", "Título", "Artista", "Álbum", "Gênero", "Ano", "Arquivo"])
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.setSortingEnabled(False)
        for column, width in enumerate((145, 165, 130, 130, 100, 60)):
            self.history_table.setColumnWidth(column, width)
        layout.addWidget(self.history_table)
        self.tabs.addTab(page, "🕘 Histórico")

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
        self.toggle.setText("🟢 AutoDJ ligado" if enabled else "⏹ AutoDJ desligado")

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
        self.status.setText(text)

    @guarded
    def refresh_libraries(self):
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
        self.main_window.fill_autodj_queue()

    @guarded
    def add_library(self):
        folder = QFileDialog.getExistingDirectory(self, "Adicionar biblioteca musical")
        if not folder:
            return
        default_name = Path(folder).name or "Biblioteca"
        name, accepted = QInputDialog.getText(self, "Nome da biblioteca", "Nome:", text=default_name)
        if not accepted:
            return
        try:
            library_id = self.library.add_library(name, folder)
        except Exception as exc:
            QMessageBox.warning(self, "Biblioteca", str(exc)); return
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
        self.set_status("Indexando biblioteca em segundo plano…")
        self.pool.start(worker)

    @guarded
    def _scan_finished(self, library_id, result):
        self.scanning_libraries.discard(library_id)
        for worker in list(self.workers):
            if worker.library_id == library_id:
                self.workers.discard(worker)
        if isinstance(result, Exception):
            self.main_window.log_event("error", f"Falha ao indexar biblioteca: {result}")
            self.set_status(f"Falha no exame: {result}")
        else:
            added, unchanged, errors = result
            self.set_status(f"Exame concluído: {added} atualizado(s), {unchanged} sem alterações, {errors} erro(s).")
            self.main_window.log_event("info", f"Biblioteca examinada: {added} faixa(s) atualizada(s), {errors} erro(s).")
        self.refresh_libraries(); self.main_window.save_state()
        self.main_window.autodj_last_signature = None
        self.main_window.fill_autodj_queue()

    @guarded
    def remove_selected(self):
        ids = self.selected_library_ids()
        if not ids:
            return
        answer = QMessageBox.question(self, "Remover bibliotecas", "Remover biblioteca(s) e seus índices? O histórico de reprodução será preservado.")
        if answer != QMessageBox.Yes:
            return
        for library_id in ids:
            self.library.remove_library(library_id)
        for program in self.main_window.autodj_programs:
            if not isinstance(program, dict):
                continue
            selected = program.get("library_ids", [])
            if not isinstance(selected, (list, tuple, set)):
                selected = []
            program["library_ids"] = [value for value in selected if value not in ids]
        self.refresh_libraries(); self.main_window.save_state()
        self.main_window.autodj_last_signature = None
        self.main_window.fill_autodj_queue()

    @guarded
    def refresh_programs(self):
        programs = self.main_window.autodj_programs
        libraries = {lib["id"]: lib for lib in self.library.libraries()}
        self.program_table.setRowCount(len(programs))
        for row, program in enumerate(programs):
            if not isinstance(program, dict):
                continue
            active = QTableWidgetItem("✓" if program.get("enabled", True) else "")
            active.setData(Qt.UserRole, program.get("id"))
            self.program_table.setItem(row, 0, active)
            self.program_table.setItem(row, 1, QTableWidgetItem(str(program.get("name") or "Bloco")))
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
            days = ", ".join(DAY_NAMES[d] for d in sorted(day_indices))
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
                    bits.append(f"{label}: {value}")
            library_ids = program.get("library_ids", [])
            if not isinstance(library_ids, (list, tuple, set)):
                library_ids = []
            selected = [libraries[str(value)]["name"] for value in library_ids
                        if str(value) in libraries]
            bits.append("Bibliotecas: " + (", ".join(selected) if selected else "todas habilitadas"))
            self.program_table.setItem(row, 4, QTableWidgetItem(str(program.get("priority", 0))))
            self.program_table.setItem(row, 5, QTableWidgetItem(" • ".join(bits)))
        self.program_table.resizeColumnsToContents()

    @guarded
    def add_program(self):
        dialog = ProgramDialog(self.library.libraries(), parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.main_window.autodj_programs.append(dialog.value())
            self.refresh_programs(); self.main_window.save_state()
            self.main_window.autodj_last_signature = None; self.main_window.fill_autodj_queue()

    def _selected_program_row(self):
        return self.program_table.currentRow()

    @guarded
    def edit_program(self):
        row = self._selected_program_row()
        if row < 0:
            return
        current = self.main_window.autodj_programs[row]
        dialog = ProgramDialog(self.library.libraries(), current, self)
        if dialog.exec() == QDialog.Accepted:
            self.main_window.autodj_programs[row] = dialog.value(current)
            self.refresh_programs(); self.main_window.save_state()
            self.main_window.autodj_last_signature = None; self.main_window.fill_autodj_queue()

    @guarded
    def delete_program(self):
        row = self._selected_program_row()
        if row >= 0:
            del self.main_window.autodj_programs[row]
            self.refresh_programs(); self.main_window.save_state()
            self.main_window.autodj_last_signature = None; self.main_window.fill_autodj_queue()

    @guarded
    def refresh_history(self):
        rows = self.library.recent_history(1000)
        self.history_table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
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
                self.history_table.setItem(row, column, item)

    @guarded
    def clear_history(self):
        if QMessageBox.question(self, "Limpar histórico", "Apagar o histórico de músicas tocadas? As regras de repetição perderão essa referência.") != QMessageBox.Yes:
            return
        self.library.clear_history()
        self.refresh_history(); self.main_window.save_state()
