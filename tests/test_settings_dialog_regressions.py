import unittest
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from radiostudio.services.track_metadata import DEFAULT_STATION_METADATA
from radiostudio.ui.settings_dialog import SettingsDialog
from radiostudio.ui import settings_controller
from radiostudio.ui.settings_controller import SettingsControllerMixin
from radiostudio.services.backups import RepairResult


class SettingsDialogRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_title_preview_accepts_text_changed_signal(self):
        dialog = SettingsDialog("", "", True, DEFAULT_STATION_METADATA)
        try:
            dialog.title_format.setText("%station | %title")
            dialog.station_name.setText("Rádio Nova")

            self.assertEqual(dialog.title_preview.text(), "Prévia: Rádio Nova | Título")
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_backup_settings_are_exposed_by_the_settings_dialog(self):
        dialog = SettingsDialog("", "", True, DEFAULT_STATION_METADATA,
                               backup_directory="C:/Backups",
                               backup_periodic_enabled=True,
                               backup_interval_seconds=21600,
                               backup_retention=25)
        try:
            self.assertEqual(dialog.backup_directory.text(), "C:/Backups")
            self.assertTrue(dialog.backup_interval.isEnabled())
            self.assertEqual(dialog.values()[-5:], ("C:/Backups", True, 21600, 25, "system"))
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_metadata_frequency_options_and_interval_are_exposed(self):
        dialog = SettingsDialog(
            "", "", True, DEFAULT_STATION_METADATA,
            metadata_update_mode="interval",
            metadata_update_interval_seconds=47)
        try:
            self.assertEqual(dialog.metadata_update_mode.count(), 3)
            self.assertEqual(dialog.metadata_update_mode.currentData(), "interval")
            self.assertTrue(dialog.metadata_update_interval.isEnabled())
            self.assertEqual(dialog.metadata_update_interval.value(), 47)
            self.assertEqual(dialog.values()[4:6], ("interval", 47))

            dialog.metadata_update_mode.setCurrentIndex(
                dialog.metadata_update_mode.findData("track_change"))
            self.assertFalse(dialog.metadata_update_interval.isEnabled())
            self.assertEqual(dialog.values()[4:6], ("track_change", 47))
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_control_api_defaults_to_disabled_and_keeps_secrets_out_of_state_values(self):
        dialog = SettingsDialog(
            "", "", True, DEFAULT_STATION_METADATA,
            control_api_config={"enabled": False, "port": 8091, "auth_mode": "token"})
        try:
            self.assertFalse(dialog.control_api_enabled.isChecked())
            self.assertEqual(dialog.control_api_port.value(), 8091)
            self.assertEqual(dialog.control_api_settings(), {
                "enabled": False, "port": 8091, "auth_mode": "token",
                "allowed_origins": [],
            })
            self.assertNotIn("token", dialog.control_api_settings())

            dialog.control_api_enabled.setChecked(True)
            credentials = dialog.control_api_credentials()
            self.assertTrue(credentials["token_changed"])
            self.assertGreaterEqual(len(credentials["token"]), 40)
            self.assertNotIn(credentials["token"], repr(dialog.values()))
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_control_api_basic_username_rejects_colon(self):
        dialog = SettingsDialog(
            "", "", True, DEFAULT_STATION_METADATA,
            control_api_config={"enabled": True, "auth_mode": "basic"},
            control_api_username="studio", control_api_password_configured=True)
        try:
            dialog.control_api_username.setText("studio:invalid")
            with patch("radiostudio.ui.settings_dialog.show_warning") as warning:
                dialog.accept()
            self.assertIn("dois-pontos", warning.call_args.args[2])
            self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_restart_keeps_the_original_python_module_launcher(self):
        original_arguments = [sys.executable, "-m", "radiostudio"]
        with patch.object(sys, "orig_argv", original_arguments, create=True), \
                patch.object(QProcess, "startDetached", return_value=(True, 123)) as start:
            self.assertTrue(SettingsControllerMixin._restart_application())

        self.assertEqual(start.call_args.args[0], sys.executable)
        self.assertEqual(start.call_args.args[1], [
            "-m", "radiostudio", "--radiostudio-restart-wait"])

    def test_language_change_confirmation_uses_selected_language_and_reverts_on_no(self):
        dialog = SettingsDialog("", "", True, DEFAULT_STATION_METADATA,
                                language_preference="pt_BR")
        dialog.language_preference.setCurrentIndex(
            dialog.language_preference.findData("en_US"))
        try:
            with patch("radiostudio.ui.settings_controller.ask_question",
                       return_value=QMessageBox.No) as question:
                confirmed = SettingsControllerMixin._confirm_language_restart(
                    object(), dialog, "pt_BR")

            self.assertFalse(confirmed)
            self.assertEqual(dialog.language_preference.currentData(), "pt_BR")
            self.assertEqual(question.call_args.args[1], "Language change")
            self.assertEqual(question.call_args.args[2],
                             "Restart RadioStudio now to apply the language changes?")
            self.assertEqual(question.call_args.kwargs["locale"], "en_US")
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_confirmed_language_change_retains_new_selection_for_restart(self):
        dialog = SettingsDialog("", "", True, DEFAULT_STATION_METADATA,
                                language_preference="pt_BR")
        dialog.language_preference.setCurrentIndex(
            dialog.language_preference.findData("ja_JP"))
        try:
            with patch("radiostudio.ui.settings_controller.ask_question",
                       return_value=QMessageBox.Yes) as question:
                confirmed = SettingsControllerMixin._confirm_language_restart(
                    object(), dialog, "pt_BR")

            self.assertTrue(confirmed)
            self.assertEqual(dialog.language_preference.currentData(), "ja_JP")
            self.assertEqual(question.call_args.args[1], "言語の変更")
            self.assertEqual(question.call_args.kwargs["locale"], "ja_JP")
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_successful_language_restart_starts_new_instance_then_closes_current(self):
        class Harness(SettingsControllerMixin):
            def __init__(self):
                self._skip_state_save = False
                self.is_quitting = False
                self.close = Mock()

        harness = Harness()
        with patch.object(SettingsControllerMixin, "_restart_application", return_value=True):
            restarted = SettingsControllerMixin._restart_for_language_change(
                harness, "pt_BR", "en_US")

        self.assertTrue(restarted)
        self.assertTrue(harness._skip_state_save)
        self.assertTrue(harness.is_quitting)
        harness.close.assert_called_once_with()

    def test_failed_language_restart_restores_old_preference_and_reports_in_new_language(self):
        class Harness(SettingsControllerMixin):
            def __init__(self):
                self.language_preference = "en_US"
                self.save_state = Mock()

        harness = Harness()
        with patch.object(SettingsControllerMixin, "_restart_application", return_value=False), \
                patch.object(settings_controller, "save_language_preference") as save, \
                patch.object(settings_controller, "show_warning") as warning:
            restarted = SettingsControllerMixin._restart_for_language_change(
                harness, "pt_BR", "en_US")

        self.assertFalse(restarted)
        self.assertEqual(harness.language_preference, "pt_BR")
        save.assert_called_once_with(settings_controller.LANGUAGE_FILE, "pt_BR")
        harness.save_state.assert_called_once_with()
        self.assertEqual(warning.call_args.kwargs["locale"], "en_US")

    def test_periodic_backup_waits_for_its_interval(self):
        class Harness:
            backup_periodic_enabled = True
            backup_directory = "C:/Backups"
            backup_interval_seconds = 3600
            backup_last_run = 1000
            backup_retention = 12
            _backup_retry_after = 0
            _backup_worker = None

            start_backup = Mock(return_value=True)

        harness = Harness()
        with patch("radiostudio.ui.settings_controller.time.time", return_value=2000):
            SettingsControllerMixin.check_scheduled_backup(harness)
        harness.start_backup.assert_not_called()

        with patch("radiostudio.ui.settings_controller.time.time", return_value=5000):
            SettingsControllerMixin.check_scheduled_backup(harness)
        harness.start_backup.assert_called_once_with("C:/Backups", 12, scheduled=True)

    def test_healthy_database_restarts_when_startup_is_using_temporary_fallback(self):
        class Harness(SettingsControllerMixin):
            def __init__(self):
                self._backup_worker = None
                self.autodj_panel = SimpleNamespace(scanning_libraries=set())
                self.music_library_fallback = True
                self.backup_directory = ""
                self.save_state = Mock()
                self._skip_state_save = False
                self.is_quitting = False
                self.close = Mock()

        harness = Harness()
        with patch("radiostudio.ui.settings_controller.ask_question",
                   return_value=QMessageBox.Yes) as question, \
                patch("radiostudio.ui.settings_controller.show_information") as info, \
                patch("radiostudio.ui.settings_controller.show_warning") as warning, \
                patch("radiostudio.ui.settings_controller.repair_database",
                      return_value=RepairResult(False, "O banco está íntegro.")), \
                patch.object(SettingsControllerMixin, "_restart_application",
                             return_value=True) as restart:
            SettingsControllerMixin.repair_music_database(harness)

        restart.assert_called_once_with()
        self.assertIn("índice temporário", question.call_args.args[2])
        warning.assert_not_called()
        self.assertTrue(harness._skip_state_save)
        self.assertTrue(harness.is_quitting)
        harness.close.assert_called_once_with()
        self.assertIn("índice temporário", info.call_args.args[2])

    def test_database_repair_waits_for_autodj_queue_worker(self):
        class Harness(SettingsControllerMixin):
            def __init__(self):
                self._backup_worker = None
                self._autodj_fill_worker = object()
                self.autodj_panel = SimpleNamespace(scanning_libraries=set())
                self.music_library_fallback = False
                self.backup_directory = ""

        harness = Harness()
        with patch("radiostudio.ui.settings_controller.show_warning") as warning, \
                patch("radiostudio.ui.settings_controller.repair_database") as repair:
            SettingsControllerMixin.repair_music_database(harness)

        warning.assert_called_once()
        repair.assert_not_called()

    def test_backup_import_waits_for_autodj_queue_worker(self):
        class Harness(SettingsControllerMixin):
            def __init__(self):
                self._backup_worker = None
                self._autodj_fill_worker = object()
                self.autodj_panel = SimpleNamespace(scanning_libraries=set())
                self.save_state = Mock()

        harness = Harness()
        with patch("radiostudio.ui.settings_controller.ask_question",
                   return_value=QMessageBox.Yes), \
                patch("radiostudio.ui.settings_controller.show_warning") as warning, \
                patch("radiostudio.ui.settings_controller.import_backup") as importer:
            SettingsControllerMixin.import_settings_backup(harness, "backup.zip")

        warning.assert_called_once()
        importer.assert_not_called()
        harness.save_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
