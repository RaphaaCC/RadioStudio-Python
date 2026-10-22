import array
import io
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from radiostudio.services.broadcast import PCM_BYTES_PER_FRAME
from radiostudio.services.broadcast_quality import BroadcastManager
from radiostudio.services.recording import (
    QUEUE_CAPACITY_FRAMES, RecordingSession, sanitize_recording_schedules,
    unique_recording_path, validate_recording_schedule,
)


def pcm_frame(value=5000):
    return array.array("h", [value, -value] * (PCM_BYTES_PER_FRAME // 4)).tobytes()


class RecordingServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_recording_panel_and_schedule_dialog_open_offscreen(self):
        from radiostudio.i18n import current_preference, set_language
        from radiostudio.ui.recording_panel import RecordingPanel, RecordingScheduleDialog

        class MainWindowStub:
            recording_config = {"folder": "C:/Recordings", "format": "mp3",
                                "bitrate": 192, "auto_on_broadcast": False}
            recording_schedules = []
            recording_schedule_session = {}

            def __init__(self):
                self.broadcast = BroadcastManager()

            def save_state(self):
                pass

        previous = current_preference()
        set_language("en_US")
        main = MainWindowStub()
        panel = RecordingPanel(main)
        dialog = RecordingScheduleDialog(parent=panel)
        try:
            self.assertEqual(panel.format.currentData(), "mp3")
            self.assertEqual(panel.bitrate.currentData(), 192)
            self.assertIn("Ready", panel.status.text())
            self.assertEqual(dialog.value()["type"], "once")
            dialog.kind.setCurrentIndex(dialog.kind.findData("weekly"))
            dialog.days[0].setChecked(True)
            self.assertEqual(dialog.value()["weekdays"], [0])
        finally:
            dialog.close()
            panel.close()
            set_language(previous)
            self.app.processEvents()

    def test_mixer_thread_is_reused_if_recording_restarts_during_idle_exit(self):
        manager = BroadcastManager()
        stopping = threading.Event()
        finish_thread = threading.Event()

        def stop_from_mixer_thread():
            manager.mixer_thread = threading.current_thread()
            manager._stop_mixer_if_idle()
            stopping.set()
            finish_thread.wait(1)

        old_mixer = threading.Thread(target=stop_from_mixer_thread, daemon=True)
        old_mixer.start()
        try:
            self.assertTrue(stopping.wait(1))
            manager._ensure_mixer()
            self.assertIs(manager.mixer_thread, old_mixer)
        finally:
            finish_thread.set()
            old_mixer.join(1)
            manager.stop_event.set()
            active_mixer = manager.mixer_thread
            if active_mixer and active_mixer is not threading.current_thread():
                active_mixer.join(1)

    def test_editing_or_deleting_schedule_preserves_active_recording_deadline(self):
        from PySide6.QtWidgets import QDialog
        from types import SimpleNamespace
        from radiostudio.ui.recording_panel import RecordingPanel, RecordingScheduleDialog

        class MainWindowStub:
            def __init__(self):
                self.recording_config = {
                    "folder": "C:/Recordings", "format": "wav", "bitrate": 192,
                    "auto_on_broadcast": False,
                }
                self.recording_schedules = [{
                    "id": "schedule-1", "type": "weekly", "date": "",
                    "weekdays": [0], "start": "09:00", "end": "10:00",
                    "enabled": True,
                }]
                self.recording_schedule_session = {
                    "schedule-1": {"session_id": "active-run", "end_at": "2026-10-26T10:00:00"},
                }
                self.broadcast = SimpleNamespace(recording=None)

            def save_state(self):
                pass

        edited_main = MainWindowStub()
        edited_panel = RecordingPanel(edited_main)
        edited_panel.schedule_table.setCurrentCell(0, 0)
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.value.return_value = {
            "type": "weekly", "date": "", "weekdays": [1],
            "start": "11:00", "end": "12:00", "enabled": True,
        }
        with patch("radiostudio.ui.recording_panel.RecordingScheduleDialog",
                   return_value=dialog) as dialog_type:
            dialog_type.DAY_CHOICES = RecordingScheduleDialog.DAY_CHOICES
            edited_panel.edit_schedule()
        self.assertIn("schedule-1", edited_main.recording_schedule_session)

        deleted_main = MainWindowStub()
        deleted_panel = RecordingPanel(deleted_main)
        deleted_panel.schedule_table.setCurrentCell(0, 0)
        deleted_panel.delete_schedule()
        self.assertEqual(deleted_main.recording_schedules, [])
        self.assertIn("schedule-1", deleted_main.recording_schedule_session)

        edited_panel.close()
        deleted_panel.close()
        self.app.processEvents()

    def test_broadcast_stop_does_not_stop_an_unrelated_manual_recording(self):
        from radiostudio.ui.recording_controller import RecordingControllerMixin

        class FakeSession:
            session_id = "manual-session"
            active = True

        class FakeBroadcast:
            recording = FakeSession()

            def stop_recording(self, session_id, *, wait=False):
                if session_id and session_id != self.recording.session_id:
                    return False
                self.recording.active = False
                return True

        class Harness(RecordingControllerMixin):
            def __init__(self):
                self.broadcast = FakeBroadcast()
                self._broadcast_recording_session_id = "broadcast-session"
                self._recording_owner = "manual"

        harness = Harness()
        harness.on_broadcast_stopped()
        self.assertTrue(harness.broadcast.recording.active)
        self.assertIsNone(harness._broadcast_recording_session_id)

    def test_one_time_schedule_runs_once_and_stops_its_own_session(self):
        from datetime import datetime as RealDateTime
        from types import SimpleNamespace
        from radiostudio.ui import recording_controller
        from radiostudio.ui.recording_controller import RecordingControllerMixin

        class FrozenDateTime(RealDateTime):
            current = RealDateTime(2026, 10, 22, 9, 0)

            @classmethod
            def now(cls):
                return cls.current

        class FakeBroadcast:
            recording = None

            def start_recording(self, *_args):
                if self.recording and self.recording.active:
                    return None
                self.recording = SimpleNamespace(
                    session_id="one-time-session", active=True,
                    busy=True, path=Path("synthetic.wav"))
                return self.recording.session_id

            def stop_recording(self, session_id, *, wait=False):
                if self.recording.session_id != session_id:
                    return False
                self.recording.active = False
                self.recording.busy = False
                return True

        class Harness(RecordingControllerMixin):
            def __init__(self):
                self.recording_config = {"folder": "recordings", "format": "wav", "bitrate": 192}
                self.recording_schedules = [{
                    "id": "once", "type": "once", "date": "2026-10-22",
                    "weekdays": [], "start": "09:00", "end": "09:30", "enabled": True,
                }]
                self.recording_schedule_session = {}
                self._recording_schedule_fired = set()
                self._recording_owner = ""
                self.broadcast = FakeBroadcast()
                self.recording_panel = SimpleNamespace(refresh_schedules=lambda: None)
                self.events = []

            def save_state(self):
                pass

            def log_event(self, kind, message):
                self.events.append((kind, message))

            def statusBar(self):
                return SimpleNamespace(showMessage=lambda _message: None)

        harness = Harness()
        with patch.object(recording_controller, "datetime", FrozenDateTime):
            harness.check_recording_schedules()
            session = harness.broadcast.recording
            self.assertTrue(session.active)
            self.assertFalse(harness.recording_schedules[0]["enabled"])
            FrozenDateTime.current = RealDateTime(2026, 10, 22, 9, 30)
            harness.check_recording_schedules()
        self.assertFalse(session.active)
        self.assertNotIn("once", harness.recording_schedule_session)

    def test_weekly_overnight_schedule_tracks_its_own_end_date(self):
        from datetime import datetime as RealDateTime
        from types import SimpleNamespace
        from radiostudio.ui import recording_controller
        from radiostudio.ui.recording_controller import RecordingControllerMixin

        class FrozenDateTime(RealDateTime):
            current = RealDateTime(2026, 10, 19, 23, 30)  # Monday

            @classmethod
            def now(cls):
                return cls.current

        class FakeBroadcast:
            recording = None

            def start_recording(self, *_args):
                self.recording = SimpleNamespace(session_id="weekly-session", active=True,
                                                 busy=True, path=Path("weekly.wav"))
                return self.recording.session_id

            def stop_recording(self, session_id, *, wait=False):
                if session_id != self.recording.session_id:
                    return False
                self.recording.active = False
                return True

        class Harness(RecordingControllerMixin):
            def __init__(self):
                self.recording_config = {"folder": "recordings", "format": "wav", "bitrate": 192}
                self.recording_schedules = [{
                    "id": "weekly", "type": "weekly", "date": "", "weekdays": [0],
                    "start": "23:30", "end": "01:00", "enabled": True,
                }]
                self.recording_schedule_session = {}
                self._recording_schedule_fired = set()
                self._recording_owner = ""
                self.broadcast = FakeBroadcast()
                self.recording_panel = SimpleNamespace(refresh_schedules=lambda: None)

            def save_state(self):
                pass

            def log_event(self, *_args):
                pass

            def statusBar(self):
                return SimpleNamespace(showMessage=lambda _message: None)

        harness = Harness()
        with patch.object(recording_controller, "datetime", FrozenDateTime):
            harness.check_recording_schedules()
            self.assertEqual(harness.recording_schedule_session["weekly"]["end_at"],
                             "2026-10-20T01:00:00")
            FrozenDateTime.current = RealDateTime(2026, 10, 20, 1, 0)
            harness.check_recording_schedules()
        self.assertFalse(harness.broadcast.recording.active)

    def test_wav_pause_resume_and_finalization(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = RecordingSession(directory, "wav").start()
            frame = pcm_frame()
            self.assertTrue(recorder.submit(frame))
            self.assertTrue(recorder.pause())
            self.assertFalse(recorder.submit(frame))
            self.assertTrue(recorder.resume())
            self.assertTrue(recorder.submit(frame))
            self.assertTrue(recorder.stop())
            with wave.open(str(recorder.path), "rb") as output:
                self.assertEqual(output.getnchannels(), 2)
                self.assertEqual(output.getsampwidth(), 2)
                self.assertEqual(output.getframerate(), 44100)
                self.assertEqual(output.getnframes(), 2 * (PCM_BYTES_PER_FRAME // 4))
                self.assertNotEqual(output.readframes(1), b"\0\0\0\0")
            self.assertEqual(recorder.state, "stopped")

    def test_mp3_is_encoded_from_synthetic_stereo_pcm(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = RecordingSession(directory, "mp3", 192).start()
            frame = pcm_frame(3000)
            for _ in range(35):
                self.assertTrue(recorder.submit(frame))
            self.assertTrue(recorder.stop())
            self.assertTrue(recorder.path.is_file())
            self.assertGreater(recorder.path.stat().st_size, 512)
            self.assertFalse(recorder.partial_path.exists())

    def test_recording_without_transmission_activates_only_the_audio_bus(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = BroadcastManager()
            session_id = manager.start_recording(directory, "wav")
            self.assertIsNotNone(session_id)
            self.assertFalse(manager.running)
            self.assertTrue(manager.audio_bus_active)
            manager.push_audio("synthetic", pcm_frame() * 4)
            time.sleep(0.12)
            self.assertTrue(manager.stop_recording(session_id, wait=True))
            self.assertFalse(manager.running)
            self.assertFalse(manager.audio_bus_active)
            with wave.open(str(manager.recording.path), "rb") as output:
                self.assertGreater(output.getnframes(), 0)

    def test_stopping_transmission_keeps_manual_recording_active(self):
        class ActiveOutput:
            name = "synthetic"
            profile_id = "synthetic"
            status = "Ativo"
            thread = None
            stats_thread = None

            def feed(self, _pcm):
                pass

            def stop(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            manager = BroadcastManager()
            manager.outputs.append(ActiveOutput())
            manager.start_recording(directory, "wav")
            manager._ensure_mixer()
            manager.push_audio("synthetic", pcm_frame() * 4)
            manager.stop_transmission()
            self.assertFalse(manager.running)
            self.assertTrue(manager.recording_active)
            self.assertTrue(manager.audio_bus_active)
            time.sleep(0.05)
            self.assertTrue(manager.stop_recording(wait=True))
            self.assertFalse(manager.audio_bus_active)

    def test_queue_overflow_fails_recording_without_blocking_producer(self):
        def waiting_writer(session):
            with session._lock:
                session._started_at = time.monotonic()
                session.state = "recording"
            session._ready.set()
            session._stop_requested.wait(2)
            session._writer_finished.set()

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(RecordingSession, "_writer", waiting_writer):
                recorder = RecordingSession(directory, "wav").start()
                for _ in range(QUEUE_CAPACITY_FRAMES):
                    recorder.queue.put_nowait(pcm_frame())
                started = time.monotonic()
                self.assertFalse(recorder.submit(pcm_frame()))
                self.assertLess(time.monotonic() - started, 0.1)
                self.assertEqual(recorder.state, "error")
                self.assertIn("fila", recorder.error)
                recorder.stop()
            recorder.path.unlink(missing_ok=True)

    def test_ffmpeg_start_failure_is_isolated_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = RecordingSession(directory, "mp3", ffmpeg_path="missing-ffmpeg.exe")
            with self.assertRaises(RuntimeError):
                recorder.start()
            self.assertEqual(recorder.state, "error")
            self.assertTrue(recorder.error)
            self.assertFalse(recorder.path.exists())

    def test_ffmpeg_finalization_timeout_kills_encoder_and_keeps_partial_file(self):
        class HangingEncoder:
            instances = []

            def __init__(self, command, **_kwargs):
                self.returncode = None
                self.killed = False
                self.timeouts = []
                self.stdin = io.BytesIO()
                self.stderr = self.Stderr(self)
                Path(command[-1]).write_bytes(b"partial MP3")
                self.instances.append(self)

            class Stderr:
                def __init__(self, process):
                    self.process = process

                def read(self):
                    if self.process.returncode is None:
                        raise AssertionError("stderr was read before the encoder exited")
                    return b"encoder finalization timeout"

                def close(self):
                    pass

            def poll(self):
                return self.returncode

            def communicate(self, timeout=None):
                self.timeouts.append(timeout)
                if len(self.timeouts) == 1:
                    raise subprocess.TimeoutExpired("fake-ffmpeg", timeout)
                self.returncode = -9
                return b"", b"encoder finalization timeout"

            def kill(self):
                self.killed = True

            def wait(self, timeout=None):
                self.returncode = -9
                return self.returncode

            def terminate(self):
                self.returncode = -15

        with tempfile.TemporaryDirectory() as directory:
            with patch("radiostudio.services.recording.subprocess.Popen", HangingEncoder):
                recorder = RecordingSession(directory, "mp3", ffmpeg_path="fake-ffmpeg").start()
                self.assertTrue(recorder.submit(pcm_frame()))
                self.assertFalse(recorder.stop(timeout=3))

            process = HangingEncoder.instances[-1]
            self.assertTrue(process.killed)
            self.assertEqual(process.timeouts, [8, 2])
            self.assertEqual(recorder.state, "error")
            self.assertIn("timeout", recorder.error)
            self.assertFalse(recorder._thread.is_alive())
            self.assertTrue(recorder.partial_path.is_file())

    def test_ffmpeg_encoder_failure_keeps_partial_file_and_does_not_stop_stream(self):
        class BrokenInput:
            def write(self, _data):
                raise BrokenPipeError("synthetic encoder failure")

            def close(self):
                pass

        class BrokenEncoder:
            def __init__(self, *args, **kwargs):
                self.stdin = BrokenInput()
                self.stderr = io.BytesIO(b"synthetic encoder failure")
                self.terminated = False
                Path(args[0][-1]).write_bytes(b"partial-mp3")

            def poll(self):
                return None if not self.terminated else 1

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 1

            def kill(self):
                self.terminated = True

        class ActiveOutput:
            name = "synthetic"
            profile_id = "synthetic"
            status = "Ativo"
            thread = None
            stats_thread = None

            def feed(self, _pcm):
                pass

            def stop(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            manager = BroadcastManager()
            manager.outputs.append(ActiveOutput())
            recorder = RecordingSession(directory, "mp3")
            manager.recording_session = recorder
            with patch.object(RecordingSession, "_ffmpeg_command",
                              side_effect=lambda output: ["mock-ffmpeg", str(output)]), \
                    patch("radiostudio.services.recording.subprocess.Popen", BrokenEncoder):
                recorder.start()
                self.assertTrue(recorder.submit(pcm_frame()))
                self.assertTrue(recorder._writer_finished.wait(1))
                self.assertFalse(recorder._thread.is_alive())
                self.assertEqual(recorder.state, "error")
                self.assertTrue(recorder.partial_path.is_file())
                self.assertTrue(manager.running)
                self.assertFalse(manager.stop_event.is_set())
                self.assertFalse(manager.stop_recording("another-session", wait=True))
                self.assertEqual(manager.recording.session_id, recorder.session_id)
            manager.stop()

    def test_timestamped_filename_never_overwrites_existing_file(self):
        stamp = datetime(2026, 10, 22, 18, 40, 5)
        with tempfile.TemporaryDirectory() as directory:
            first = unique_recording_path(directory, "wav", stamp)
            second = unique_recording_path(directory, "wav", stamp)
            self.assertNotEqual(first, second)
            self.assertEqual(first.name, "RadioStudio_20261022_184005.wav")
            self.assertEqual(second.name, "RadioStudio_20261022_184005_01.wav")

    def test_partial_file_from_failed_session_is_not_reused(self):
        stamp = datetime(2026, 10, 22, 18, 40, 6)
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "RadioStudio_20261022_184006.partial.wav"
            partial.write_bytes(b"preserved partial")
            path = unique_recording_path(directory, "wav", stamp)
            self.assertEqual(path.name, "RadioStudio_20261022_184006_01.wav")
            self.assertEqual(partial.read_bytes(), b"preserved partial")

    def test_schedule_validation_supports_overnight_and_rejects_equal_times(self):
        overnight = validate_recording_schedule({
            "type": "weekly", "weekdays": [0, 4], "start": "23:30", "end": "01:00",
        })
        self.assertEqual(overnight["weekdays"], [0, 4])
        with self.assertRaisesRegex(ValueError, "não pode ser igual"):
            validate_recording_schedule({
                "type": "once", "date": "2026-10-22", "start": "09:00", "end": "09:00",
            })
        self.assertEqual(sanitize_recording_schedules([
            {"type": "weekly", "weekdays": [], "start": "12:00", "end": "13:00"},
            {"type": "once", "date": "2026-10-23", "start": "21:00", "end": "22:00"},
        ]).__len__(), 1)

    def test_duplicate_schedule_ids_are_repaired_during_state_sanitization(self):
        source = [
            {"id": "duplicate", "type": "weekly", "weekdays": [0],
             "start": "09:00", "end": "10:00"},
            {"id": "duplicate", "type": "weekly", "weekdays": [1],
             "start": "11:00", "end": "12:00"},
        ]

        schedules = sanitize_recording_schedules(source)

        self.assertEqual(len(schedules), 2)
        self.assertNotEqual(schedules[0]["id"], schedules[1]["id"])
        self.assertEqual([schedule["start"] for schedule in schedules], ["09:00", "11:00"])

    def test_malformed_recording_schedule_enabled_value_fails_closed(self):
        schedules = sanitize_recording_schedules([{
            "type": "weekly", "weekdays": [0], "start": "09:00", "end": "10:00",
            "enabled": "false",
        }])

        self.assertEqual(len(schedules), 1)
        self.assertFalse(schedules[0]["enabled"])


if __name__ == "__main__":
    unittest.main()
