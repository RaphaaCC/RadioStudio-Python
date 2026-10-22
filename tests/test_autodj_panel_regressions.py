import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from radiostudio.i18n import translate
from radiostudio.ui.autodj_panel import AutoDJPanel, ProgramDialog


class _EmptyLibrary:
    def libraries(self):
        return []

    def collections(self):
        return []

    def rotation_groups(self):
        return []

    def unique_tags(self):
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
            self.assertIn(translate("Desativado"), dialog.source_table.item(0, 2).text())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_program_editor_saves_weighted_library_collection_group_and_tag_sources(self):
        libraries = [{"id": "music", "name": "Music", "root": "D:/Music", "enabled": 1}]
        collections = [{"id": "collection", "name": "Favorites"}]
        groups = [{"id": "group", "name": "Evening", "weight": 2.5}]
        dialog = ProgramDialog(libraries, collections=collections, groups=groups,
                               tags=["featured"])
        try:
            row_index = next(index for index, (enabled, _weight) in enumerate(dialog.source_rows)
                             if enabled.data(Qt.UserRole).get("type") == "group")
            enabled, weight = dialog.source_rows[row_index]
            enabled.setCheckState(Qt.Checked)
            weight.setValue(4.75)
            value = dialog.value()
            self.assertEqual(value["sources"], [
                {"type": "group", "id": "group", "weight": 4.75}])
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_editor_preserves_missing_sources_without_expanding_to_full_library(self):
        program = {
            "id": "block", "name": "Evening", "start": "18:00", "end": "20:00",
            "library_ids": [],
            "sources": [{"type": "collection", "id": "removed-collection", "weight": 3.5}],
        }
        dialog = ProgramDialog([], program)
        try:
            self.assertEqual(dialog.value(program)["sources"], program["sources"])
            unavailable = translate("Fonte indisponível ({type}: {id})").format(
                type=translate("Coleção"), id="removed-collection")
            self.assertIn(unavailable, dialog.source_table.item(0, 2).text())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

        main_window = _AutoDJMainWindow()
        main_window.autodj_programs = [program]
        panel = AutoDJPanel(main_window)
        try:
            summary = panel.program_table.item(0, 5).text()
            self.assertIn(unavailable, summary)
            self.assertNotIn(translate("Todas as pastas habilitadas para o AutoDJ"), summary)
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_removing_last_selected_library_does_not_expand_program_to_all(self):
        class Library:
            def __init__(self):
                self.rows = [{"id": "music", "name": "Music", "root": "D:/Music",
                              "enabled": 1, "track_count": 0}]

            def libraries(self):
                return list(self.rows)

            def collections(self):
                return []

            def rotation_groups(self):
                return []

            def unique_tags(self):
                return []

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
            missing_library = translate("Pasta indisponível ({id})").format(id="music")
            self.assertIn(missing_library, panel.program_table.item(0, 5).text())
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_program_summary_shows_collection_without_implying_all_folders(self):
        class Library(_EmptyLibrary):
            def libraries(self):
                return [{"id": "music", "name": "Music", "root": "D:/Music",
                         "enabled": 1, "track_count": 20}]

            def collections(self):
                return [{"id": "favorites", "name": "Favorites"}]

        main_window = _AutoDJMainWindow()
        main_window.music_library = Library()
        main_window.autodj_programs = [{
            "id": "favorites-block", "name": "Favorites block", "days": list(range(7)),
            "start": "18:00", "end": "20:00", "library_ids": [],
            "sources": [{"type": "collection", "id": "favorites", "weight": 1}],
            "filters": {},
        }]
        panel = AutoDJPanel(main_window)
        try:
            summary = panel.program_table.item(0, 5).text()
            self.assertIn(translate("Fontes"), summary)
            self.assertIn(translate("Coleção"), summary)
            self.assertIn("Favorites", summary)
            self.assertNotIn(translate("Todas as pastas habilitadas para o AutoDJ"), summary)
            self.assertEqual(panel.program_table.horizontalHeaderItem(5).text(),
                             translate("Fontes e filtros"))
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_program_summary_explains_empty_selection_uses_enabled_folders(self):
        main_window = _AutoDJMainWindow()
        main_window.autodj_programs = [{
            "id": "all-folders", "name": "All folders", "days": list(range(7)),
            "start": "18:00", "end": "20:00", "library_ids": [], "sources": [],
            "filters": {},
        }]
        panel = AutoDJPanel(main_window)
        try:
            summary = panel.program_table.item(0, 5).text()
            self.assertIn(translate("Pastas"), summary)
            self.assertIn(translate("Todas as pastas habilitadas para o AutoDJ"), summary)
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
