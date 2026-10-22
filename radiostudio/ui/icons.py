"""Shared branded SVG icons and top-level Qt window icon handling."""

from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from ..config import APP_ICON_FILE, ASSET_DIR
from ..i18n import translate


UI_ICON_DIR = ASSET_DIR / "ui_icons"


@lru_cache(maxsize=48)
def ui_icon(name: str) -> QIcon:
    """Render and cache a compact branded SVG icon."""
    path = UI_ICON_DIR / f"{name}.svg"
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return QIcon()
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, 64, 64))
    painter.end()
    return QIcon(pixmap)


@lru_cache(maxsize=8)
def explorer_icon(name: str) -> QIcon:
    """Render a compact Explorer toolbar SVG and cache its high-DPI pixmap."""
    path = UI_ICON_DIR / "explorer" / f"{name}.svg"
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return QIcon()
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, 64, 64))
    painter.end()
    return QIcon(pixmap)


def translated_icon_label(source: str, marker: str) -> str:
    """Translate a legacy emoji-prefixed label without displaying its emoji."""
    label = translate(source)
    if label.startswith(marker):
        return label[len(marker):].lstrip()
    return label


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
