"""Explorer operations for the main window."""

import os
import time

from ._shared import *
from ..runtime_safety import guarded

EXPLORER_REFRESH_DEBOUNCE_MS = 750
EXPLORER_REFRESH_MIN_INTERVAL_MS = 2500


class ExplorerMixin:
    @staticmethod
    def _explorer_path_key(path):
        return os.path.normcase(os.path.abspath(str(path)))

    @guarded
    def open_explorer_folder(self, path, persist=False):
        folder = Path(path).expanduser()
        if not folder.is_dir():
            QMessageBox.warning(self, "Pasta", f"Pasta não encontrada: {path}")
            return
        folder = folder.resolve()
        signals_blocked = self.explorer.blockSignals(True)
        try:
            self.explorer.collapseAll()
        finally:
            self.explorer.blockSignals(signals_blocked)
        self.explorer_folder = str(folder)
        self._explorer_expanded_paths.clear()
        self._explorer_mode_switching = False
        self.explorer.setUpdatesEnabled(True)
        self._explorer_restore_state = None
        self._explorer_restore_timer.stop()
        self._explorer_refresh_timer.stop()
        self._explorer_last_refresh_at = 0.0
        watched = False
        old_directories = self._explorer_watcher.directories()
        if old_directories:
            self._explorer_watcher.removePaths(old_directories)
        try:
            watched = self._explorer_watcher.addPath(str(folder))
        except (OSError, RuntimeError):
            watched = False
        # Fall back to Qt's normal watcher on filesystems that do not support
        # QFileSystemWatcher, so the explorer still updates automatically.
        current_mode = self.file_model.testOption(QFileSystemModel.Option.DontWatchForChanges)
        if current_mode != watched:
            self._replace_explorer_file_model(dont_watch=watched, preserve_view=False)
        else:
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
        self._pending_explorer_filter = text.strip()
        self.explorer_filter_timer.start()

    @guarded
    def apply_explorer_filter(self):
        term = getattr(self, "_pending_explorer_filter", "")
        self.file_model.setNameFilters(
            [f"*{term}*{extension}" for extension in sorted(AUDIO_EXTENSIONS)])

    @guarded
    def show_drives(self):
        self._explorer_refresh_timer.stop()
        self._explorer_restore_timer.stop()
        self.explorer.setUpdatesEnabled(True)
        self._explorer_restore_state = None
        self._explorer_mode_switching = False
        self._explorer_expanded_paths.clear()
        directories = self._explorer_watcher.directories()
        if directories:
            self._explorer_watcher.removePaths(directories)
        self.file_model.setOption(QFileSystemModel.Option.DontWatchForChanges, False)
        self.file_model.setRootPath("")
        self.explorer.setRootIndex(QModelIndex())
        for row in range(self.file_model.rowCount(QModelIndex())):
            self.explorer.expand(self.file_model.index(row, 0, QModelIndex()))
        self.explorer_folder = ""
        self.explorer_path.setText("")
        self.save_state()

    @guarded
    def queue_explorer_refresh(self, changed_path):
        changed_key = self._explorer_path_key(changed_path)
        watched_keys = {self._explorer_path_key(path)
                        for path in self._explorer_watcher.directories()}
        if changed_key in watched_keys:
            # Group bursts of creates/renames (such as a multi-file download)
            # into model scans after the folder has been quiet briefly. Keep a
            # minimum interval too, so a long download queue cannot trigger a
            # full directory scan for every file that finishes.
            if self._explorer_refresh_timer.isActive():
                return
            elapsed_ms = round((time.monotonic() - self._explorer_last_refresh_at) * 1000)
            delay_ms = max(
                EXPLORER_REFRESH_DEBOUNCE_MS,
                EXPLORER_REFRESH_MIN_INTERVAL_MS - elapsed_ms,
            )
            self._explorer_refresh_timer.start(delay_ms)

    @guarded
    def refresh_explorer_folder(self):
        folder = getattr(self, "explorer_folder", "")
        if not folder or not Path(folder).is_dir() or getattr(self, "is_quitting", False):
            return

        self._explorer_last_refresh_at = time.monotonic()
        selected_paths = {
            self.file_model.filePath(index)
            for index in self.explorer.selectionModel().selectedRows(0)
        }
        self._explorer_restore_state = {
            "expanded": set(self._explorer_expanded_paths),
            "selected": selected_paths,
        }
        self._explorer_restore_timer.start()
        # Force one fresh enumeration. setRootPath(folder) alone is a no-op
        # when the root is unchanged, so clear it first.
        self.file_model.setRootPath("")
        root_index = self.file_model.setRootPath(folder)
        self.explorer.setRootIndex(root_index)

    @guarded
    def restore_explorer_view_state(self, _loaded_path=""):
        state = self._explorer_restore_state
        if not state:
            return

        expanded = state["expanded"]
        for path in tuple(expanded):
            index = self.file_model.index(path)
            if index.isValid() and self.file_model.isDir(index):
                self.explorer.setExpanded(index, True)
                expanded.discard(path)

        selected = state["selected"]
        selection = self.explorer.selectionModel()
        for path in tuple(selected):
            index = self.file_model.index(path, 0)
            if index.isValid():
                selection.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
                selected.discard(path)

        if not expanded and not selected:
            self.discard_explorer_restore_state()

    @guarded
    def discard_explorer_restore_state(self):
        self._explorer_restore_state = None
        self._explorer_mode_switching = False
        self._explorer_restore_timer.stop()

    @guarded
    def remember_explorer_expansion(self, index):
        if self.file_model.isDir(index):
            path = self.file_model.filePath(index)
            self._explorer_expanded_paths.add(path)
            # Keep full live updates for expanded trees. The debounced mode is
            # intended for a flat open folder; Qt's watcher is more reliable
            # for nested directories and is restored when the tree is collapsed.
            if not self._explorer_mode_switching:
                self._use_native_explorer_watcher()

    @guarded
    def forget_explorer_expansion(self, index):
        raw_path = self.file_model.filePath(index)
        if not raw_path:
            return
        path = self._explorer_path_key(raw_path)
        prefix = path + os.sep
        self._explorer_expanded_paths = {
            item for item in self._explorer_expanded_paths
            if (self._explorer_path_key(item) != path
                and not self._explorer_path_key(item).startswith(prefix))
        }
        if not self._explorer_expanded_paths and not self._explorer_mode_switching:
            self._use_debounced_explorer_watcher()

    @guarded
    def _use_native_explorer_watcher(self):
        if not self.file_model.testOption(QFileSystemModel.Option.DontWatchForChanges):
            return
        self._explorer_refresh_timer.stop()
        directories = self._explorer_watcher.directories()
        if directories:
            self._explorer_watcher.removePaths(directories)
        self._replace_explorer_file_model(dont_watch=False)

    @guarded
    def _use_debounced_explorer_watcher(self):
        folder = getattr(self, "explorer_folder", "")
        if (not folder or self._explorer_expanded_paths
                or self.file_model.testOption(QFileSystemModel.Option.DontWatchForChanges)):
            return
        directories = self._explorer_watcher.directories()
        if directories:
            self._explorer_watcher.removePaths(directories)
        watched = False
        try:
            watched = self._explorer_watcher.addPath(folder)
        except (OSError, RuntimeError):
            watched = False
        self._replace_explorer_file_model(dont_watch=watched)

    @guarded
    def _replace_explorer_file_model(self, *, dont_watch, preserve_view=True):
        folder = getattr(self, "explorer_folder", "")
        if not folder or not Path(folder).is_dir():
            self._explorer_mode_switching = False
            return
        selected_paths = ({
            self.file_model.filePath(index)
            for index in self.explorer.selectionModel().selectedRows(0)
        } if preserve_view else set())
        self._explorer_restore_state = {
            "expanded": set(self._explorer_expanded_paths) if preserve_view else set(),
            "selected": selected_paths,
        }
        self._explorer_mode_switching = True
        self._explorer_restore_timer.start()
        previous_model = self.file_model
        model = QFileSystemModel(self)
        model.setFilter(previous_model.filter())
        model.setNameFilters(previous_model.nameFilters())
        model.setNameFilterDisables(previous_model.nameFilterDisables())
        model.setOption(QFileSystemModel.Option.DontWatchForChanges, bool(dont_watch))
        model.directoryLoaded.connect(self.restore_explorer_view_state)
        try:
            previous_model.directoryLoaded.disconnect(self.restore_explorer_view_state)
        except (RuntimeError, TypeError):
            pass
        self.file_model = model
        self.explorer.setModel(model)
        for column in (1, 2, 3):
            self.explorer.hideColumn(column)
        root_index = model.setRootPath(folder)
        self.explorer.setRootIndex(root_index)
        previous_model.deleteLater()

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
