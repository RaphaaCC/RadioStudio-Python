"""Playlist operations for the main window."""

import os

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ._shared import *
from ..runtime_safety import guarded
from ..services.audio_paths import same_audio_path


def m3u_entries(lines):
    """Return meaningful M3U path entries with surrounding whitespace removed."""
    result = []
    for raw in lines:
        line = str(raw).strip()
        if line and not line.startswith("#"):
            result.append(line)
    return result


class _FolderScanSignals(QObject):
    finished = Signal(str, object)


class _FolderScanWorker(QRunnable):
    """Find audio files without blocking playback or the interface thread."""

    def __init__(self, folder: str):
        super().__init__()
        self.folder = folder
        self.signals = _FolderScanSignals()

    @Slot()
    def run(self):
        paths = []
        metadata_by_path = {}
        errors = 0

        def record_error(_error):
            nonlocal errors
            errors += 1

        try:
            for root, directories, files in os.walk(self.folder, onerror=record_error):
                directories.sort(key=str.casefold)
                files.sort(key=str.casefold)
                for filename in files:
                    if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
                        continue
                    path = Path(root) / filename
                    try:
                        if path.is_file():
                            path_text = str(path)
                            paths.append(path_text)
                            # Tag parsing can be slow on removable or network
                            # drives. Resolve it on this low-priority worker so
                            # the completion slot only creates table items.
                            metadata_by_path[path_text] = read_track_metadata(path_text)
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
        paths.sort(key=str.casefold)
        self.signals.finished.emit(self.folder, (paths, metadata_by_path, errors))


