"""Compact control panel for the microphone and five master-bus inputs."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QGridLayout, QLabel, QToolButton, QVBoxLayout,
                               QWidget)

from ..services.audio_inputs import INPUT_CHANNELS, INPUT_LABELS


class AudioInputsPanel(QWidget):
    input_toggled = Signal(str, bool)

    def __init__(self, open_settings, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        intro = QLabel("Ligue uma entrada para enviá-la ao master e às saídas ativas.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.buttons = {}
        self.status_labels = {}
        for index, channel in enumerate(INPUT_CHANNELS):
            button = QToolButton()
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setMinimumSize(112, 96)
            button.setText("🎙 Microfone" if channel == "microphone" else f"🔊 Auxiliar {index}")
            button.setToolTip(f"Ativar ou desativar {INPUT_LABELS[channel]} no master")
            if channel == "microphone":
                icon_path = Path(__file__).resolve().parents[1] / "assets" / "microphone.png"
                if icon_path.is_file():
                    button.setIcon(QIcon(str(icon_path)))
                    button.setIconSize(QSize(52, 52))
            button.toggled.connect(lambda checked, key=channel: self.input_toggled.emit(key, checked))
            status = QLabel("Desativado")
            status.setWordWrap(True)
            status.setMinimumHeight(32)
            self.buttons[channel] = button
            self.status_labels[channel] = status
            if channel == "microphone":
                grid.addWidget(button, 0, 0, 1, 2)
                grid.addWidget(status, 1, 0, 1, 2)
            else:
                row, column = 2 + (index - 1) // 2 * 2, (index - 1) % 2
                grid.addWidget(button, row, column)
                grid.addWidget(status, row + 1, column)
        layout.addLayout(grid)
        note = QLabel(
            "O microfone reduz o nível da música conforme as configurações. "
            "Auxiliares não alteram o volume da música. A escuta local segue a opção de áudio master.")
        note.setWordWrap(True)
        layout.addWidget(note)
        settings = QToolButton()
        settings.setText("⚙ Configurar dispositivos e níveis")
        settings.clicked.connect(open_settings)
        layout.addWidget(settings)
        layout.addStretch(1)

    def set_enabled(self, channel: str, enabled: bool):
        button = self.buttons.get(channel)
        if button is not None and button.isChecked() != enabled:
            button.blockSignals(True)
            button.setChecked(enabled)
            button.blockSignals(False)

    def set_status(self, channel: str, status: str):
        label = self.status_labels.get(channel)
        if label is not None:
            label.setText(status)
        if channel in self.buttons and not status.startswith(("Ativo", "Desativado")):
            self.set_enabled(channel, False)
