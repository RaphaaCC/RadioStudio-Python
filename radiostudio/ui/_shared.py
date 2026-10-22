"""Qt and application symbols shared by UI controller mixins."""

import json
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QDir, QItemSelectionModel, QModelIndex, QStandardPaths,
                            Qt, QTimer, QUrl, Slot)
from PySide6.QtGui import QAction, QColor, QFont, QPalette
from PySide6.QtMultimedia import (QAudioBuffer, QAudioBufferOutput, QAudioFormat,
                                  QAudioOutput, QMediaPlayer)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFileSystemModel, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSlider, QSplitter,
    QSystemTrayIcon, QTabWidget, QTableWidget, QTableWidgetItem, QTimeEdit,
    QToolButton, QTreeView, QVBoxLayout, QWidget,
)

from ..config import (APP_DIR, APP_DISPLAY_NAME, AUDIO_EXTENSIONS, AUDIO_FILTER, MAX_LOG_ROWS,
                      STATE_FILE, clock_text)
from ..services.broadcast import CHANNELS, SAMPLE_RATE
from ..services.broadcast_quality import BroadcastManager
from ..services.track_metadata import (DEFAULT_STATION_METADATA, format_broadcast_title,
                                       read_track_metadata)
from ..i18n import translate, translate_dialog_buttons
from .message_boxes import ask_question, show_critical, show_information, show_warning
from .audio_inputs_panel import AudioInputsPanel
from .broadcast_panel import BroadcastPanel
from .settings_dialog import SettingsDialog, resolve_output_device
from .studio_widgets import OnAirPanel, StudioClock
