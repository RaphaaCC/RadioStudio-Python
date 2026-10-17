"""Application startup and single-instance handling."""

from __future__ import annotations

import logging
import sys
import ctypes
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import APP_DIR, APP_DISPLAY_NAME, APP_ICON_FILE, APP_NAME, INSTANCE_NAME
from .runtime_safety import configure_exception_logging, guarded
from .ui.main_window import RadioStudio


def set_windows_app_id() -> None:
    """Give the Windows taskbar an identity separate from the Python host."""
    if sys.platform != "win32":
        return
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        set_app_id = shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        result = set_app_id("RaemiLabs.RadioStudioPython")
        if result:
            logging.getLogger("radiostudio").warning(
                "Não foi possível definir a identidade do ícone na barra de tarefas (HRESULT 0x%08X)",
                result & 0xFFFFFFFF)
    except Exception:
        logging.getLogger("radiostudio").exception(
            "Falha ao definir a identidade do aplicativo na barra de tarefas")


def main() -> int:
    configure_exception_logging(APP_DIR)
    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setWindowIcon(QIcon(str(APP_ICON_FILE)))
    existing = QLocalSocket(app)
    existing.connectToServer(INSTANCE_NAME)
    if existing.waitForConnected(300):
        existing.disconnectFromServer()
        return 0
    existing.abort()
    instance_server = QLocalServer(app)
    if not instance_server.listen(INSTANCE_NAME):
        probe = QLocalSocket(app)
        probe.connectToServer(INSTANCE_NAME)
        if probe.waitForConnected(1500):
            probe.disconnectFromServer()
            return 0
        probe.abort()
        QLocalServer.removeServer(INSTANCE_NAME)
        if not instance_server.listen(INSTANCE_NAME):
            message = f"Não foi possível criar o controle de instância única: {instance_server.errorString()}"
            logging.getLogger("radiostudio").error(message)
            QMessageBox.critical(None, APP_DISPLAY_NAME, message)
            return 1
    try:
        window = RadioStudio()
    except Exception as exc:
        logging.getLogger("radiostudio").exception("Falha ao criar a janela principal")
        QMessageBox.critical(None, APP_DISPLAY_NAME,
                             f"O programa não conseguiu iniciar.\n\n{exc}\n\n"
                             f"Consulte o log em: {APP_DIR / 'app.log'}")
        return 1
    if instance_server.isListening():
        def reopen_existing_window():
            while instance_server.hasPendingConnections():
                connection = instance_server.nextPendingConnection()
                connection.disconnectFromServer()
                connection.deleteLater()
            window.restore_window()
        instance_server.newConnection.connect(guarded(reopen_existing_window))
    window.show()
    return app.exec()
