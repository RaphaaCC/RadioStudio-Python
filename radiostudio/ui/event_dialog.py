"""Dialog for creating and editing scheduled RadioStudio actions."""

from datetime import datetime
from pathlib import Path
import uuid

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTimeEdit, QWidget,
)

from ..config import AUDIO_FILTER
from ..i18n import translate, translate_dialog_buttons
from ..runtime_safety import guarded
from ..services.schedule_engine import normalize_event
from .icons import apply_app_icon
from .message_boxes import show_warning


class EventDialog(QDialog):
    def __init__(self, parent=None, event=None):
        super().__init__(parent)
        apply_app_icon(self)
        self.setWindowTitle(translate("Evento agendado"))
        self.setMinimumWidth(560)
        self._original_event = normalize_event(event) if event else None
        self.event_id = uuid.uuid4().hex
        layout = QFormLayout(self)

        self.action = QComboBox()
        for label, value in (
            ("Tocar faixa", "play_track"),
            ("Carregar playlist", "play_playlist"),
            ("Executar Cart Wall", "play_cart"),
            ("Comando de reprodução", "playback_command"),
            ("Comando de transmissão", "broadcast_command"),
        ):
            self.action.addItem(translate(label), value)
        layout.addRow(translate("Ação"), self.action)

        self.target_path = self._original_event["target"] if (
            self._original_event and self._original_event["action"] in {"play_track", "play_playlist"}
        ) else ""
        self.filename = QLabel(Path(self.target_path).name or translate("Nenhum arquivo"))
        self.filename.setWordWrap(True)
        self.choose_button = QPushButton(translate("Escolher arquivo..."))
        self.choose_button.clicked.connect(self.choose_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self.filename, 1)
        file_row.addWidget(self.choose_button)
        self.file_row = QWidget()
        self.file_row.setLayout(file_row)
        self.file_label = QLabel(translate("Arquivo"))
        layout.addRow(self.file_label, self.file_row)

        self.cart_slot = QComboBox()
        for index in range(25):
            self.cart_slot.addItem(translate(f"Cart {index + 1}"), index)
        layout.addRow(translate("Cart"), self.cart_slot)

        self.command = QComboBox()
        self.playback_commands = (
            ("Reproduzir", "play"), ("Pausar", "pause"), ("Retomar", "resume"),
            ("Parar", "stop"), ("Próxima faixa", "next"),
        )
        self.broadcast_commands = (
            ("Iniciar transmissão", "start"), ("Parar transmissão", "stop"),
        )
        for label, value in self.playback_commands:
            self.command.addItem(translate(label), value)
        self.command_label = QLabel(translate("Comando"))
        layout.addRow(self.command_label, self.command)

        self.recurrence = QComboBox()
        self.recurrence.addItem(translate("Uma vez"), "once")
        self.recurrence.addItem(translate("Semanal"), "weekly")
        self._set_combo_data(self.recurrence, "weekly")
        layout.addRow(translate("Repetição"), self.recurrence)

        self.event_date = QDateEdit()
        self.event_date.setCalendarPopup(True)
        self.event_date.setDisplayFormat("dd/MM/yyyy")
        self.event_date.setDate(QDate.currentDate())
        layout.addRow(translate("Data"), self.event_date)

        self.weekday_checks = []
        weekdays_row = QGridLayout()
        for index, label in enumerate(("Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira",
                                       "Sexta-feira", "Sábado", "Domingo")):
            checkbox = QCheckBox(translate(label))
            weekdays_row.addWidget(checkbox, index // 4, index % 4)
            self.weekday_checks.append(checkbox)
        weekdays_widget = QWidget()
        weekdays_widget.setLayout(weekdays_row)
        self.weekdays_widget = weekdays_widget
        self.weekdays_label = QLabel(translate("Dias da semana"))
        layout.addRow(self.weekdays_label, weekdays_widget)

        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        self.time.setTime(datetime.now().time().replace(second=0, microsecond=0))
        layout.addRow(translate("Horário"), self.time)

        self.playback_mode = QComboBox()
        self.playback_mode.addItem(translate("No horário agendado"), "at_time")
        self.playback_mode.addItem(translate("Após a faixa atual"), "after_current")
        self.playback_mode_label = QLabel(translate("Início da reprodução"))
        layout.addRow(self.playback_mode_label, self.playback_mode)

        self.playlist_mode = QComboBox()
        for label, value in (
            ("Inserir mantendo a fila", "insert"),
            ("Substituir a fila", "replace"),
            ("Carregar sem iniciar", "load_only"),
        ):
            self.playlist_mode.addItem(translate(label), value)
        self.playlist_mode_label = QLabel(translate("Comportamento da fila"))
        layout.addRow(self.playlist_mode_label, self.playlist_mode)

        self.enabled = QCheckBox(translate("Ativo"))
        self.enabled.setChecked(True)
        layout.addRow(translate(""), self.enabled)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        translate_dialog_buttons(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.action.currentIndexChanged.connect(self._update_action_fields)
        self.recurrence.currentIndexChanged.connect(self._update_recurrence_fields)
        if self._original_event:
            self._restore_event(self._original_event)
        else:
            for checkbox in self.weekday_checks:
                checkbox.setChecked(True)
        self._update_action_fields()
        self._update_recurrence_fields()

    def _set_row_visible(self, label, widget, visible):
        label.setVisible(visible)
        widget.setVisible(visible)

    def _set_combo_data(self, combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _restore_event(self, event):
        self.event_id = event["id"]
        self._set_combo_data(self.action, event["action"])
        if event["action"] in {"play_track", "play_playlist"}:
            self.target_path = event["target"]
            self.filename.setText(Path(self.target_path).name)
        elif event["action"] == "play_cart":
            self._set_combo_data(self.cart_slot, event["target"])
        else:
            commands = (self.playback_commands if event["action"] == "playback_command"
                        else self.broadcast_commands)
            self._replace_command_items(commands)
            self._set_combo_data(self.command, event["target"])
        self._set_combo_data(self.recurrence, event["recurrence"])
        if event["recurrence"] == "once":
            saved_date = QDate.fromString(event["date"], "yyyy-MM-dd")
            if saved_date.isValid():
                self.event_date.setDate(saved_date)
        else:
            for checkbox in self.weekday_checks:
                checkbox.setChecked(False)
            for day in event["weekdays"]:
                self.weekday_checks[day].setChecked(True)
        saved_time = QTime.fromString(event["time"], "HH:mm")
        if saved_time.isValid():
            self.time.setTime(saved_time)
        self._set_combo_data(self.playback_mode, event["playback_mode"])
        self._set_combo_data(self.playlist_mode, event["playlist_mode"])
        self.enabled.setChecked(event["enabled"])

    def _replace_command_items(self, commands):
        selected = self.command.currentData()
        self.command.blockSignals(True)
        self.command.clear()
        for label, value in commands:
            self.command.addItem(translate(label), value)
        self._set_combo_data(self.command, selected)
        self.command.blockSignals(False)

    def _update_action_fields(self, *_args):
        action = self.action.currentData()
        file_action = action in {"play_track", "play_playlist"}
        self._set_row_visible(self.file_label, self.file_row, file_action)
        self._set_row_visible(self.layout().labelForField(self.cart_slot), self.cart_slot,
                              action == "play_cart")
        command_action = action in {"playback_command", "broadcast_command"}
        self._set_row_visible(self.command_label, self.command, command_action)
        self._set_row_visible(self.playback_mode_label, self.playback_mode,
                              action in {"play_track", "play_playlist"})
        self._set_row_visible(self.playlist_mode_label, self.playlist_mode,
                              action == "play_playlist")
        if action == "playback_command":
            self._replace_command_items(self.playback_commands)
        elif action == "broadcast_command":
            self._replace_command_items(self.broadcast_commands)

    def _update_recurrence_fields(self, *_args):
        once = self.recurrence.currentData() == "once"
        self._set_row_visible(self.layout().labelForField(self.event_date), self.event_date, once)
        self._set_row_visible(self.weekdays_label, self.weekdays_widget, not once)

    @guarded
    def choose_file(self):
        if self.action.currentData() == "play_playlist":
            file_filter = translate("Playlist (*.m3u *.m3u8)")
            title = translate("Escolher playlist")
        else:
            file_filter = translate(AUDIO_FILTER)
            title = translate("Escolher áudio")
        path, _ = QFileDialog.getOpenFileName(self, title, "", file_filter)
        if path:
            self.target_path = path
            self.filename.setText(Path(path).name)

    @guarded
    def accept(self):
        action = self.action.currentData()
        if action in {"play_track", "play_playlist"} and not self.target_path:
            label = "Selecione um arquivo de áudio." if action == "play_track" else "Selecione uma playlist M3U."
            show_warning(self, translate("Arquivo necessário"), translate(label))
            return
        if self.recurrence.currentData() == "weekly" and not any(
                checkbox.isChecked() for checkbox in self.weekday_checks):
            show_warning(self, translate("Evento agendado"),
                         translate("Escolha pelo menos um dia da semana."))
            return
        selected_datetime = datetime.combine(
            self.event_date.date().toPython(), self.time.time().toPython())
        if (self.enabled.isChecked() and self.recurrence.currentData() == "once"
                and selected_datetime <= datetime.now()):
            show_warning(self, translate("Evento agendado"),
                         translate("A data de um evento ativo precisa ser futura."))
            return
        super().accept()

    def value(self):
        action = self.action.currentData()
        if action in {"play_track", "play_playlist"}:
            target = self.target_path
        elif action == "play_cart":
            target = self.cart_slot.currentData()
        else:
            target = self.command.currentData()
        recurrence = self.recurrence.currentData()
        return {
            "id": self.event_id,
            "enabled": self.enabled.isChecked(),
            "recurrence": recurrence,
            "time": self.time.time().toString("HH:mm"),
            "date": (self.event_date.date().toString("yyyy-MM-dd")
                     if recurrence == "once" else ""),
            "weekdays": ([index for index, checkbox in enumerate(self.weekday_checks)
                          if checkbox.isChecked()] if recurrence == "weekly" else []),
            "action": action,
            "target": target,
            "playback_mode": self.playback_mode.currentData(),
            "playlist_mode": self.playlist_mode.currentData(),
            "last_occurrence": self._original_event.get("last_occurrence", "")
            if self._original_event else "",
            "last_result": self._original_event.get("last_result", "")
            if self._original_event else "",
        }
