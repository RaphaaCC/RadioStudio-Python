"""Application startup and single-instance handling."""

from __future__ import annotations

import logging
import sys
import ctypes
import time
import builtins
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QInputDialog

from .config import (APP_DIR, APP_ICON_FILE, APP_NAME, INSTALL_LANGUAGE_FILE,
                     INSTANCE_NAME, LANGUAGE_FILE, STATE_FILE)
from .i18n import (LANGUAGE_CHOICES, install_translations, read_language_preference,
                   save_language_preference, translate)
from .runtime_safety import configure_exception_logging, guarded
from .ui.main_window import RadioStudio
from .ui.icons import apply_app_icon
from .ui.message_boxes import show_critical


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
    logging.getLogger("radiostudio").info("Arquivo de configurações: %s", STATE_FILE)
    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("radiostudio")
    # On Windows, Qt appends applicationDisplayName to every native dialog
    # caption. The main window already sets its full name and version itself.
    app.setApplicationDisplayName("")
    app.setWindowIcon(QIcon(str(APP_ICON_FILE)))
    language_preference, language_was_configured = read_language_preference(
        STATE_FILE, LANGUAGE_FILE, INSTALL_LANGUAGE_FILE)
    install_translations(app, language_preference)
    restart_wait_flag = "--radiostudio-restart-wait"
    if restart_wait_flag in sys.argv[1:]:
        # A restarted copy waits for the previous process to release the
        # single-instance socket before trying to become the new instance.
        sys.argv.remove(restart_wait_flag)
        deadline = time.monotonic() + 30
        consecutive_disconnected = 0
        while time.monotonic() < deadline and consecutive_disconnected < 3:
            probe = QLocalSocket()
            probe.connectToServer(INSTANCE_NAME)
            if probe.waitForConnected(150):
                probe.disconnectFromServer()
                consecutive_disconnected = 0
            else:
                probe.abort()
                consecutive_disconnected += 1
            if consecutive_disconnected < 3:
                time.sleep(0.2)
        if consecutive_disconnected < 3:
            logging.getLogger("radiostudio").error(
                "O RadioStudio anterior não liberou o controle de instância a tempo")
            return 1
    existing = QLocalSocket(app)
    existing.connectToServer(INSTANCE_NAME)
    if existing.waitForConnected(300):
        existing.disconnectFromServer()
        return 0
    existing.abort()
    instance_server = QLocalServer(app)
    instance_server.setSocketOptions(QLocalServer.UserAccessOption)
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
            show_critical(None, translate("Erro ao iniciar"), message)
            return 1
    if getattr(builtins, "_RADIOSTUDIO_PORTABLE", False) and not language_was_configured:
        labels = [translate(label) for label, _ in LANGUAGE_CHOICES]
        selected, accepted = QInputDialog.getItem(
            None, translate("Escolha o idioma do RadioStudio"),
            translate("Selecione o idioma. Você poderá alterá-lo depois nas Configurações."),
            labels, 0, False)
        language_preference = next(
            (value for label, value in LANGUAGE_CHOICES if translate(label) == selected),
            "system"
        ) if accepted else "system"
        try:
            save_language_preference(LANGUAGE_FILE, language_preference)
        except OSError:
            logging.getLogger("radiostudio").exception(
                "Não foi possível salvar a preferência de idioma")
        install_translations(app, language_preference)
    try:
        window = RadioStudio()
    except Exception as exc:
        logging.getLogger("radiostudio").exception("Falha ao criar a janela principal")
        show_critical(None, translate("Erro ao iniciar"),
                       translate(f"O programa não conseguiu iniciar.\n\n{exc}\n\n"
                       f"Consulte o log em: {APP_DIR / 'app.log'}"))
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
    apply_app_icon(window)
    return app.exec()
