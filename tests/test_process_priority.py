import unittest

from radiostudio.services.process_priority import (
    PROCESS_PRIORITY_OPTIONS,
    _PRIORITY_CLASSES,
    _apply_with_kernel32,
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
