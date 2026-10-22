"""Manual and scheduled recording controls for the final program mix."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDate, QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget, QFileDialog,
)

from ..i18n import translate, translate_dialog_buttons
from ..services.recording import sanitize_recording_schedules, validate_recording_schedule
from .message_boxes import show_warning
from .icons import apply_app_icon


def default_recording_folder() -> Path:
    """Return the user's Music folder with the app's recording subfolder."""
    from PySide6.QtCore import QStandardPaths

    music = QStandardPaths.writableLocation(QStandardPaths.MusicLocation)
    return (Path(music) if music else Path.home() / "Music") / "RadioStudio Recordings"


class RecordingScheduleDialog(QDialog):
    DAY_CHOICES = (("Seg", 0), ("Ter", 1), ("Qua", 2), ("Qui", 3),
                   ("Sex", 4), ("Sáb", 5), ("Dom", 6))

    def __init__(self, schedule: dict | None = None, parent=None):
        super().__init__(parent)
        apply_app_icon(self)
        self.setWindowTitle(translate("Agenda de gravação"))
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItem(translate("Única"), "once")
        self.kind.addItem(translate("Recorrente"), "weekly")
        form.addRow(translate("Tipo"), self.kind)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        form.addRow(translate("Data"), self.date)
        days_row = QWidget()
        days_layout = QHBoxLayout(days_row)
        days_layout.setContentsMargins(0, 0, 0, 0)
        self.days = {}
        for label, day in self.DAY_CHOICES:
            check = QCheckBox(translate(label))
            self.days[day] = check
            days_layout.addWidget(check)
        form.addRow(translate("Dias da semana"), days_row)
        self.start = QTimeEdit(QTime(9, 0))
        self.start.setDisplayFormat("HH:mm")
        self.end = QTimeEdit(QTime(10, 0))
        self.end.setDisplayFormat("HH:mm")
        form.addRow(translate("Início"), self.start)
        form.addRow(translate("Término"), self.end)
        self.enabled = QCheckBox(translate("Ativa"))
        self.enabled.setChecked(True)
        form.addRow("", self.enabled)
        layout.addLayout(form)
        self.note = QLabel(translate(
            "Se o término for anterior ao início, a gravação termina no dia seguinte."))
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.kind.currentIndexChanged.connect(self._update_fields)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        translate_dialog_buttons(buttons)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if schedule:
            self.kind.setCurrentIndex(max(0, self.kind.findData(schedule.get("type"))))
            if schedule.get("date"):
                saved_date = QDate.fromString(str(schedule["date"]), "yyyy-MM-dd")
                if saved_date.isValid():
                    self.date.setDate(saved_date)
            for day in schedule.get("weekdays", []):
                if day in self.days:
                    self.days[day].setChecked(True)
            self.start.setTime(QTime.fromString(schedule.get("start", "09:00"), "HH:mm"))
            self.end.setTime(QTime.fromString(schedule.get("end", "10:00"), "HH:mm"))
            self.enabled.setChecked(schedule.get("enabled", True) is True)
        self._update_fields()

    def _update_fields(self, *_args):
        once = self.kind.currentData() == "once"
        self.date.setEnabled(once)
        for check in self.days.values():
            check.setEnabled(not once)

    def _accept_if_valid(self):
        try:
            validate_recording_schedule(self.value())
        except ValueError as exc:
            show_warning(self, translate("Agenda de gravação"), translate(str(exc)))
            return
        self.accept()

    def value(self) -> dict:
        kind = self.kind.currentData()
        return {"type": kind,
                "date": self.date.date().toString("yyyy-MM-dd") if kind == "once" else "",
                "weekdays": [day for day, check in self.days.items() if check.isChecked()],
                "start": self.start.time().toString("HH:mm"),
                "end": self.end.time().toString("HH:mm"),
                "enabled": self.enabled.isChecked()}


class RecordingPanel(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self._last_error = ""
        root = QVBoxLayout(self)

        controls = QGroupBox(translate("Gravação do Master Ao Vivo"))
        controls_layout = QVBoxLayout(controls)
        self.status = QLabel()
        self.duration = QLabel()
        self.current_file = QLabel()
        self.current_file.setWordWrap(True)
        controls_layout.addWidget(self.status)
        controls_layout.addWidget(self.duration)
        controls_layout.addWidget(self.current_file)
        button_row = QHBoxLayout()
        self.start_button = QPushButton(translate("Iniciar"))
        self.pause_button = QPushButton(translate("Pausar"))
        self.stop_button = QPushButton(translate("Parar gravação"))
        self.start_button.clicked.connect(self.start_manual)
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button.clicked.connect(self.stop_manual)
        for button in (self.start_button, self.pause_button, self.stop_button):
            button_row.addWidget(button)
        button_row.addStretch(1)
        controls_layout.addLayout(button_row)
        root.addWidget(controls)

        options = QGroupBox(translate("Arquivo e início automático"))
        options_form = QFormLayout(options)
        self.folder = QLineEdit()
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        self.browse_button = QPushButton(translate("Escolher pasta…"))
        self.browse_button.clicked.connect(self.choose_folder)
        folder_row.addWidget(self.browse_button)
        options_form.addRow(translate("Pasta"), folder_row)
        self.format = QComboBox()
        self.format.addItem(translate("MP3"), "mp3")
        self.format.addItem(translate("WAV"), "wav")
        options_form.addRow(translate("Formato"), self.format)
        self.bitrate = QComboBox()
        for rate in (128, 160, 192, 256, 320):
            self.bitrate.addItem(translate("{rate} kbps").format(rate=rate), rate)
        options_form.addRow(translate("Taxa de bits do MP3"), self.bitrate)
        self.auto_broadcast = QCheckBox(translate(
            "Iniciar gravação automaticamente ao iniciar a transmissão"))
        options_form.addRow("", self.auto_broadcast)
        self.format.currentIndexChanged.connect(self._update_format_controls)
        self.folder.textChanged.connect(self.save_config)
        self.format.currentIndexChanged.connect(self.save_config)
        self.bitrate.currentIndexChanged.connect(self.save_config)
        self.auto_broadcast.toggled.connect(self.save_config)
        root.addWidget(options)

        schedules = QGroupBox(translate("Agendas de gravação"))
        schedule_layout = QVBoxLayout(schedules)
        schedule_buttons = QHBoxLayout()
        for label, handler in (("Adicionar", self.add_schedule),
                               ("Editar", self.edit_schedule),
                               ("Excluir", self.delete_schedule)):
            button = QPushButton(translate(label))
            button.clicked.connect(handler)
            schedule_buttons.addWidget(button)
        schedule_buttons.addStretch(1)
        schedule_layout.addLayout(schedule_buttons)
        self.schedule_table = QTableWidget(0, 5)
        self.schedule_table.setHorizontalHeaderLabels([
            translate("Ativa"), translate("Tipo"), translate("Data / dias"),
            translate("Início"), translate("Término")])
        self.schedule_table.horizontalHeader().setStretchLastSection(True)
        self.schedule_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.schedule_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        schedule_layout.addWidget(self.schedule_table)
        root.addWidget(schedules, 1)
        self.schedule_note = QLabel(translate(
            "As agendas funcionam enquanto o RadioStudio estiver aberto. Cada agenda só encerra a gravação que ela iniciou."))
        self.schedule_note.setWordWrap(True)
        root.addWidget(self.schedule_note)

        self.config_save_timer = QTimer(self)
        self.config_save_timer.setSingleShot(True)
        self.config_save_timer.setInterval(350)
        self.config_save_timer.timeout.connect(self.main_window.save_state)
        self.apply_settings()
        self.refresh_schedules()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(500)
        self.refresh_status()

    def apply_settings(self):
        settings = self.main_window.recording_config
        self.folder.setText(str(settings.get("folder") or default_recording_folder()))
        self.format.setCurrentIndex(max(0, self.format.findData(settings.get("format", "mp3"))))
        rate = int(settings.get("bitrate", 192))
        self.bitrate.setCurrentIndex(max(0, self.bitrate.findData(rate)))
        self.auto_broadcast.setChecked(settings.get("auto_on_broadcast") is True)
        self._update_format_controls()

    def save_config(self, *_args):
        self.main_window.recording_config = {
            "folder": self.folder.text().strip() or str(default_recording_folder()),
            "format": self.format.currentData() or "mp3",
            "bitrate": int(self.bitrate.currentData() or 192),
            "auto_on_broadcast": self.auto_broadcast.isChecked(),
        }
        self.config_save_timer.start()

    def _update_format_controls(self, *_args):
        self.bitrate.setEnabled(self.format.currentData() == "mp3")

    def choose_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self, translate("Pasta de gravação"), self.folder.text())
        if selected:
            self.folder.setText(selected)

    def start_manual(self):
        self.main_window.start_recording_session("manual")
        self.refresh_status()

    def toggle_pause(self):
        session = self.main_window.broadcast.recording
        if not session:
            return
        if session.state == "paused":
            self.main_window.broadcast.resume_recording()
        elif session.state == "recording":
            self.main_window.broadcast.pause_recording()
        self.refresh_status()

    def stop_manual(self):
        self.main_window.stop_recording_session_if_id(None, reason="manual")
        self.refresh_status()

    def add_schedule(self):
        dialog = RecordingScheduleDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.main_window.recording_schedules.append(validate_recording_schedule(dialog.value()))
            self.refresh_schedules()
            self.main_window.save_state()

    def edit_schedule(self):
        row = self.schedule_table.currentRow()
        if not 0 <= row < len(self.main_window.recording_schedules):
            return
        dialog = RecordingScheduleDialog(self.main_window.recording_schedules[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = validate_recording_schedule(dialog.value())
            updated["id"] = self.main_window.recording_schedules[row]["id"]
            self.main_window.recording_schedules[row] = updated
            # An active run keeps its original end time when its recurrence is edited.
            self.refresh_schedules()
            self.main_window.save_state()

    def delete_schedule(self):
        row = self.schedule_table.currentRow()
        if 0 <= row < len(self.main_window.recording_schedules):
            schedule = self.main_window.recording_schedules.pop(row)
            # Keep the deadline for a run already started by this schedule.
            self.refresh_schedules()
            self.main_window.save_state()

    def refresh_schedules(self):
        schedules = self.main_window.recording_schedules
        self.schedule_table.setRowCount(len(schedules))
        day_labels = [translate(label) for label, _day in RecordingScheduleDialog.DAY_CHOICES]
        for row, schedule in enumerate(schedules):
            if schedule["type"] == "once":
                when = schedule.get("date", "")
                kind = translate("Única")
            else:
                when = ", ".join(day_labels[day] for day in schedule.get("weekdays", []))
                kind = translate("Recorrente")
            values = [translate("Sim" if schedule.get("enabled") else "Não"), kind, when,
                      schedule.get("start", ""), schedule.get("end", "")]
            for column, value in enumerate(values):
                self.schedule_table.setItem(row, column, QTableWidgetItem(value))

    def refresh_status(self):
        session = self.main_window.broadcast.recording
        state = session.state if session else "idle"
        error_text = translate(session.error) if session else ""
        states = {
            "idle": translate("Pronta para gravar"),
            "starting": translate("Preparando gravação…"),
            "recording": translate("Gravando"),
            "paused": translate("Gravação pausada"),
            "stopping": translate("Finalizando arquivo…"),
            "stopped": translate("Gravação finalizada"),
            "error": translate("Erro na gravação: {error}").format(
                error=error_text),
        }
        self.status.setText(translate("Estado: {state}").format(state=states.get(state, state)))
        self.duration.setText(translate("Duração: {duration}").format(
            duration=clock_duration(session.elapsed_seconds if session else 0)))
        self.current_file.setText(translate("Arquivo: {path}").format(
            path=str(session.path) if session else translate("Nenhum arquivo")))
        self.start_button.setEnabled(not (session and session.busy))
        self.pause_button.setEnabled(bool(session and state in {"recording", "paused"}))
        self.pause_button.setText(translate("Retomar" if state == "paused" else "Pausar"))
        self.stop_button.setEnabled(bool(session and state in {"recording", "paused"}))
        if session and state == "error" and session.error != self._last_error:
            self._last_error = session.error
            error = translate(session.error)
            self.main_window.log_event("error", f"Falha na gravação: {error}")
            self.main_window.statusBar().showMessage(translate("Falha na gravação: {error}").format(
                error=error))


def clock_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02}:{total % 3600 // 60:02}:{total % 60:02}"
