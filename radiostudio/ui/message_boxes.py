"""Message boxes whose standard buttons follow the selected UI language."""

from PySide6.QtWidgets import QMessageBox

from ..i18n import translate_dialog_buttons


def create_message_box(parent, icon, title, text, buttons, default_button=None, locale=None):
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(buttons)
    if default_button is not None:
        box.setDefaultButton(default_button)
    translate_dialog_buttons(box, locale)
    return box


def show_warning(parent, title, text, *, locale=None):
    box = create_message_box(
        parent, QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.Ok,
        locale=locale)
    return QMessageBox.StandardButton(box.exec())


def show_information(parent, title, text, *, locale=None):
    box = create_message_box(
        parent, QMessageBox.Icon.Information, title, text, QMessageBox.StandardButton.Ok,
        locale=locale)
    return QMessageBox.StandardButton(box.exec())


def show_critical(parent, title, text, *, locale=None):
    box = create_message_box(
        parent, QMessageBox.Icon.Critical, title, text, QMessageBox.StandardButton.Ok,
        locale=locale)
    return QMessageBox.StandardButton(box.exec())


def ask_question(parent, title, text,
                 buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                 default_button=QMessageBox.StandardButton.NoButton, *, locale=None):
    box = create_message_box(
        parent, QMessageBox.Icon.Question, title, text, buttons,
        default_button if default_button != QMessageBox.StandardButton.NoButton else None,
        locale=locale)
    return QMessageBox.StandardButton(box.exec())
