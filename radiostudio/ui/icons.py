"""Shared application icon handling for top-level Qt windows."""

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..config import APP_ICON_FILE


def app_icon() -> QIcon:
    application = QApplication.instance()
    if application is not None:
        icon = application.windowIcon()
        if not icon.isNull():
            return icon
    return QIcon(str(APP_ICON_FILE))


def apply_app_icon(window) -> None:
    icon = app_icon()
    if not icon.isNull():
        window.setWindowIcon(icon)
        # QWidget can be assigned its icon before the native window exists.
        # Reapply it to QWindow when available so Windows also receives the
        # icon for the taskbar after the HWND has been created.
        handle = window.windowHandle()
        if handle is not None:
            handle.setIcon(icon)
