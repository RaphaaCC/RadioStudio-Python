import unittest

from PySide6.QtWidgets import QApplication

from radiostudio.ui.autodj_panel import ProgramDialog


class AutoDJPanelRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_program_defaults_to_all_weekdays(self):
        dialog = ProgramDialog([])
        try:
            self.assertEqual(len(dialog.days), 7)
            self.assertTrue(all(check.isChecked() for check in dialog.days))
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
