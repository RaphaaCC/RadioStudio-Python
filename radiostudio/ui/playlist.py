"""Playlist operations for the main window."""

from ._shared import *
from ..runtime_safety import guarded


def m3u_entries(lines):
    """Return meaningful M3U path entries with surrounding whitespace removed."""
    result = []
    for raw in lines:
        line = str(raw).strip()
        if line and not line.startswith("#"):
            result.append(line)
    return result


class PlaylistMixin:
    @guarded
    def refresh_playlist(self):
        self.playlist.setRowCount(len(self.tracks))
        for i, path in enumerate(self.tracks):
            metadata = read_track_metadata(path)
            for column, value in enumerate((metadata.title, metadata.artist, metadata.album)):
                item = QTableWidgetItem(value)
                item.setToolTip(path)
                if column == 0:
                    item.setData(Qt.UserRole, path)
                    item.setData(Qt.UserRole + 1, i)
                self.playlist.setItem(i, column, item)
        if 0 <= self.current_index < len(self.tracks):
            self.playlist.selectRow(self.current_index)

    @guarded
    def commit_playlist_order(self, order):
        if len(order) != len(self.tracks) or any(path is None for path, _ in order):
            self.refresh_playlist()
            return
        self.cancel_pending_transition()
        old_current = self.current_index
        old_sources = list(self.track_sources)
        self.tracks = [str(path) for path, _ in order]
        self.track_sources = [old_sources[original_row] if 0 <= original_row < len(old_sources) else "manual"
                              for _path, original_row in order]
        self.current_index = next((row for row, (_path, original_row) in enumerate(order)
                                   if original_row == old_current), old_current)
        self.refresh_playlist()
        self.save_state()

    @guarded
    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Adicionar áudio", "", AUDIO_FILTER)
        self.add_audio_paths(paths)

    @guarded
    def add_audio_paths(self, paths, row=None):
        valid = [str(Path(path)) for path in paths
                 if Path(path).is_file() and Path(path).suffix.lower() in AUDIO_EXTENSIONS]
        if not valid:
            return
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
        if folder:
            paths = [str(p) for p in sorted(Path(folder).rglob("*"))
                     if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]
            self.add_audio_paths(paths)

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
