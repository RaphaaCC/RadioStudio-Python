import unittest
from unittest.mock import Mock

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication, QTableWidget

from radiostudio.ui.broadcast_panel import BroadcastPanel, ProfileDialog


class BroadcastSignalRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_protocol_selector_accepts_current_index_signal(self):
        dialog = ProfileDialog()
        try:
            dialog.kind.setCurrentIndex(1)

            self.assertTrue(dialog.mount.isHidden())
            self.assertFalse(dialog.stream_id.isHidden())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_profile_double_click_index_is_accepted(self):
        class Harness:
            edit_profile = BroadcastPanel.edit_profile

            def __init__(self):
                self.table = Mock()
                self.table.currentRow.return_value = -1

        harness = Harness()
        table = QTableWidget()
        table.doubleClicked.connect(harness.edit_profile)
        table.doubleClicked.emit(QModelIndex())

        harness.table.currentRow.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
