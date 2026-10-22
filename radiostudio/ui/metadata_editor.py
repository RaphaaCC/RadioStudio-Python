"""Individual and batch metadata editing for indexed library tracks."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget,
)
from tinytag import TinyTag

from ..i18n import translate, translate_dialog_buttons
from ..runtime_safety import guarded
from ..services.embedded_metadata import read_embedded_custom_tags
from .icons import apply_app_icon
from .message_boxes import show_warning


_FIELDS = (
    ("title", "Título", False), ("artist", "Artista", True),
    ("album", "Álbum", False), ("albumartist", "Artista do álbum", True),
    ("genre", "Gênero", True), ("year", "Ano", False),
    ("track", "Faixa", False), ("composer", "Compositor", True),
    ("comments", "Comentários", False), ("lyrics", "Letras", False),
)


class MetadataEditorDialog(QDialog):
    """Edits selected fields only; clearing a field is a separate explicit choice."""

    def __init__(self, library, paths, default_target="internal", parent=None):
        super().__init__(parent)
        apply_app_icon(self)
        self.library = library
        self.paths = [str(path) for path in paths]
        self._cover_path = ""
        self.setWindowTitle(translate("Editor de metadados"))
        self.resize(690, 780)
        layout = QVBoxLayout(self)
        if len(self.paths) == 1:
            self.track = library.metadata_for_track(self.paths[0])
            self.setWindowTitle(translate("Editar metadados da faixa"))
            heading = QLabel(translate("{title} — {filename}").format(
                title=self.track.title, filename=Path(self.paths[0]).name))
        else:
            self.track = None
            heading = QLabel(translate("Faixas selecionadas") + ": " + str(len(self.paths)))
        heading.setWordWrap(True)
        layout.addWidget(heading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        music_box = QGroupBox(translate("Campos musicais"))
        music_form = QFormLayout(music_box)
        self.field_controls = {}
        self.clear_controls = {}
        current = self.track
        for name, label, multi in _FIELDS:
            row = QWidget()
            row_layout = QGridLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            apply = QCheckBox(translate("Aplicar campo"))
            initial = self._display_value(getattr(current, name, "")) if current else ""
            edit = (QPlainTextEdit(initial) if name in {"comments", "lyrics"}
                    else QLineEdit(initial))
            if isinstance(edit, QPlainTextEdit):
                edit.setMaximumHeight(78)
            if multi:
                edit.setToolTip(translate("Valores separados por ponto e vírgula"))
            clear = QCheckBox(translate("Limpar campo"))
            def sync_clear(checked, widget=edit, apply_box=apply):
                widget.setEnabled(not checked and apply_box.isChecked())
                if checked and not apply_box.isChecked():
                    apply_box.setChecked(True)

            def sync_apply(checked, widget=edit, clear_box=clear):
                if not checked and clear_box.isChecked():
                    clear_box.setChecked(False)
                widget.setEnabled(checked and not clear_box.isChecked())

            clear.toggled.connect(sync_clear)
            apply.toggled.connect(sync_apply)
            edit.setEnabled(False)
            row_layout.addWidget(apply, 0, 0)
            row_layout.addWidget(edit, 0, 1)
            row_layout.addWidget(clear, 0, 2)
            row_layout.setColumnStretch(1, 1)
            self.field_controls[name] = (apply, edit, multi)
            self.clear_controls[name] = clear
            music_form.addRow(translate(label), row)
        content_layout.addWidget(music_box)

        custom_box = QGroupBox(translate("Tags personalizadas (uma por linha: nome=valor)"))
        custom_layout = QVBoxLayout(custom_box)
        self.custom_tags_enabled = QCheckBox(translate("Aplicar tags personalizadas"))
        custom_layout.addWidget(self.custom_tags_enabled)
        self.custom_tags_edit = QPlainTextEdit()
        self.custom_tags_edit.setPlaceholderText(translate("TXXX=valor"))
        self.custom_tags_edit.setEnabled(False)
        self.custom_tags_edit.textChanged.connect(self._sync_custom_tags_enabled)
        self.custom_tags_enabled.toggled.connect(self.custom_tags_edit.setEnabled)
        self.custom_tags_edit.setToolTip(translate(
            "Para limpar uma tag personalizada, mantenha o nome e deixe o valor vazio (NOME=)."))
        override = library.read_metadata_override(self.paths[0]) if len(self.paths) == 1 else {}
        custom_tags = {}
        if len(self.paths) == 1:
            custom_tags.update(read_embedded_custom_tags(self.paths[0]))
        custom_tags.update(override.get("custom_tags", {}))
        if custom_tags:
            self.custom_tags_edit.setPlainText("\n".join(
                f"{name}={self._display_value(value)}" for name, value in custom_tags.items()))
        custom_layout.addWidget(self.custom_tags_edit)
        content_layout.addWidget(custom_box)

        cover_box = QGroupBox(translate("Capa incorporada"))
        cover_layout = QHBoxLayout(cover_box)
        self.cover_status = QLabel(translate("Nenhum arquivo de imagem selecionado"))
        self.cover_status.setWordWrap(True)
        self.choose_cover_button = QPushButton(translate("Escolher imagem"))
        self.choose_cover_button.clicked.connect(self.choose_cover)
        self.clear_cover = QCheckBox(translate("Remover capa"))
        self.clear_cover.toggled.connect(self._clear_cover_changed)
        cover_layout.addWidget(self.cover_status, 1)
        cover_layout.addWidget(self.choose_cover_button)
        cover_layout.addWidget(self.clear_cover)
        content_layout.addWidget(cover_box)

        target_box = QGroupBox(translate("Destino"))
        target_layout = QFormLayout(target_box)
        self.target = QComboBox()
        self.target.addItem(translate("Metadados internos do RadioStudio (SQLite)"), "internal")
        self.target.addItem(translate("Gravar tags no arquivo"), "file")
        index = self.target.findData(default_target if default_target in {"internal", "file"} else "internal")
        self.target.setCurrentIndex(max(0, index))
        self.target_note = QLabel(translate(
            "O arquivo em reprodução não será modificado até deixar de tocar."))
        self.target_note.setWordWrap(True)
        target_layout.addRow(translate("Destino"), self.target)
        target_layout.addRow(self.target_note)
        content_layout.addWidget(target_box)

        if len(self.paths) == 1:
            technical = QGroupBox(translate("Propriedades técnicas (somente leitura)"))
            technical_form = QFormLayout(technical)
            try:
                tags = TinyTag.get(self.paths[0], tags=False, duration=True)
                technical_form.addRow(translate("Formato"), QLabel(Path(self.paths[0]).suffix.upper().lstrip(".")))
                technical_form.addRow(translate("Duração"), QLabel(self._duration(tags.duration)))
                technical_form.addRow(translate("Taxa de amostragem"), QLabel(
                    self._technical(tags, "samplerate", "—")))
                technical_form.addRow(translate("Bitrate"), QLabel(
                    self._technical(tags, "bitrate", "—")))
                technical_form.addRow(translate("Tamanho"), QLabel(
                    self._size(Path(self.paths[0]).stat().st_size)))
            except Exception:
                technical_form.addRow(translate("Formato"), QLabel(Path(self.paths[0]).suffix.upper()))
            content_layout.addWidget(technical)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        translate_dialog_buttons(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _display_value(value):
        if isinstance(value, (list, tuple)):
            return "; ".join(str(item) for item in value)
        return str(value or "")

    @staticmethod
    def _technical(tags, name, default):
        value = getattr(tags, name, None)
        if value is None:
            return default
        if name == "samplerate":
            return f"{value} Hz"
        return f"{value} kbps"

    @staticmethod
    def _duration(value):
        try:
            seconds = max(0, int(float(value or 0)))
        except (TypeError, ValueError, OverflowError):
            seconds = 0
        return f"{seconds // 60:02}:{seconds % 60:02}"

    @staticmethod
    def _size(value):
        size = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    @guarded
    def choose_cover(self):
        extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
        image_filter = "{} ({})".format(
            translate("Arquivo de imagem"),
            " ".join(f"*{extension}" for extension in sorted(set(extensions))))
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, translate("Selecione um arquivo de capa"), "",
            image_filter)
        if path:
            try:
                if Path(path).stat().st_size > 8 * 1024 * 1024:
                    show_warning(self, translate("Editor de metadados"),
                                 translate("A capa excede o limite de 8 MB."))
                    return
            except OSError as exc:
                show_warning(self, translate("Editor de metadados"), str(exc))
                return
            self._cover_path = path
            self.cover_status.setText(Path(path).name)
            self.clear_cover.setChecked(False)

    def _clear_cover_changed(self, checked):
        self.choose_cover_button.setEnabled(not checked)
        if checked:
            self._cover_path = ""
            self.cover_status.setText(translate("Capa será removida ao salvar"))
        elif not self._cover_path:
            self.cover_status.setText(translate("Nenhum arquivo de imagem selecionado"))

    def _sync_custom_tags_enabled(self, *_args):
        if self.custom_tags_edit.toPlainText().strip():
            self.custom_tags_enabled.setChecked(True)

    def values(self):
        fields = {}
        clear_fields = []
        for name, (apply, edit, multi) in self.field_controls.items():
            if self.clear_controls[name].isChecked():
                clear_fields.append(name)
            elif apply.isChecked():
                value = (edit.toPlainText() if isinstance(edit, QPlainTextEdit)
                         else edit.text()).strip()
                if value:
                    fields[name] = ([part.strip() for part in value.split(";") if part.strip()]
                                    if multi else value)
        custom_tags = None
        if self.custom_tags_enabled.isChecked():
            custom_tags = {}
            for line in self.custom_tags_edit.toPlainText().splitlines():
                if "=" in line:
                    name, value = line.split("=", 1)
                    if name.strip():
                        custom_tags[name.strip()] = value.strip()
        cover_data = None
        cover_mime = ""
        if self._cover_path:
            cover_data = Path(self._cover_path).read_bytes()
            cover_mime = mimetypes.guess_type(self._cover_path)[0] or ""
        return {"fields": fields, "clear_fields": clear_fields,
                "custom_tags": custom_tags, "cover_data": cover_data,
                "cover_mime": cover_mime, "clear_cover": self.clear_cover.isChecked(),
                "target": self.target.currentData()}

    @guarded
    def accept(self):
        if not self.paths:
            show_warning(self, translate("Editor de metadados"),
                         translate("Selecione pelo menos uma faixa."))
            return
        try:
            values = self.values()
        except OSError as exc:
            show_warning(self, translate("Editor de metadados"), str(exc))
            return
        if (not values["fields"] and not values["clear_fields"]
                and values["custom_tags"] is None and values["cover_data"] is None
                and not values["clear_cover"]):
            show_warning(self, translate("Editor de metadados"),
                         translate("Marque os campos que deseja aplicar antes de salvar."))
            return
        super().accept()