class PlaylistMixin:
    def _set_playlist_row(self, playlist, row, path):
        metadata = getattr(self, "_playlist_metadata_overrides", {}).pop(path, None)
        if metadata is None:
            metadata = read_track_metadata(path)
        for column, value in enumerate((metadata.title, metadata.artist, metadata.album)):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setToolTip(path)
                item.setData(Qt.UserRole, path)
                item.setData(Qt.UserRole + 1, row)
            playlist.setItem(row, column, item)

    @guarded
    def refresh_playlist(self, *, force=False):
        playlist = self.playlist
        paths = tuple(self.tracks)
        rendered_paths = getattr(self, "_rendered_playlist_paths", None)
        row_count = playlist.rowCount()

        if not force and rendered_paths == paths and row_count == len(paths):
            if 0 <= self.current_index < len(paths):
                playlist.selectRow(self.current_index)
            return

        append_only = (
            rendered_paths is not None
            and len(paths) > len(rendered_paths)
            and paths[:len(rendered_paths)] == rendered_paths
            and row_count == len(rendered_paths)
        )
        updates_enabled = playlist.updatesEnabled()
        playlist.setUpdatesEnabled(False)
        try:
            if force:
                playlist.clearSelection()
                playlist.setRowCount(0)
                playlist.setRowCount(len(paths))
                first_new_row = 0
            elif append_only:
                playlist.setRowCount(len(paths))
                first_new_row = len(rendered_paths)
            else:
                playlist.setRowCount(len(paths))
                first_new_row = 0
            for row in range(first_new_row, len(paths)):
                self._set_playlist_row(playlist, row, paths[row])
            if 0 <= self.current_index < len(self.tracks):
                playlist.selectRow(self.current_index)
            self._rendered_playlist_paths = paths
        finally:
            playlist.setUpdatesEnabled(updates_enabled)
            if updates_enabled:
                playlist.viewport().update()

    @guarded
    def commit_playlist_order(self, order):
        valid_order = isinstance(order, (list, tuple)) and len(order) == len(self.tracks)
        original_rows = []
        if valid_order:
            for entry in order:
                if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                    valid_order = False
                    break
                path, original_row = entry
                if (not isinstance(path, str) or type(original_row) is not int
                        or not 0 <= original_row < len(self.tracks)
                        or not same_audio_path(path, self.tracks[original_row])):
                    valid_order = False
                    break
                original_rows.append(original_row)
            valid_order = valid_order and sorted(original_rows) == list(range(len(self.tracks)))
        if not valid_order:
            self.refresh_playlist(force=True)
            return False
        if original_rows == list(range(len(self.tracks))):
            self.refresh_playlist(force=True)
            return True
        old_current = self.current_index
        current_origin = old_current if 0 <= old_current < len(self.tracks) else -1
        # The media deck is the source of truth if a transition or a queued
        # playback callback has made the cached queue index stale.
        try:
            playing_path = (self.original_media_path(self.player)
                            if hasattr(self, "original_media_path")
                            and hasattr(self, "player") else "")
        except Exception:
            playing_path = ""
        if playing_path and (current_origin < 0
                             or not same_audio_path(self.tracks[current_origin], playing_path)):
            matches = [index for index, path in enumerate(self.tracks)
                       if same_audio_path(path, playing_path)]
            if matches:
                current_origin = min(matches, key=lambda index: abs(index - old_current))
            else:
                current_origin = -1
        self.cancel_pending_transition()
        old_sources = list(self.track_sources)
        self.tracks = [str(path) for path, _ in order]
        self.track_sources = [old_sources[original_row] if 0 <= original_row < len(old_sources) else "manual"
                              for _path, original_row in order]
        self.current_index = next((row for row, (_path, original_row) in enumerate(order)
                                   if original_row == current_origin), -1)
        self.refresh_playlist(force=True)
        self.save_state()
        return True

    @guarded
    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Adicionar áudio", "", AUDIO_FILTER)
        self.add_audio_paths(paths)

    @guarded
    def add_audio_paths(self, paths, row=None, metadata_by_path=None, validate_files=True):
        valid = []
        for path in paths:
            normalized = str(Path(path))
            if (Path(normalized).suffix.lower() in AUDIO_EXTENSIONS
                    and (not validate_files or Path(normalized).is_file())):
                valid.append(normalized)
        if not valid:
            return
        if metadata_by_path:
            self._playlist_metadata_overrides = {
                str(path): metadata for path, metadata in metadata_by_path.items()
                if str(path) in valid
            }
        self.cancel_pending_transition()
        row = len(self.tracks) if row is None else max(0, min(row, len(self.tracks)))
        self.tracks[row:row] = valid
        self.track_sources[row:row] = ["manual"] * len(valid)
        if self.current_index >= row:
            self.current_index += len(valid)
        self.refresh_playlist()
        if self.current_index < 0:
            self.playlist.selectRow(row)
        self.save_state()
        self.statusBar().showMessage(f"{len(valid)} áudio(s) adicionado(s) à Now Playing")

    @guarded
    def playlist_menu(self, point):
        row = self.playlist.indexAt(point).row()
        if row >= 0:
            self.playlist.selectRow(row)
        menu = QMenu(self)
        preview_action = menu.addAction("Pré-escutar")
        preview_action.setEnabled(row >= 0)
        stop_action = menu.addAction("Parar escuta")
        stop_action.setEnabled(self.preview_player.playbackState() != QMediaPlayer.StoppedState)
        chosen = menu.exec(self.playlist.viewport().mapToGlobal(point))
        if chosen == preview_action:
            self.toggle_prelisten()
        elif chosen == stop_action:
            self.stop_prelisten()

    @guarded
    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Adicionar pasta")
        if not folder:
            return
        if getattr(self, "_folder_scan_running", False):
            self.statusBar().showMessage("Já há uma pasta sendo examinada")
            return
        self._folder_scan_running = True
        worker = _FolderScanWorker(folder)
        self._folder_scan_worker = worker
        worker.signals.finished.connect(self._folder_scan_finished)
        self.statusBar().showMessage("Localizando arquivos de áudio em segundo plano…")
        QThreadPool.globalInstance().start(worker, priority=-1)

    @guarded
    def _folder_scan_finished(self, folder, result):
        self._folder_scan_running = False
        self._folder_scan_worker = None
        if getattr(self, "is_quitting", False):
            return
        paths, metadata_by_path, errors = result
        before = len(self.tracks)
        if paths:
            # The worker already checked files and extracted their tags. Avoid
            # repeating filesystem probes and metadata reads on the UI thread.
            self.add_audio_paths(paths, metadata_by_path=metadata_by_path,
                                 validate_files=False)
        added = len(self.tracks) - before
        message = (f"{added} áudio(s) adicionado(s) da pasta selecionada"
                   if added else "Nenhum áudio compatível encontrado na pasta")
        if errors:
            message += f" · {errors} erro(s) de leitura ignorado(s)"
        self.statusBar().showMessage(message)

    @guarded
    def open_m3u(self):
        path, _ = QFileDialog.getOpenFileName(self, "Abrir playlist", "", "Playlist (*.m3u *.m3u8)")
        if not path:
            return
        self.cancel_pending_transition()
        base = Path(path).parent
        lines = m3u_entries(Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines())
        self.tracks = [str((base / line).resolve()) if not Path(line).is_absolute() else line
                       for line in lines]
        self.track_sources = ["manual"] * len(self.tracks)
        self.current_index = -1
        self.refresh_playlist()
        self.save_state()

    @guarded
    def save_m3u(self):
        path, _ = QFileDialog.getSaveFileName(self, "Salvar playlist", "playlist.m3u8", "Playlist (*.m3u8 *.m3u)")
        if path:
            Path(path).write_text("#EXTM3U\n" + "\n".join(self.tracks) + "\n", encoding="utf-8")

    @guarded
    def remove_track(self):
        self.remove_selected_tracks()

    @guarded
    def remove_selected_tracks(self):
        rows = sorted({index.row() for index in self.playlist.selectionModel().selectedRows()}, reverse=True)
        if not rows and self.playlist.currentRow() >= 0:
            rows = [self.playlist.currentRow()]
        if not rows:
            return
        self.cancel_pending_transition()
        old_current = self.current_index
        removed_current = self.current_index in rows
        for row in rows:
            if 0 <= row < len(self.tracks):
                del self.tracks[row]
                if row < len(self.track_sources):
                    del self.track_sources[row]
        if removed_current:
            self.stop()
            self.current_index = -1
        elif old_current >= 0:
            self.current_index = old_current - sum(row < old_current for row in rows)
        self.refresh_playlist()
        self.save_state()

    @guarded
    def move_track(self, direction):
        row = self.playlist.currentRow()
        other = row + direction
        if row < 0 or not 0 <= other < len(self.tracks):
            return
        self.cancel_pending_transition()
        self.tracks[row], self.tracks[other] = self.tracks[other], self.tracks[row]
        self.track_sources[row], self.track_sources[other] = self.track_sources[other], self.track_sources[row]
        if self.current_index == row:
            self.current_index = other
        elif self.current_index == other:
            self.current_index = row
        self.refresh_playlist()
        self.playlist.selectRow(other)
        self.save_state()

    @guarded
    def move_selected_tracks(self, direction):
        rows = sorted({index.row() for index in self.playlist.selectionModel().selectedRows()})
        if not rows and self.playlist.currentRow() >= 0:
            rows = [self.playlist.currentRow()]
        if not rows:
            return
        if direction < 0:
            if rows[0] == 0:
                return
            target = rows[0] - 1
        else:
            if rows[-1] >= len(self.tracks) - 1:
                return
            target = rows[-1] + 2
        self.reorder_tracks(rows, target)

    @guarded
    def reorder_tracks(self, rows, target):
        rows = sorted({row for row in rows if 0 <= row < len(self.tracks)})
        if not rows:
            return
        self.cancel_pending_transition()
        old_current = self.current_index
        moved = [(row, self.tracks[row], self.track_sources[row]) for row in rows]
        row_set = set(rows)
        remaining = [(index, track, self.track_sources[index]) for index, track in enumerate(self.tracks)
                     if index not in row_set]
        insert_at = max(0, min(len(remaining), target - sum(row < target for row in rows)))
        ordered = remaining[:insert_at] + moved + remaining[insert_at:]
        self.tracks = [track for _, track, _source in ordered]
        self.track_sources = [source for _, _track, source in ordered]
        if old_current >= 0:
            self.current_index = next((new_index for new_index, (old_index, _track, _source)
                                       in enumerate(ordered) if old_index == old_current), -1)
        self.refresh_playlist()
        self.playlist.clearSelection()
        selection_start = insert_at
        selection_end = insert_at + len(moved)
        for row in range(selection_start, selection_end):
            self.playlist.selectionModel().select(
                self.playlist.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.save_state()

    @guarded
    def play_selected(self, *_signal_args):
        row = self.playlist.currentRow()
        if row < 0 and self.tracks:
            row = 0
        if row >= 0:
            self.play_index(row)
