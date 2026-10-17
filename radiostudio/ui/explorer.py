"""Explorer operations for the main window."""

from ._shared import *
from ..runtime_safety import guarded


class ExplorerMixin:
    @guarded
    def open_explorer_folder(self, path, persist=False):
        folder = Path(path).expanduser()
        if not folder.is_dir():
            QMessageBox.warning(self, "Pasta", f"Pasta não encontrada: {path}")
            return
        folder = folder.resolve()
        self.explorer_folder = str(folder)
        self.file_model.setRootPath(str(folder))
        self.explorer.setRootIndex(self.file_model.index(str(folder)))
        self.explorer_path.setText(str(folder))
        if persist:
            self.save_state()

    @guarded
    def choose_explorer_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Escolher pasta de músicas", self.explorer_folder or str(Path.home()))
        if folder:
            self.open_explorer_folder(folder, persist=True)

    @guarded
    def open_music_folder(self):
        folder = QStandardPaths.writableLocation(QStandardPaths.MusicLocation)
        self.open_explorer_folder(
            folder if folder and Path(folder).is_dir() else str(Path.home()), persist=True)

    @guarded
    def toggle_explorer_search(self):
        visible = not self.explorer_search.isVisible()
        self.explorer_search.setVisible(visible)
        if visible:
            self.explorer_search.setFocus()
        else:
            self.explorer_search.clear()

    @guarded
    def toggle_explorer_path(self):
        visible = not self.explorer_path.isVisible()
        self.explorer_path.setVisible(visible)
        if visible:
            self.explorer_path.selectAll()
            self.explorer_path.setFocus()

    @guarded
    def filter_explorer(self, text):
        term = text.strip()
        self.file_model.setNameFilters(
            [f"*{term}*{extension}" for extension in sorted(AUDIO_EXTENSIONS)])

    @guarded
    def show_drives(self):
        self.file_model.setRootPath("")
        self.explorer.setRootIndex(QModelIndex())
        for row in range(self.file_model.rowCount(QModelIndex())):
            self.explorer.expand(self.file_model.index(row, 0, QModelIndex()))
        self.explorer_folder = ""
        self.explorer_path.setText("")
        self.save_state()

    @guarded
    def explorer_up(self):
        current = self.explorer_path.text()
        if current:
            parent = Path(current).parent
            if parent == Path(current):
                self.show_drives()
            else:
                self.open_explorer_folder(str(parent), persist=True)

    @guarded
    def explorer_double_clicked(self, index):
        path = self.file_model.filePath(index)
        if self.file_model.isDir(index):
            self.explorer.setExpanded(index, not self.explorer.isExpanded(index))
        else:
            self.add_audio_paths([path])

    @guarded
    def add_explorer_selection(self):
        rows = self.explorer.selectionModel().selectedRows(0)
        self.add_audio_paths([self.file_model.filePath(index) for index in rows])
