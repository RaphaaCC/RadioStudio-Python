"""Dialog for creating or editing scheduled playback events."""


from datetime import datetime
from pathlib import Path
import uuid

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QTimeEdit)
from ..config import AUDIO_FILTER
from ..runtime_safety import guarded


class EventDialog(QDialog):
    def __init__(self, parent=None, event=None):
        super().__init__(parent)
        self.setWindowTitle("Evento agendado")
        self.path = event.get("path", "") if event else ""
        self.event_id = (event.get("id") if event and isinstance(event.get("id"), str)
                         else uuid.uuid4().hex)
        layout = QFormLayout(self)
        self.filename = QLabel(Path(self.path).name or "Nenhum arquivo")
        choose = QPushButton("Escolher arquivo...")
        choose.clicked.connect(self.choose_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self.filename, 1)
        file_row.addWidget(choose)
        layout.addRow("Áudio", file_row)
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        self.time.setTime(datetime.now().time().replace(second=0, microsecond=0))
        if event:
            saved_time = QTime.fromString(event.get("time", ""), "HH:mm")
            if saved_time.isValid():
                self.time.setTime(saved_time)
        layout.addRow("Horário", self.time)
        self.days = QComboBox()
        self.days.addItems(["Todos os dias", "Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"])
        if event:
            self.days.setCurrentIndex(event.get("day", 0))
        layout.addRow("Repetição", self.days)
        self.enabled = QCheckBox("Ativo")
        self.enabled.setChecked(event.get("enabled", True) if event else True)
        layout.addRow("", self.enabled)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @guarded
    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Escolher áudio", "", AUDIO_FILTER)
        if path:
            self.path = path
            self.filename.setText(Path(path).name)

    @guarded
    def accept(self):
        if not self.path:
            QMessageBox.warning(self, "Arquivo necessário", "Escolha um arquivo de áudio.")
            return
        super().accept()

    def value(self):
        return {"id": self.event_id, "path": self.path, "time": self.time.time().toString("HH:mm"),
                "day": self.days.currentIndex(), "enabled": self.enabled.isChecked()}
