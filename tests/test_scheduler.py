import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from radiostudio.ui.scheduler import SchedulerMixin


class _SchedulerHarness(SchedulerMixin):
    def __init__(self, tracks, events=None):
        self.tracks = tracks
        self.track_sources = ["manual"] * len(tracks)
        self.current_index = 0
        self.played_index = None
        self.events = events or []
        self.last_fired = set()
        self.schedule = type("Schedule", (), {"currentRow": lambda _self: 0})()

    def cancel_pending_transition(self):
        pass

    def refresh_playlist(self):
        pass

    def play_index(self, index):
        self.played_index = index
        self.current_index = index

    def save_state(self):
        pass

    def fill_autodj_queue(self):
        pass


class SchedulerTests(unittest.TestCase):
    def test_scheduled_track_runs_next_and_preserves_existing_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / "current.mp3"
            scheduled = Path(folder) / "scheduled.mp3"
            upcoming = Path(folder) / "upcoming.mp3"
            for path in (current, scheduled, upcoming):
                path.write_bytes(b"audio")

            harness = _SchedulerHarness([str(current), str(upcoming)])
            SchedulerMixin.execute_event(harness, {"path": str(scheduled)})

            self.assertEqual(harness.tracks,
                             [str(current), str(scheduled), str(upcoming)])
            self.assertEqual(harness.track_sources,
                             ["manual", "schedule", "manual"])
            self.assertEqual(harness.played_index, 1)

    def test_run_now_does_not_fire_the_same_schedule_again_in_that_minute(self):
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / "current.mp3"
            scheduled = Path(folder) / "scheduled.mp3"
            current.write_bytes(b"audio")
            scheduled.write_bytes(b"audio")
            event = {"id": "stable-event-id", "path": str(scheduled), "time": "12:00",
                     "day": 0, "enabled": True}
            harness = _SchedulerHarness([str(current)], [event])
            now = datetime(2026, 10, 17, 12, 0)
            with patch("radiostudio.ui.scheduler.datetime") as mock_datetime:
                mock_datetime.now.return_value = now
                SchedulerMixin.run_event(harness)
                SchedulerMixin.check_schedule(harness)

            self.assertEqual(harness.tracks.count(str(scheduled)), 1)

    def test_same_minute_events_are_queued_in_order_instead_of_interrupting_each_other(self):
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / "current.mp3"
            first = Path(folder) / "first.mp3"
            second = Path(folder) / "second.mp3"
            upcoming = Path(folder) / "upcoming.mp3"
            for path in (current, first, second, upcoming):
                path.write_bytes(b"audio")
            events = [
                {"id": "first", "path": str(first), "time": "12:00", "day": 0, "enabled": True},
                {"id": "second", "path": str(second), "time": "12:00", "day": 0, "enabled": True},
            ]
            harness = _SchedulerHarness([str(current), str(upcoming)], events)
            with patch("radiostudio.ui.scheduler.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2026, 10, 17, 12, 0)
                SchedulerMixin.check_schedule(harness)

            self.assertEqual(harness.tracks,
                             [str(current), str(first), str(second), str(upcoming)])
            self.assertEqual(harness.track_sources,
                             ["manual", "schedule", "schedule", "manual"])
            self.assertEqual(harness.played_index, 1)


if __name__ == "__main__":
    unittest.main()
