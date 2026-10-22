"""Shared, searchable music catalog, organization, history, and metadata editor."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..i18n import translate, translate_dialog_buttons
from ..runtime_safety import guarded
from ..services.audio_paths import audio_path_key, same_audio_path
from ..services.embedded_metadata import EmbeddedMetadataError
from .icons import translated_icon_label, ui_icon
from .message_boxes import ask_question, show_warning
from .metadata_editor import MetadataEditorDialog


class _LibrarySignals(QObject):
    finished = Signal(object)


class _LibraryWorker(QRunnable):
    def __init__(self, callback, payload=None):
        super().__init__()
        self.callback = callback
        self.payload = payload
        self.signals = _LibrarySignals()

    @Slot()
    def run(self):
        try:
            result = self.callback(self.payload)
        except Exception as exc:
            result = exc
        self.signals.finished.emit(result)


class _GroupPicker(QDialog):
    def __init__(self, groups, selected=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("Atribuir grupos"))
        layout = QVBoxLayout(self)
        self.checks = []
        selected = {str(value) for value in selected}
        for group in groups:
            check = QCheckBox(translate("Grupo: {name} (peso {weight:g})").format(
                name=group["name"], weight=group["weight"]))
            check.setProperty("group_id", str(group["id"]))
            check.setChecked(str(group["id"]) in selected)
            layout.addWidget(check)
            self.checks.append(check)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        translate_dialog_buttons(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self):
        return [str(check.property("group_id")) for check in self.checks if check.isChecked()]


class LibraryPanel(QWidget):
    """A first-class catalog shared with AutoDJ instead of an AutoDJ sub-page."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.library = main_window.music_library
        self.pool = QThreadPool.globalInstance()
        self._workers = set()
        self._scanning = set()
        self._writing_tags = False
        self._metadata_operation_running = False
        self._has_pending_tag_writes = bool(self.library.pending_file_tag_writes())
        self._history_last_id = 0
        self._history_refresh_timer = QTimer(self)
        self._history_refresh_timer.setSingleShot(True)
        self._history_refresh_timer.timeout.connect(self.refresh_history)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self._build_catalog_tab()
        self._build_folders_tab()
        self._build_organization_tab()
        self._build_history_tab()
        self.tabs.currentChanged.connect(self._tab_changed)
        self.pending_timer = QTimer(self)
        self.pending_timer.setSingleShot(True)
        self.pending_timer.setInterval(250)
        self.pending_timer.timeout.connect(self._process_pending_writes)
        for player in (getattr(main_window, "player", None),
                       getattr(main_window, "mix_player", None)):
            if player is not None:
                player.playbackStateChanged.connect(self._schedule_pending_write_check)
                player.mediaStatusChanged.connect(self._schedule_pending_write_check)
        if self._has_pending_tag_writes:
            self.pending_timer.start()
        self.refresh_libraries()
        self.refresh_organization()
        self.refresh_catalog()

    def _build_catalog_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(translate("Pesquisar faixas..."))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._schedule_catalog_refresh)
        filters.addWidget(self.search, 1)
        self.library_filter = QComboBox()
        self.library_filter.currentIndexChanged.connect(self.refresh_catalog)
        filters.addWidget(self.library_filter)
        self.collection_filter = QComboBox()
        self.collection_filter.currentIndexChanged.connect(self.refresh_catalog)
        filters.addWidget(self.collection_filter)
        self.tag_filter = QComboBox()
        self.tag_filter.currentIndexChanged.connect(self.refresh_catalog)
        filters.addWidget(self.tag_filter)
        self.group_filter = QComboBox()
        self.group_filter.currentIndexChanged.connect(self.refresh_catalog)
        filters.addWidget(self.group_filter)
        self.sort = QComboBox()
        for label, field in (("Título", "title"), ("Artista", "artist"),
                             ("Álbum", "album"), ("Artista do álbum", "albumartist"),
                             ("Compositor", "composer"), ("Gênero", "genre"),
                             ("Ano", "year"), ("Duração", "duration"),
                             ("Etiquetas", "tags"), ("Coleções", "collections"),
                             ("Grupos", "groups"), ("Pasta", "folder"),
                             ("Arquivo", "path")):
            self.sort.addItem(translate(label), field)
        self.sort.currentIndexChanged.connect(self.refresh_catalog)
        filters.addWidget(self.sort)
        layout.addLayout(filters)

        buttons = QHBoxLayout()
        for label, handler, icon in (
                ("Adicionar à fila", self.enqueue_selected, "add"),
                ("Reproduzir agora", self.play_selected, "play"),
                ("Pré-escutar", self.preview_selected, "headphones"),
                ("Abrir pasta", self.open_selected_folder, "folder_open"),
                ("Editar metadados", self.edit_selected_metadata, "edit"),
                ("Etiquetas", self.edit_selected_tags, "tag"),
                ("Desfazer metadados", self.undo_selected_metadata, "undo")):
            button = QPushButton(translate(label))
            button.setIcon(ui_icon(icon))
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.catalog_table = QTableWidget(0, 10)
        self.catalog_table.setHorizontalHeaderLabels([
            translate("Título"), translate("Artista"), translate("Álbum"),
            translate("Gênero"), translate("Ano"), translate("Duração"),
            translate("Etiquetas"), translate("Coleções"), translate("Grupos"),
            translate("Arquivo")])
        self.catalog_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.catalog_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.catalog_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.catalog_table.setSortingEnabled(False)
        self.catalog_table.horizontalHeader().setStretchLastSection(True)
        self.catalog_table.doubleClicked.connect(self.enqueue_selected)
        layout.addWidget(self.catalog_table, 1)
        self.catalog_status = QLabel()
        layout.addWidget(self.catalog_status)
        self.catalog_refresh_timer = QTimer(self)
        self.catalog_refresh_timer.setSingleShot(True)
        self.catalog_refresh_timer.setInterval(180)
        self.catalog_refresh_timer.timeout.connect(self.refresh_catalog)
        self.tabs.addTab(page, ui_icon("libraries"), translate("Catálogo"))

    def _build_folders_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        buttons = QHBoxLayout()
        for label, handler, icon in (("Adicionar pasta", self.add_library, "add_folder"),
                                     ("Examinar", self.scan_selected_libraries, "refresh"),
                                     ("Remover pasta", self.remove_selected_libraries, "remove")):
            button = QPushButton(translate(label))
            button.setIcon(ui_icon(icon))
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.library_table = QTableWidget(0, 4)
        self.library_table.setHorizontalHeaderLabels([
            translate("Ativa"), translate("Pasta indexada"), translate("Faixas"),
            translate("Caminho")])
        self.library_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.library_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.library_table.horizontalHeader().setStretchLastSection(True)
        self.library_table.itemChanged.connect(self.library_toggled)
        layout.addWidget(self.library_table)
        self.folders_status = QLabel(translate(
            "A Biblioteca reúne as faixas das pastas indexadas e ativas. Desativar uma pasta a remove do catálogo e da seleção do AutoDJ; os arquivos permanecem no disco."))
        self.folders_status.setWordWrap(True)
        layout.addWidget(self.folders_status)
        self.tabs.addTab(page, ui_icon("folder_open"), translate("Pastas indexadas"))

    def _build_organization_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        description = QLabel(translate(
            "Coleções, etiquetas e grupos organizam o acervo e podem ser usados nos blocos do AutoDJ."))
        description.setWordWrap(True)
        layout.addWidget(description)
        collection_box = QGroupBox(translate("Coleções"))
        collection_layout = QVBoxLayout(collection_box)
        self.collection_table = QTableWidget(0, 3)
        self.collection_table.setHorizontalHeaderLabels([
            translate("Coleção"), translate("Tipo"), translate("Faixas")])
        self.collection_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.collection_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.collection_table.horizontalHeader().setStretchLastSection(True)
        collection_layout.addWidget(self.collection_table)
        collection_buttons = QHBoxLayout()
        for label, handler in (("Coleção manual", self.create_manual_collection),
                               ("Coleção inteligente", self.create_smart_collection),
                               ("Adicionar à coleção", self.add_selected_to_collection),
                               ("Remover coleção", self.delete_selected_collection)):
            button = QPushButton(translate(label))
            button.clicked.connect(handler)
            collection_buttons.addWidget(button)
        collection_layout.addLayout(collection_buttons)
        layout.addWidget(collection_box, 1)

        group_box = QGroupBox(translate("Grupos de rotação"))
        group_layout = QVBoxLayout(group_box)
        self.group_table = QTableWidget(0, 3)
        self.group_table.setHorizontalHeaderLabels([
            translate("Nome do grupo"), translate("Peso"), translate("Faixas")])
        self.group_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.group_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.group_table.horizontalHeader().setStretchLastSection(True)
        group_layout.addWidget(self.group_table)
        group_buttons = QHBoxLayout()
        for label, handler in (("Criar grupo de rotação", self.create_rotation_group),
                               ("Atribuir grupos", self.assign_selected_groups),
                               ("Remover grupo", self.delete_selected_group)):
            button = QPushButton(translate(label))
            button.clicked.connect(handler)
            group_buttons.addWidget(button)
        group_layout.addLayout(group_buttons)
        layout.addWidget(group_box, 1)
        self.tabs.addTab(page, ui_icon("tag"), translate("Coleções e grupos"))

    def _build_history_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        header = QHBoxLayout()
        label = QLabel(translate(
            "Histórico persistente: cada faixa iniciada salva horário e snapshot dos metadados."))
        label.setWordWrap(True)
        header.addWidget(label, 1)
        self.clear_history_button = QPushButton(translate("Limpar histórico"))
        self.clear_history_button.setIcon(ui_icon("clear"))
        self.clear_history_button.clicked.connect(self.clear_history)
        header.addWidget(self.clear_history_button)
        layout.addLayout(header)
        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels([
            translate("Quando"), translate("Título"), translate("Artista"),
            translate("Álbum"), translate("Gênero"), translate("Ano"),
            translate("Arquivo")])
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.history_table)
        self.tabs.addTab(page, ui_icon("history"), translate("Histórico de reprodução"))

    def _schedule_catalog_refresh(self, *_args):
        self.catalog_refresh_timer.start()

    def _combo_value(self, combo):
        return combo.currentData() or None

    @guarded
    def refresh_catalog(self, *_args):
        if not hasattr(self, "catalog_table"):
            return
        libraries = self.library_filter.currentData() if self.library_filter.count() else None
        collection = self.collection_filter.currentData() if self.collection_filter.count() else None
        tag = self.tag_filter.currentData() if self.tag_filter.count() else None
        group_id = self.group_filter.currentData() if self.group_filter.count() else None
        tracks = self.library.catalog_tracks(
            search=self.search.text(), library_id=libraries, collection_id=collection,
            tag=tag, group_id=group_id, sort_by=self.sort.currentData() or "title")
        self.catalog_table.setUpdatesEnabled(False)
        try:
            self.catalog_table.setRowCount(len(tracks))
            for row, track in enumerate(tracks):
                values = (track.get("title", ""), track.get("artist", ""),
                          track.get("album", ""), track.get("genre", ""),
                          track.get("year", ""), self._duration(track.get("duration", 0)),
                          ", ".join(track.get("tags", [])),
                          ", ".join(track.get("collection_names", [])),
                          ", ".join(track.get("group_names", [])), Path(track["path"]).name)
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value or ""))
                    if column == 0:
                        item.setData(Qt.UserRole, track["path"])
                        item.setToolTip(track["path"])
                    self.catalog_table.setItem(row, column, item)
        finally:
            self.catalog_table.setUpdatesEnabled(True)
            self.catalog_table.viewport().update()
        self.catalog_status.setText(translate("{count} faixas encontradas").format(
            count=len(tracks)))

    @staticmethod
    def _duration(value):
        try:
            seconds = max(0, int(float(value or 0)))
        except (TypeError, ValueError, OverflowError):
            seconds = 0
        return f"{seconds // 60:02}:{seconds % 60:02}"

    def selected_paths(self):
        rows = sorted({index.row() for index in self.catalog_table.selectionModel().selectedRows()})
        paths = []
        for row in rows:
            item = self.catalog_table.item(row, 0)
            path = item.data(Qt.UserRole) if item else None
            if path:
                paths.append(str(path))
        return paths

    @guarded
    def enqueue_selected(self, *_args):
        paths = self.selected_paths()
        if paths:
            self.main_window.add_audio_paths(paths)

    @guarded
    def play_selected(self, *_args):
        paths = self.selected_paths()
        if not paths:
            return
        path = paths[0]
        index = next((i for i, queued in enumerate(self.main_window.tracks)
                      if same_audio_path(queued, path)), -1)
        if index < 0:
            index = len(self.main_window.tracks)
            self.main_window.add_audio_paths([path])
        if index >= 0:
            self.main_window.play_index(index, immediate=True)

    @guarded
    def preview_selected(self, *_args):
        paths = self.selected_paths()
        if paths:
            metadata = self.library.metadata_for_track(paths[0])
            self.main_window.preview_dialog.load_track(paths[0], metadata=metadata)

    @guarded
    def open_selected_folder(self, *_args):
        paths = self.selected_paths()
        if paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(paths[0]).parent)))

    @guarded
    def edit_selected_metadata(self, *_args):
        if self._metadata_operation_running or self._writing_tags:
            self.catalog_status.setText(translate("Aguarde a operação de metadados atual terminar."))
            return
        paths = self.selected_paths()
        if not paths:
            return
        dialog = MetadataEditorDialog(
            self.library, paths,
            getattr(self.main_window, "metadata_edit_default_target", "internal"), self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._start_metadata_save(paths, dialog.values())

    def _start_metadata_save(self, paths, changes):
        if self._metadata_operation_running or self._writing_tags:
            self.catalog_status.setText(translate("Aguarde a operação de metadados atual terminar."))
            return
        active_paths = self._active_media_path_keys()

        def save(current_paths):
            result = []
            for path in paths:
                try:
                    cover_data = changes["cover_data"]
                    if cover_data is None and changes["clear_cover"]:
                        cover_data = None
                    result.append((path, self.library.save_metadata(
                        path, changes["fields"], clear_fields=changes["clear_fields"],
                        custom_tags=changes["custom_tags"], cover_data=cover_data,
                        cover_mime=changes["cover_mime"], clear_cover=changes["clear_cover"],
                        target=changes["target"], active=audio_path_key(path) in current_paths)))
                except Exception as exc:
                    result.append((path, exc))
            return result

        self._metadata_operation_running = True
        worker = _LibraryWorker(save, active_paths)
        self._workers.add(worker)
        worker.signals.finished.connect(
            lambda result, current=worker: self._metadata_save_finished(result, current))
        self.catalog_status.setText(translate("Salvando metadados…"))
        self.pool.start(worker, priority=-1)

    @Slot(object)
    @guarded
    def _metadata_save_finished(self, result, worker=None):
        self._metadata_operation_running = False
        if worker is not None:
            self._workers.discard(worker)
        successes = 0
        pending = 0
        failures = []
        for path, outcome in result if isinstance(result, list) else []:
            if isinstance(outcome, Exception):
                failures.append(f"{Path(path).name}: {translate(str(outcome))}")
            else:
                successes += 1
                pending += int(bool(outcome.get("pending")))
        self._refresh_metadata_consumers()
        message = f"{translate('Faixas atualizadas')}: {successes}"
        if pending:
            message += f" • {translate('Aguardando o fim da reprodução para gravar tags.')}: {pending}"
        if failures:
            message += f" • {translate('Falhas por arquivo')}: " + " | ".join(failures[:5])
        self.catalog_status.setText(message)
        self.main_window.log_event("info" if not failures else "warning", message)
        if failures:
            show_warning(self, translate("Editor de metadados"), "\n".join(failures[:10]))
        self._has_pending_tag_writes = bool(self.library.pending_file_tag_writes())
        if self._has_pending_tag_writes:
            self._schedule_pending_write_check()

    @guarded
    def undo_selected_metadata(self, *_args):
        if self._metadata_operation_running or self._writing_tags:
            self.catalog_status.setText(translate("Aguarde a operação de metadados atual terminar."))
            return
        paths = self.selected_paths()
        if not paths:
            return
        active_paths = self._active_media_path_keys()

        def undo(payload):
            selected_paths, current_paths = payload
            result = []
            for path in selected_paths:
                try:
                    result.append((path, self.library.undo_metadata(
                        path, active=audio_path_key(path) in current_paths)))
                except Exception as exc:
                    result.append((path, exc))
            return result

        self._metadata_operation_running = True
        worker = _LibraryWorker(undo, (paths, active_paths))
        self._workers.add(worker)
        worker.signals.finished.connect(
            lambda result, current=worker: self._undo_metadata_finished(result, current))
        self.catalog_status.setText(translate("Desfazendo metadados…"))
        self.pool.start(worker, priority=-1)

    @Slot(object)
    @guarded
    def _undo_metadata_finished(self, result, worker=None):
        self._metadata_operation_running = False
        if worker is not None:
            self._workers.discard(worker)
        failures = [f"{Path(path).name}: {translate(str(outcome))}" for path, outcome in result
                    if isinstance(outcome, Exception)]
        undone = sum(bool(outcome) for _path, outcome in result
                     if not isinstance(outcome, Exception))
        self._refresh_metadata_consumers()
        if failures:
            show_warning(self, translate("Desfazer metadados"), "\n".join(failures[:8]))
        self.catalog_status.setText(translate("{count} alterações desfeitas").format(
            count=undone))
        self._has_pending_tag_writes = bool(self.library.pending_file_tag_writes())
        if self._has_pending_tag_writes:
            self._schedule_pending_write_check()

    def _active_media_path_keys(self):
        """Return files held by either audio deck, including an outgoing mix deck."""
        active = set()
        original_path = getattr(self.main_window, "original_media_path", None)
        for player in (getattr(self.main_window, "player", None),
                       getattr(self.main_window, "mix_player", None)):
            if player is None:
                continue
            try:
                if player.playbackState() == QMediaPlayer.StoppedState:
                    continue
                path = original_path(player) if callable(original_path) else ""
                if path:
                    active.add(audio_path_key(path))
            except Exception:
                continue
        try:
            index = int(self.main_window.current_index)
            if (0 <= index < len(self.main_window.tracks)
                    and self.main_window.player.playbackState() != QMediaPlayer.StoppedState):
                active.add(audio_path_key(self.main_window.tracks[index]))
        except (AttributeError, IndexError, TypeError, ValueError):
            pass
        return active

    def _schedule_pending_write_check(self, *_args):
        if self._has_pending_tag_writes and not self.pending_timer.isActive():
            self.pending_timer.start(250)

    def _refresh_metadata_consumers(self):
        self.refresh_catalog()
        self.refresh_organization()
        self.main_window.refresh_playlist(force=True)
        if 0 <= self.main_window.current_index < len(self.main_window.tracks):
            self.main_window._display_sync_metadata = None
            self.main_window._discord_metadata_cache.clear()
            self.main_window.refresh_playback_display()
            self.main_window.request_on_air_cover(
                self.main_window.tracks[self.main_window.current_index])
            self.main_window.sync_discord_presence()
        self.main_window.request_selected_cover()

    @guarded
    def edit_selected_tags(self, *_args):
        paths = self.selected_paths()
        if not paths:
            show_warning(self, translate("Etiquetas"), translate("Selecione pelo menos uma faixa."))
            return
        initial = "; ".join(self.library.tags_for_track(paths[0])) if len(paths) == 1 else ""
        text, accepted = QInputDialog.getText(
            self, translate("Etiquetas"), translate("Separe as etiquetas por ponto e vírgula:"),
            text=initial)
        if accepted:
            tags = [part.strip() for part in text.split(";") if part.strip()]
            for path in paths:
                self.library.set_track_tags(path, tags)
            self.refresh_organization()
            self.refresh_catalog()

    def refresh_libraries(self):
        libraries = self.library.libraries()
        self.library_table.blockSignals(True)
        try:
            self.library_table.setRowCount(len(libraries))
            for row, lib in enumerate(libraries):
                enabled = QTableWidgetItem()
                enabled.setFlags(enabled.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                enabled.setCheckState(Qt.Checked if lib.get("enabled") else Qt.Unchecked)
                enabled.setData(Qt.UserRole, lib["id"])
                self.library_table.setItem(row, 0, enabled)
                for col, value in enumerate((lib["name"], lib.get("track_count", 0), lib["root"]), 1):
                    self.library_table.setItem(row, col, QTableWidgetItem(str(value)))
        finally:
            self.library_table.blockSignals(False)
        self._refresh_filters()

    def _refresh_filters(self):
        def refill(combo, first_label, values):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(translate(first_label), None)
            for label, value in values:
                combo.addItem(str(label), value)
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)
        refill(self.library_filter, "Todas as pastas",
               [(lib["name"], str(lib["id"])) for lib in self.library.libraries() if lib.get("enabled")])
        refill(self.collection_filter, "Todas as coleções",
               [(item["name"], str(item["id"])) for item in self.library.collections()])
        refill(self.tag_filter, "Todas as etiquetas", [(tag, tag) for tag in self.library.unique_tags()])
        refill(self.group_filter, "Todos os grupos",
               [(group["name"], str(group["id"])) for group in self.library.rotation_groups()])

    @guarded
    def library_toggled(self, item):
        if item.column() == 0:
            self.library.update_library(item.data(Qt.UserRole), enabled=item.checkState() == Qt.Checked)
            self.refresh_catalog()
            self._refresh_filters()

    @guarded
    def add_library(self, *_args):
        folder = QFileDialog.getExistingDirectory(self, translate("Selecione uma pasta de músicas"))
        if not folder:
            return
        name, accepted = QInputDialog.getText(
            self, translate("Nome da pasta na Biblioteca"), translate("Nome:"),
            text=Path(folder).name)
        if not accepted:
            return
        try:
            library_id = self.library.add_library(name, folder)
        except Exception as exc:
            show_warning(self, translate("Biblioteca"), str(exc))
            return
        self.refresh_libraries()
        self._start_scan(library_id)

    def _selected_library_ids(self):
        return {str(item.data(Qt.UserRole)) for item in self.library_table.selectedItems()
                if item.column() == 0 and item.data(Qt.UserRole)}

    @guarded
    def scan_selected_libraries(self, *_args):
        selected = self._selected_library_ids()
        if not selected:
            selected = {str(row["id"]) for row in self.library.libraries()}
        for library_id in selected:
            self._start_scan(library_id)

    def _start_scan(self, library_id):
        if library_id in self._scanning:
            return
        worker = _LibraryWorker(lambda value: self.library.scan_library(value), library_id)
        worker.library_id = library_id
        self._workers.add(worker)
        self._scanning.add(library_id)
        worker.signals.finished.connect(
            lambda result, key=library_id, current=worker: self._scan_finished(key, result, current))
        self.folders_status.setText(translate("Indexando pasta da Biblioteca em segundo plano…"))
        self.pool.start(worker, priority=-1)

    @guarded
    def _scan_finished(self, library_id, result, worker=None):
        self._scanning.discard(library_id)
        if worker is not None:
            self._workers.discard(worker)
        if isinstance(result, Exception):
            self.main_window.log_event("error", translate(
                "Falha ao indexar pasta da Biblioteca: {result}").format(result=result))
            self.folders_status.setText(translate(str(result)))
        else:
            added, unchanged, errors = result
            self.folders_status.setText(translate(
                "Exame concluído: {added} • sem alterações: {unchanged} • erros: {errors}"
            ).format(added=added, unchanged=unchanged, errors=errors))
        self.refresh_libraries()
        self.refresh_catalog()
        self.main_window.autodj_last_signature = None
        self.main_window.fill_autodj_queue(bypass_throttle=True)

    @guarded
    def remove_selected_libraries(self, *_args):
        ids = self._selected_library_ids()
        if not ids:
            return
        if ask_question(self, translate("Remover pastas"),
                         translate("Remover pasta(s) e seus índices? O histórico será preservado.")) != QMessageBox.Yes:
            return
        for library_id in ids:
            self.library.remove_library(library_id)
        self.refresh_libraries()
        self.refresh_catalog()
        self.main_window.save_state()
        self.main_window.autodj_last_signature = None
        self.main_window.fill_autodj_queue(bypass_throttle=True)

    def refresh_organization(self):
        if not hasattr(self, "collection_table"):
            return
        collections = self.library.collections()
        self.collection_table.setRowCount(len(collections))
        for row, item in enumerate(collections):
            self.collection_table.setItem(row, 0, QTableWidgetItem(item["name"]))
            self.collection_table.setItem(row, 1, QTableWidgetItem(
                translate("Inteligente") if item["kind"] == "smart" else translate("Manual")))
            self.collection_table.setItem(row, 2, QTableWidgetItem(str(item["track_count"])))
            self.collection_table.item(row, 0).setData(Qt.UserRole, item["id"])
        groups = self.library.rotation_groups()
        self.group_table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            self.group_table.setItem(row, 0, QTableWidgetItem(group["name"]))
            self.group_table.setItem(row, 1, QTableWidgetItem(f"{group['weight']:g}"))
            self.group_table.setItem(row, 2, QTableWidgetItem(str(group["track_count"])))
            self.group_table.item(row, 0).setData(Qt.UserRole, group["id"])
        self._refresh_filters()

    @guarded
    def create_manual_collection(self, *_args):
        name, accepted = QInputDialog.getText(self, translate("Coleção manual"), translate("Nome da coleção"))
        if not accepted or not name.strip():
            return
        self.library.save_collection(name, kind="manual", paths=self.selected_paths())
        self.refresh_organization()
        self.refresh_catalog()

    @guarded
    def create_smart_collection(self, *_args):
        name, accepted = QInputDialog.getText(
            self, translate("Coleção inteligente"), translate("Nome da coleção"))
        if not accepted or not name.strip():
            return
        keyword = self.search.text().strip()
        criteria = {"include_keywords": keyword, "keyword_fields": [
            "title", "artist", "album", "albumartist", "genre", "composer", "year", "path"]}
        self.library.save_collection(name, kind="smart", criteria=criteria)
        self.refresh_organization()

    @guarded
    def add_selected_to_collection(self, *_args):
        paths = self.selected_paths()
        collections = [item for item in self.library.collections() if item["kind"] == "manual"]
        if not paths or not collections:
            return
        labels = [item["name"] for item in collections]
        choice, accepted = QInputDialog.getItem(
            self, translate("Adicionar à coleção"), translate("Coleção"), labels, 0, False)
        if accepted and choice in labels:
            collection = collections[labels.index(choice)]
            self.library.add_tracks_to_collection(collection["id"], paths)
            self.refresh_organization()
            self.refresh_catalog()

    @guarded
    def delete_selected_collection(self, *_args):
        row = self.collection_table.currentRow()
        item = self.collection_table.item(row, 0) if row >= 0 else None
        if item and ask_question(self, translate("Remover coleção"),
                                 translate("Remover a coleção selecionada?")) == QMessageBox.Yes:
            self.library.delete_collection(item.data(Qt.UserRole))
            self.refresh_organization()
            self.refresh_catalog()

    @guarded
    def create_rotation_group(self, *_args):
        name, accepted = QInputDialog.getText(
            self, translate("Grupo de rotação"), translate("Nome do grupo"))
        if not accepted or not name.strip():
            return
        weight, accepted = QInputDialog.getDouble(
            self, translate("Grupo de rotação"), translate("Peso"), 1.0, 0.01, 100.0, 2)
        if not accepted:
            return
        group_id = self.library.save_rotation_group(name, weight)
        for path in self.selected_paths():
            organization = self.library.track_organization(path)
            ids = list(dict.fromkeys(organization["groups"] + [group_id]))
            self.library.set_track_rotation_groups(path, ids)
        self.refresh_organization()
        self.refresh_catalog()

    @guarded
    def assign_selected_groups(self, *_args):
        paths = self.selected_paths()
        groups = self.library.rotation_groups()
        if not paths or not groups:
            return
        selected = self.library.track_organization(paths[0])["groups"] if len(paths) == 1 else ()
        dialog = _GroupPicker(groups, selected, self)
        if dialog.exec() == QDialog.Accepted:
            ids = dialog.value()
            for path in paths:
                self.library.set_track_rotation_groups(path, ids)
            self.refresh_organization()
            self.refresh_catalog()

    @guarded
    def delete_selected_group(self, *_args):
        row = self.group_table.currentRow()
        item = self.group_table.item(row, 0) if row >= 0 else None
        if item and ask_question(self, translate("Remover grupo"),
                                 translate("Remover o grupo e suas atribuições?")) == QMessageBox.Yes:
            self.library.delete_rotation_group(item.data(Qt.UserRole))
            self.refresh_organization()
            self.refresh_catalog()

    def _tab_changed(self, index):
        if index == 3:
            self.refresh_history()

    @guarded
    def refresh_history(self):
        rows = self.library.recent_history(500)
        self.history_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            from datetime import datetime
            values = (datetime.fromtimestamp(item["played_at"]).strftime("%Y-%m-%d %H:%M:%S"),
                      item.get("title", ""), item.get("artist", ""), item.get("album", ""),
                      item.get("genre", ""), item.get("year", ""), Path(item["path"]).name)
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value or ""))
                if column == 0:
                    cell.setData(Qt.UserRole, item["id"])
                if column == 6:
                    cell.setToolTip(item["path"])
                self.history_table.setItem(row, column, cell)
        self._history_last_id = max([self._history_last_id] + [int(item["id"]) for item in rows])

    @guarded
    def clear_history(self):
        if ask_question(self, translate("Limpar histórico"),
                         translate("Apagar todo o histórico persistente de reprodução?")) == QMessageBox.Yes:
            self.library.clear_history()
            self._history_last_id = 0
            self.refresh_history()

    @guarded
    def _process_pending_writes(self):
        if (self._writing_tags or self._metadata_operation_running
                or not hasattr(self, "main_window")):
            return
        active_paths = self._active_media_path_keys()
        pending = self.library.pending_file_tag_writes()
        if not pending:
            self._has_pending_tag_writes = False
            return
        ready = [row for row in pending
                 if audio_path_key(row["path"]) not in active_paths]
        if not ready:
            return
        self._writing_tags = True
        worker = _LibraryWorker(self._write_pending, ready)
        self._workers.add(worker)
        worker.signals.finished.connect(
            lambda result, current=worker: self._pending_finished(result, current))
        self.pool.start(worker, priority=-1)

    def _write_pending(self, rows):
        done = []
        for row in rows:
            try:
                self.library.complete_pending_file_tag_write(row["path"])
                done.append((row["path"], None))
            except Exception as exc:
                done.append((row["path"], exc))
        return done

    @Slot(object)
    @guarded
    def _pending_finished(self, result, worker=None):
        self._writing_tags = False
        if worker is not None:
            self._workers.discard(worker)
        failures = [f"{Path(path).name}: {translate(str(error))}" for path, error in result
                    if isinstance(error, Exception)]
        self._refresh_metadata_consumers()
        if failures:
            self.main_window.log_event("warning", translate("Falhas por arquivo") + ": " + " | ".join(failures))
        self._has_pending_tag_writes = bool(self.library.pending_file_tag_writes())

    def schedule_history_refresh(self):
        if self.tabs.currentIndex() == 3 and self.isVisible():
            self._history_refresh_timer.start(0)
