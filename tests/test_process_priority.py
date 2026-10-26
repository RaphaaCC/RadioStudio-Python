import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from radiostudio.services.process_priority import (
    PROCESS_PRIORITY_OPTIONS,
    _PRIORITY_CLASSES,
    _apply_with_kernel32,
    _apply_with_linux,
    apply_process_priority,
    normalize_process_priority,
)


class _FakeFunction:
    def __init__(self, result):
        self.result = result
        self.args = None
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.args = args
        return self.result


class _FakeKernel32:
    def __init__(self):
        self.GetCurrentProcess = _FakeFunction(123)
        self.SetPriorityClass = _FakeFunction(1)


class ProcessPriorityTests(unittest.TestCase):
    def linux_api(self):
        return SimpleNamespace(
            PRIO_PROCESS=0, SCHED_OTHER=0, SCHED_FIFO=1, SCHED_RR=2,
            sched_getscheduler=Mock(return_value=0),
            sched_setscheduler=Mock(), sched_param=lambda value: value,
            setpriority=Mock())

    @patch("pathlib.Path.iterdir", return_value=[Path("101"), Path("102")])
    def test_linux_priority_updates_existing_workers_and_resets_realtime(self, _entries):
        api = self.linux_api()
        api.sched_getscheduler.return_value = api.SCHED_RR
        with patch("radiostudio.services.process_priority.os", api):
            self.assertEqual(_apply_with_linux("above_normal"), (True, ""))
        for thread_id in (101, 102):
            api.setpriority.assert_any_call(api.PRIO_PROCESS, thread_id, -5)
            api.sched_setscheduler.assert_any_call(thread_id, api.SCHED_OTHER, 0)

    @patch("pathlib.Path.iterdir", return_value=[Path("101")])
    def test_linux_realtime_uses_round_robin_scheduler(self, _entries):
        api = self.linux_api()
        with patch("radiostudio.services.process_priority.os", api):
            self.assertEqual(_apply_with_linux("realtime"), (True, ""))
        api.sched_setscheduler.assert_called_once_with(101, api.SCHED_RR, 1)
        api.setpriority.assert_not_called()

    @patch("pathlib.Path.iterdir", return_value=[Path("101"), Path("102")])
    def test_linux_worker_exit_does_not_block_other_workers(self, _entries):
        api = self.linux_api()
        api.setpriority.side_effect = [ProcessLookupError(), None]
        with patch("radiostudio.services.process_priority.os", api):
            self.assertEqual(_apply_with_linux("normal"), (True, ""))
        self.assertEqual(api.setpriority.call_count, 2)

    @patch("radiostudio.services.process_priority.sys.platform", "linux")
    @patch("radiostudio.services.process_priority._apply_with_linux", side_effect=PermissionError)
    def test_linux_permission_failure_is_reported_without_escaping(self, _apply):
        applied, message = apply_process_priority("high")
        self.assertFalse(applied)
        self.assertIn("permissões", message)

    def test_all_windows_priority_classes_have_a_choice(self):
        self.assertEqual({value for _label, value in PROCESS_PRIORITY_OPTIONS},
                         set(_PRIORITY_CLASSES))

    def test_unknown_priority_falls_back_to_normal(self):
        self.assertEqual(normalize_process_priority("invalid"), "normal")
        self.assertEqual(normalize_process_priority(None), "normal")

    def test_priority_class_is_applied_to_current_process(self):
        kernel32 = _FakeKernel32()

        result = _apply_with_kernel32(_PRIORITY_CLASSES["above_normal"], kernel32)

        self.assertEqual(result, (True, ""))
        self.assertEqual(kernel32.SetPriorityClass.args,
                         (123, _PRIORITY_CLASSES["above_normal"]))


if __name__ == "__main__":
    unittest.main()
