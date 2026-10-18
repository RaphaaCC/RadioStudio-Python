from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem
from radiostudio.ui.autodj_panel import AutoDJPanel


class AutoDJHistoryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_hidden_history_does_not_query_or_rebuild_the_history_table(self):
        tabs = Mock()
        tabs.currentIndex.return_value = 0
        library = Mock()
        history_table = Mock()
        panel = SimpleNamespace(
            tabs=tabs, history_tab_index=2, library=library,
            history_table=history_table, isVisible=Mock(return_value=True),
        )

        AutoDJPanel.refresh_history(panel)

        library.recent_history.assert_not_called()
        history_table.setRowCount.assert_not_called()

    def test_visible_history_refreshes_from_the_library(self):
        tabs = Mock()
        tabs.currentIndex.return_value = 2
        library = Mock()
        library.recent_history.return_value = []
        history_table = Mock()
        timer = Mock()
        panel = SimpleNamespace(
            tabs=tabs, history_tab_index=2, library=library,
            history_table=history_table, isVisible=Mock(return_value=True),
            _history_refresh_timer=timer,
        )

        AutoDJPanel.refresh_history(panel)

        library.recent_history.assert_called_once_with(1000)
        history_table.setRowCount.assert_called_once_with(0)
        timer.stop.assert_called_once_with()

    @patch("radiostudio.ui.autodj_panel.QApplication.instance")
    def test_visible_history_waits_until_modal_dialog_closes(self, application_instance):
        application = Mock()
        application.activeModalWidget.return_value = object()
        application_instance.return_value = application
        tabs = Mock()
        tabs.currentIndex.return_value = 2
        library = Mock()
        history_table = Mock()
        timer = Mock()
        panel = SimpleNamespace(
            tabs=tabs, history_tab_index=2, library=library,
            history_table=history_table, isVisible=Mock(return_value=True),
            _history_refresh_timer=timer,
        )

        AutoDJPanel.refresh_history(panel)

        library.recent_history.assert_not_called()
        history_table.setRowCount.assert_not_called()
        timer.start.assert_called_once_with(1000)

    def test_incremental_history_refresh_only_inserts_newer_entries(self):
        entries = [
            {"id": 7, "played_at": 70, "title": "Seven", "artist": "A", "album": "",
             "genre": "", "year": "", "path": "seven.mp3"},
            {"id": 6, "played_at": 60, "title": "Six", "artist": "A", "album": "",
             "genre": "", "year": "", "path": "six.mp3"},
            {"id": 5, "played_at": 50, "title": "Five", "artist": "A", "album": "",
             "genre": "", "year": "", "path": "five.mp3"},
        ]
        table = QTableWidget(2, 7)
        for row, entry in enumerate(entries[1:]):
            item = QTableWidgetItem(entry["title"])
            item.setData(Qt.UserRole, entry["played_at"])
            item.setData(Qt.UserRole + 1, entry["id"])
            table.setItem(row, 0, item)
        library = Mock()
        library.history_after_id.return_value = entries
        panel = SimpleNamespace(
            tabs=SimpleNamespace(currentIndex=lambda: 2), history_tab_index=2,
            library=library, history_table=table, isVisible=lambda: True,
            _history_refresh_timer=Mock(),
        )

        AutoDJPanel.refresh_history(panel, incremental=True)

        self.assertEqual(table.rowCount(), 3)
        self.assertEqual(table.item(0, 0).data(Qt.UserRole + 1), 7)
        self.assertEqual(table.item(0, 0).data(Qt.UserRole), 70)
        self.assertEqual(table.item(0, 1).text(), "Seven")
        library.history_after_id.assert_called_once_with(6, 1000)

    def test_reopening_history_uses_incremental_refresh(self):
        refresh = Mock()
        panel = SimpleNamespace(
            history_tab_index=2,
            history_table=SimpleNamespace(rowCount=lambda: 12),
            refresh_history=refresh,
        )

        AutoDJPanel._tab_changed(panel, 2)

        refresh.assert_called_once_with(incremental=True)

    def test_incremental_history_with_no_new_entries_keeps_existing_rows(self):
        table = QTableWidget(1, 7)
        existing = QTableWidgetItem("Existing")
        existing.setData(Qt.UserRole, 70)
        existing.setData(Qt.UserRole + 1, 7)
        table.setItem(0, 0, existing)
        library = Mock()
        library.history_after_id.return_value = []
        panel = SimpleNamespace(
            tabs=SimpleNamespace(currentIndex=lambda: 2), history_tab_index=2,
            library=library, history_table=table, isVisible=lambda: True,
            _history_refresh_timer=Mock(),
        )

        AutoDJPanel.refresh_history(panel, incremental=True)

        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "Existing")
        library.recent_history.assert_not_called()

    def test_out_of_order_history_timestamp_falls_back_to_full_display_window(self):
        table = QTableWidget(1, 7)
        existing = QTableWidgetItem("Later")
        existing.setData(Qt.UserRole, 100)
        existing.setData(Qt.UserRole + 1, 8)
        table.setItem(0, 0, existing)
        earlier = {"id": 9, "played_at": 90, "title": "Earlier", "artist": "A",
                   "album": "", "genre": "", "year": "", "path": "earlier.mp3"}
        later = {"id": 8, "played_at": 100, "title": "Later", "artist": "A",
                 "album": "", "genre": "", "year": "", "path": "later.mp3"}
        library = Mock()
        library.history_after_id.return_value = [earlier]
        library.recent_history.return_value = [later, earlier]
        panel = SimpleNamespace(
            tabs=SimpleNamespace(currentIndex=lambda: 2), history_tab_index=2,
            library=library, history_table=table, isVisible=lambda: True,
            _history_refresh_timer=Mock(),
        )

        AutoDJPanel.refresh_history(panel, incremental=True)

        self.assertEqual(table.rowCount(), 2)
        self.assertEqual(table.item(0, 0).data(Qt.UserRole + 1), 8)
        self.assertEqual(table.item(1, 0).data(Qt.UserRole + 1), 9)
        library.recent_history.assert_called_once_with(1000)


if __name__ == "__main__":
    unittest.main()
