"""Application startup and single-instance handling."""

from __future__ import annotations

import logging
import sys
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import APP_DIR, APP_DISPLAY_NAME, INSTANCE_NAME
from .runtime_safety import configure_exception_logging, guarded
from .ui.main_window import RadioStudio


def main() -> int:
    configure_exception_logging(APP_DIR)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
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
