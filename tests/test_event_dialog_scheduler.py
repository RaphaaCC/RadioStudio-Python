import unittest

from PySide6.QtWidgets import QApplication

from radiostudio.ui.event_dialog import EventDialog


class EventDialogSchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_event_defaults_to_daily_weekly_schedule(self):
        dialog = EventDialog()
        try:
            value = dialog.value()
            self.assertEqual(value["recurrence"], "weekly")
            self.assertEqual(value["weekdays"], list(range(7)))
            self.assertEqual(value["playback_mode"], "at_time")
            self.assertEqual(value["playlist_mode"], "insert")
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_action_and_recurrence_fields_keep_internal_values(self):
        dialog = EventDialog()
        try:
            dialog.action.setCurrentIndex(dialog.action.findData("play_playlist"))
            dialog.recurrence.setCurrentIndex(dialog.recurrence.findData("once"))
            dialog.playlist_mode.setCurrentIndex(dialog.playlist_mode.findData("load_only"))
            dialog.target_path = "D:/playlists/show.m3u8"
            value = dialog.value()

            self.assertEqual(value["action"], "play_playlist")
            self.assertEqual(value["recurrence"], "once")
            self.assertEqual(value["playlist_mode"], "load_only")
            self.assertEqual(value["target"], "D:/playlists/show.m3u8")
            self.assertEqual(value["weekdays"], [])
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
