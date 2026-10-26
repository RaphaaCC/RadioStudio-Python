import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.services.audio_paths import same_audio_path
from radiostudio.ui.scheduler import SchedulerMixin


class _SchedulerHarness(SchedulerMixin):
    def __init__(self, tracks, events=None):
        self.tracks = tracks
        self.track_sources = ["manual"] * len(tracks)
        self.current_index = 0 if tracks else -1
        self.played_index = None
        self.events = events or []
        self.last_fired = set()
        self.schedule = type("Schedule", (), {"currentRow": lambda _self: 0})()
        self.scheduler_started_at = datetime(2000, 1, 1)
        self.logged = []

    def cancel_pending_transition(self):
        pass

    def refresh_playlist(self):
        pass

    def refresh_schedule(self):
        pass

    def play_index(self, index):
        self.played_index = index
        self.current_index = index

    def save_state(self):
        pass

    def fill_autodj_queue(self):
        pass

    def log_event(self, kind, message):
        self.logged.append((kind, message))


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

    def test_event_can_fire_late_within_five_minutes_and_one_time_is_consumed(self):
        with tempfile.TemporaryDirectory() as folder:
            track = Path(folder) / "scheduled.mp3"
            track.write_bytes(b"audio")
            event = {
                "id": "one-time", "enabled": True, "recurrence": "once",
                "date": "2026-10-17", "time": "12:00", "weekdays": [],
                "action": "play_track", "target": str(track),
                "playback_mode": "at_time", "playlist_mode": "insert",
            }
            harness = _SchedulerHarness([], [event])
            harness.scheduler_started_at = datetime(2026, 10, 17, 11, 0)
            with patch("radiostudio.ui.scheduler.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2026, 10, 17, 12, 4)
                SchedulerMixin.check_schedule(harness)

            self.assertEqual(harness.tracks, [str(track)])
            self.assertEqual(harness.events[0]["last_result"], "late")
            self.assertFalse(harness.events[0]["enabled"])
            self.assertEqual(harness.played_index, 0)

    def test_event_older_than_grace_is_recorded_as_missed(self):
        event = {
            "id": "weekly", "enabled": True, "recurrence": "weekly",
            "time": "12:00", "weekdays": [5], "action": "playback_command",
            "target": "pause", "playback_mode": "at_time", "playlist_mode": "insert",
        }
        harness = _SchedulerHarness([], [event])
        with patch("radiostudio.ui.scheduler.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 10, 17, 12, 6)
            SchedulerMixin.check_schedule(harness)

        self.assertEqual(harness.events[0]["last_result"], "missed")
        self.assertTrue(harness.events[0]["enabled"])
        self.assertEqual(harness.played_index, None)

    def test_recent_occurrence_before_program_start_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as folder:
            track = Path(folder) / "scheduled.mp3"
            track.write_bytes(b"audio")
            event = {
                "id": "missed-on-launch", "enabled": True, "recurrence": "once",
                "date": "2026-10-17", "time": "12:00", "weekdays": [],
                "action": "play_track", "target": str(track),
                "playback_mode": "at_time", "playlist_mode": "insert",
            }
            harness = _SchedulerHarness([], [event])
            harness.scheduler_started_at = datetime(2026, 10, 17, 12, 1)
            with patch("radiostudio.ui.scheduler.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2026, 10, 17, 12, 2)
                SchedulerMixin.check_schedule(harness)

            self.assertEqual(harness.tracks, [])
            self.assertEqual(harness.events[0]["last_result"], "missed")
            self.assertFalse(harness.events[0]["enabled"])

    def test_after_current_mode_queues_track_without_interrupting_current(self):
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / "current.mp3"
            scheduled = Path(folder) / "scheduled.mp3"
            current.write_bytes(b"audio")
            scheduled.write_bytes(b"audio")
            event = {"path": str(scheduled), "time": "12:00", "day": 0,
                     "enabled": True, "playback_mode": "after_current"}
            harness = _SchedulerHarness([str(current)], [event])
            with patch("radiostudio.ui.scheduler.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2026, 10, 17, 12, 0)
                SchedulerMixin.check_schedule(harness)

            self.assertEqual(harness.tracks, [str(current), str(scheduled)])
            self.assertIsNone(harness.played_index)

    def test_first_media_event_controls_start_mode_for_same_minute_group(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            current, first, second, upcoming = [root / name for name in (
                "current.mp3", "first.mp3", "second.mp3", "upcoming.mp3")]
            for path in (current, first, second, upcoming):
                path.write_bytes(b"audio")
            common = {"enabled": True, "recurrence": "weekly", "time": "12:00",
                      "weekdays": [5], "action": "play_track", "playlist_mode": "insert"}
            events = [
                {**common, "id": "first", "target": str(first), "playback_mode": "after_current"},
                {**common, "id": "second", "target": str(second), "playback_mode": "at_time"},
            ]
            harness = _SchedulerHarness([str(current), str(upcoming)], events)
            with patch("radiostudio.ui.scheduler.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2026, 10, 17, 12, 0)
                SchedulerMixin.check_schedule(harness)

            self.assertEqual(harness.tracks,
                             [str(current), str(first), str(second), str(upcoming)])
            self.assertIsNone(harness.played_index)

    def test_playlist_replace_resolves_relative_entries_and_keeps_current_track(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            current = root / "current.mp3"
            upcoming = root / "upcoming.mp3"
            playlist_track = root / "subfolder" / "scheduled.mp3"
            playlist_track.parent.mkdir()
            for path in (current, upcoming, playlist_track):
                path.write_bytes(b"audio")
            playlist = root / "list.m3u8"
            playlist.write_text("#EXTM3U\nsubfolder/scheduled.mp3\n", encoding="utf-8")
            harness = _SchedulerHarness([str(current), str(upcoming)])

            SchedulerMixin.execute_event(harness, {
                "action": "play_playlist", "target": str(playlist), "time": "12:00",
                "day": 0, "playlist_mode": "replace", "playback_mode": "after_current",
            })

            self.assertEqual(harness.tracks, [str(current), str(playlist_track.resolve())])
            self.assertEqual(harness.track_sources, ["manual", "schedule"])
            self.assertIsNone(harness.played_index)

    def test_playlist_replace_preserves_current_index_for_pending_next_fade(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tracks = [root / name for name in ("played.mp3", "previous.mp3", "current.mp3",
                                                "old-next.mp3", "scheduled.mp3")]
            for path in tracks:
                path.write_bytes(b"audio")
            playlist = root / "replacement.m3u"
            playlist.write_text("scheduled.mp3\n", encoding="utf-8")
            harness = _SchedulerHarness([str(path) for path in tracks[:4]])
            harness.current_index = 2

            SchedulerMixin.execute_event(harness, {
                "action": "play_playlist", "target": str(playlist), "time": "12:00",
                "day": 0, "playlist_mode": "replace", "playback_mode": "after_current",
            })

            expected_tracks = [str(path) for path in
                               (tracks[0], tracks[1], tracks[2], tracks[4])]
            self.assertEqual(len(harness.tracks), len(expected_tracks))
            self.assertTrue(all(same_audio_path(actual, expected)
                                for actual, expected in zip(harness.tracks, expected_tracks)))
            self.assertEqual(harness.current_index, 2)
            self.assertIsNone(harness.played_index)

    def test_load_only_playlist_replaces_queue_without_starting_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            current = root / "current.mp3"
            playlist_track = root / "scheduled.mp3"
            current.write_bytes(b"audio")
            playlist_track.write_bytes(b"audio")
            playlist = root / "list.m3u"
            playlist.write_text("scheduled.mp3\n", encoding="utf-8")
            harness = _SchedulerHarness([str(current)])

            SchedulerMixin.execute_event(harness, {
                "action": "play_playlist", "target": str(playlist), "time": "12:00",
                "day": 0, "playlist_mode": "load_only",
            })

            self.assertEqual(harness.tracks, [str(current), str(playlist_track.resolve())])
            self.assertIsNone(harness.played_index)

    def test_playback_broadcast_and_cart_actions_call_existing_controllers(self):
        with tempfile.TemporaryDirectory() as folder:
            cart_path = Path(folder) / "cart.mp3"
            cart_path.write_bytes(b"audio")
            harness = _SchedulerHarness([])
            actions = []
            harness.player = SimpleNamespace(playbackState=lambda: QMediaPlayer.StoppedState)
            harness.play_selected = lambda: actions.append("play")
            harness.stop = lambda: actions.append("stop")
            harness.next_track = lambda: actions.append("next")
            harness.pause = lambda: actions.append("pause")
            harness.carts = [str(cart_path)]
            harness.play_cart = lambda index: actions.append(("cart", index))
            harness.broadcast_was_running = False
            harness.broadcast_panel = SimpleNamespace(
                start=lambda: setattr(harness, "broadcast_was_running", True),
                stop=lambda: setattr(harness, "broadcast_was_running", False),
            )

            for command in ("play", "stop", "next"):
                SchedulerMixin._execute_non_media_event(harness, {
                    "action": "playback_command", "target": command,
                })
            SchedulerMixin._execute_non_media_event(harness, {
                "action": "play_cart", "target": 0,
            })
            SchedulerMixin._execute_non_media_event(harness, {
                "action": "broadcast_command", "target": "start",
            })
            SchedulerMixin._execute_non_media_event(harness, {
                "action": "broadcast_command", "target": "stop",
            })

            self.assertEqual(actions, ["play", "stop", "next", ("cart", 0)])
            self.assertFalse(harness.broadcast_was_running)


if __name__ == "__main__":
    unittest.main()
