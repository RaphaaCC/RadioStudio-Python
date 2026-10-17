"""Queue table with external-file import and live row reordering."""


from ..runtime_safety import guarded
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QTableWidget
from ..config import AUDIO_EXTENSIONS


class PlaylistTable(QTableWidget):
    """Accept audio files dragged from the built-in explorer or Windows Explorer."""

    def __init__(self, add_paths, commit_order, move_selected, remove_selected, parent=None):
        super().__init__(0, 3, parent)
        self.add_paths = add_paths
        self.commit_order = commit_order
        self.move_selected = move_selected
        self.remove_selected = remove_selected
        self._internal_drag_active = False
        self._drag_committed = False
        self._drag_items = []
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(False)
        self.setDragDropOverwriteMode(False)

    @staticmethod
    def audio_paths(event):
        return [url.toLocalFile() for url in event.mimeData().urls()
                if url.isLocalFile() and Path(url.toLocalFile()).is_file()
                and Path(url.toLocalFile()).suffix.lower() in AUDIO_EXTENSIONS]

    @guarded
    def dragEnterEvent(self, event):
        if self._internal_drag_active or self.audio_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    @guarded
    def dragMoveEvent(self, event):
        if self._internal_drag_active:
            self.move_dragged_rows(event.position().toPoint())
            event.setDropAction(Qt.MoveAction)
            event.accept()
        elif self.audio_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    @guarded
    def dropEvent(self, event):
        if self._internal_drag_active:
            order = []
            for row in range(self.rowCount()):
                item = self.item(row, 0)
                if item:
                    order.append((item.data(Qt.UserRole), item.data(Qt.UserRole + 1)))
            self._drag_committed = True
            self.commit_order(order)
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        paths = self.audio_paths(event)
        if not paths:
            event.ignore()
            return
        row = self.indexAt(event.position().toPoint()).row()
        self.add_paths(paths, row if row >= 0 else None)
        event.acceptProposedAction()

    @guarded
    def startDrag(self, supported_actions):
        rows = sorted({index.row() for index in self.selectionModel().selectedRows()})
        if not rows:
            return
        self._drag_items = [self.item(row, 0) for row in rows if self.item(row, 0)]
        self._internal_drag_active = True
        self._drag_committed = False
        try:
            super().startDrag(supported_actions)
        finally:
            if not self._drag_committed:
                self.restore_drag_order()
            self._internal_drag_active = False
            self._drag_items = []

    @guarded
    def move_dragged_rows(self, point):
        source_rows = sorted({self.row(item) for item in self._drag_items
                              if item is not None and self.row(item) >= 0})
        if not source_rows:
            return
        index = self.indexAt(point)
        insert_before = index.row() if index.isValid() else self.rowCount()
        if index.isValid() and point.y() > self.visualRect(index).center().y():
            insert_before += 1
        if source_rows[0] <= insert_before <= source_rows[-1] + 1:
            return
        destination = insert_before - sum(row < insert_before for row in source_rows)
        destination = max(0, min(self.rowCount() - len(source_rows), destination))
        if source_rows == list(range(destination, destination + len(source_rows))):
            return

        moved_rows = []
        for row in source_rows:
            moved_rows.append([self.takeItem(row, column)
                               for column in range(self.columnCount())])
        for row in reversed(source_rows):
            self.removeRow(row)
        for offset, items in enumerate(moved_rows):
            row = destination + offset
            self.insertRow(row)
            for column, item in enumerate(items):
                if item:
                    self.setItem(row, column, item)
                    item.setSelected(True)

    @guarded
    def restore_drag_order(self):
        rows = [[self.takeItem(row, column) for column in range(self.columnCount())]
                for row in range(self.rowCount())]
        rows.sort(key=lambda cells: cells[0].data(Qt.UserRole + 1)
                  if cells and cells[0] else 0)
        self.setRowCount(0)
        for row, cells in enumerate(rows):
            self.insertRow(row)
            for column, item in enumerate(cells):
                if item:
                    self.setItem(row, column, item)

    @guarded
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self.selectAll()
            event.accept()
            return
        if event.key() == Qt.Key_Delete:
            self.remove_selected()
            event.accept()
            return
        if event.key() in (Qt.Key_Up, Qt.Key_Down) and event.modifiers() & Qt.ControlModifier:
            self.move_selected(-1 if event.key() == Qt.Key_Up else 1)
            event.accept()
            return
        super().keyPressEvent(event)
