import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from radiostudio.i18n import set_language
from radiostudio.ui.audio_inputs_panel import AudioInputsPanel
from radiostudio.ui.broadcast_panel import BroadcastPanel


class BroadcastPanelRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _main_window_stub():
        broadcast = Mock(outputs=[], local_server=None, startup_errors={})
        on_air = SimpleNamespace(set_broadcast_active=Mock())
        return SimpleNamespace(broadcast=broadcast, on_air=on_air)

    def test_malformed_saved_flags_do_not_enable_network_broadcast(self):
        panel = BroadcastPanel(self._main_window_stub())
        try:
            panel.load_config(
                [{"id": "remote", "enabled": "false", "tls": "false"}],
                {"enabled": "false", "lan": "false", "send_cover_art": "false"},
            )

            self.assertFalse(panel.profiles[0]["enabled"])
            self.assertFalse(panel.profiles[0]["tls"])
            self.assertFalse(panel.local_enabled.isChecked())
            self.assertFalse(panel.lan.isChecked())
            self.assertFalse(panel.local_send_cover.isChecked())
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_missing_profile_enabled_flag_keeps_legacy_default(self):
        panel = BroadcastPanel(self._main_window_stub())
        try:
            panel.load_config([{"id": "legacy-profile"}], {})

            self.assertTrue(panel.profiles[0]["enabled"])
            self.assertFalse(panel.local_enabled.isChecked())
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    def test_network_address_and_audio_input_tooltips_localize_dynamic_labels(self):
        set_language("en_US")
        broadcast_panel = BroadcastPanel(self._main_window_stub())
        inputs_panel = AudioInputsPanel(lambda: None)
        try:
            broadcast_panel.lan.setChecked(True)

            self.assertIn("This computer's IP", broadcast_panel.local_url.text())
            self.assertNotIn("IP desta máquina", broadcast_panel.local_url.text())
            self.assertEqual(
                inputs_panel.buttons["microphone"].toolTip(),
                "Enable or disable Microphone on the Master bus")
            self.assertEqual(
                inputs_panel.buttons["aux1"].toolTip(),
                "Enable or disable Auxiliary 1 on the Master bus")
        finally:
            broadcast_panel.close()
            inputs_panel.close()
            broadcast_panel.deleteLater()
            inputs_panel.deleteLater()
            self.app.processEvents()
            set_language("pt_BR")


if __name__ == "__main__":
    unittest.main()
