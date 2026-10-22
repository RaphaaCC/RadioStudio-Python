import unittest
from datetime import datetime

from radiostudio.services.schedule_engine import (
    latest_occurrence_at_or_before, next_occurrence, normalize_event, occurrence_key,
)


class ScheduleEngineTests(unittest.TestCase):
    def test_legacy_daily_and_weekday_events_migrate_to_weekly_days(self):
        daily = normalize_event({"id": "daily", "path": "song.mp3", "time": "08:30",
                                 "day": 0, "enabled": True})
        monday = normalize_event({"id": "monday", "path": "song.mp3", "time": "08:30",
                                  "day": 1, "enabled": False})

        self.assertEqual(daily["weekdays"], list(range(7)))
        self.assertEqual(daily["action"], "play_track")
        self.assertEqual(daily["target"], "song.mp3")
        self.assertEqual(monday["weekdays"], [0])
        self.assertFalse(monday["enabled"])

    def test_once_and_weekly_occurrences_use_local_wall_clock(self):
        once = normalize_event({
            "id": "once", "enabled": True, "recurrence": "once", "date": "2026-10-22",
            "time": "09:15", "weekdays": [], "action": "playback_command",
            "target": "play",
        })
        weekly = normalize_event({
            "id": "weekly", "enabled": True, "recurrence": "weekly",
            "weekdays": [0, 3], "time": "09:15", "action": "broadcast_command",
            "target": "start",
        })
        now = datetime(2026, 10, 22, 9, 10)

        self.assertEqual(next_occurrence(once, now), datetime(2026, 10, 22, 9, 15))
        self.assertEqual(next_occurrence(weekly, now), datetime(2026, 10, 22, 9, 15))
        self.assertEqual(latest_occurrence_at_or_before(weekly, datetime(2026, 10, 22, 9, 16)),
                         datetime(2026, 10, 22, 9, 15))
        self.assertEqual(occurrence_key(datetime(2026, 10, 22, 9, 15)), "2026-10-22 09:15")

    def test_invalid_events_and_out_of_range_actions_are_rejected(self):
        invalid_time = normalize_event({"path": "song.mp3", "time": "25:00", "day": 0})
        invalid_day = normalize_event({"path": "song.mp3", "time": "12:00", "day": 8})
        malformed_enabled = normalize_event({
            "path": "song.mp3", "time": "12:00", "day": 0, "enabled": "false",
        })
        legacy_disabled = normalize_event({
            "path": "song.mp3", "time": "12:00", "day": 0, "enabled": 0,
        })
        invalid_cart = normalize_event({
            "time": "12:00", "recurrence": "weekly", "weekdays": [0],
            "action": "play_cart", "target": 25,
        })
        invalid_command = normalize_event({
            "time": "12:00", "recurrence": "weekly", "weekdays": [0],
            "action": "playback_command", "target": "broadcast",
        })
        malformed_values = normalize_event({
            "time": "12:00", "recurrence": [], "weekdays": [0],
            "action": [], "target": {},
        })

        self.assertIsNone(invalid_time)
        self.assertIsNone(invalid_day)
        self.assertIsNone(malformed_enabled)
        self.assertFalse(legacy_disabled["enabled"])
        self.assertIsNone(invalid_cart)
        self.assertIsNone(invalid_command)
        self.assertIsNone(malformed_values)


if __name__ == "__main__":
    unittest.main()
