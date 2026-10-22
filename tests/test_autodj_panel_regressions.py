import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from radiostudio.i18n import translate
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

    def test_program_editor_preserves_disabled_and_missing_selected_libraries(self):
        libraries = [{"id": "disabled", "name": "Disabled", "root": "D:/Music",
                      "enabled": 0}]
        program = {"id": "block", "name": "Evening", "library_ids": ["disabled", "removed"]}
        dialog = ProgramDialog(libraries, program)
        try:
            self.assertEqual(dialog.library_list.count(), 2)
            self.assertEqual(dialog.value(program)["library_ids"], ["disabled", "removed"])
            self.assertIn(translate("Desativado"), dialog.library_list.item(0).text())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_removing_last_selected_library_does_not_expand_program_to_all(self):
        class Library:
            def __init__(self):
                self.rows = [{"id": "music", "name": "Music", "root": "D:/Music",
                              "enabled": 1, "track_count": 0}]

            def libraries(self):
                return list(self.rows)

            def recent_history(self, _limit):
                return []

            def remove_library(self, library_id):
                self.rows = [row for row in self.rows if row["id"] != library_id]

        class MainWindow(_AutoDJMainWindow):
            def __init__(self):
                super().__init__()
                self.music_library = Library()
                self.autodj_programs = [{"id": "block", "name": "Evening",
                                         "library_ids": ["music"]}]
                self.autodj_last_signature = None

            def save_state(self):
                pass

        main_window = MainWindow()
        panel = AutoDJPanel(main_window)
        try:
            panel.library_table.selectRow(0)
            with patch("radiostudio.ui.autodj_panel.ask_question",
                       return_value=QMessageBox.Yes):
                panel.remove_selected()

            self.assertEqual(main_window.autodj_programs[0]["library_ids"], ["music"])
            missing_library = translate("Biblioteca indisponível ({id})").format(id="music")
            self.assertIn(missing_library, panel.program_table.item(0, 5).text())
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
