import unittest

from PySide6.QtWidgets import QApplication

from radiostudio.ui.autodj_panel import AutoDJPanel, ProgramDialog


class _EmptyLibrary:
    def libraries(self):
        return []

    def recent_history(self, _limit):
        return []


class _AutoDJMainWindow:
    def __init__(self):
        self.music_library = _EmptyLibrary()
        self.autodj_config = {"enabled": False}
        self.autodj_programs = []
        self.fill_requests = []

    def set_autodj_enabled(self, enabled):
        self.autodj_config["enabled"] = enabled

    def fill_autodj_queue(self, **kwargs):
        self.fill_requests.append(kwargs)


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

    def test_complete_queue_button_bypasses_periodic_throttle(self):
        main_window = _AutoDJMainWindow()
        panel = AutoDJPanel(main_window)
        try:
            panel.fill_button.click()
            self.assertEqual(main_window.fill_requests, [{"force": True}])
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
