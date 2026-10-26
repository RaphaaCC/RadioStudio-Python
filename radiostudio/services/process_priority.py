"""Native Windows and Linux priority controls, isolated from the UI layer."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes


PROCESS_PRIORITY_OPTIONS = (
    ("Baixa", "low"),
    ("Abaixo do normal", "below_normal"),
    ("Normal", "normal"),
    ("Acima do normal", "above_normal"),
    ("Alto", "high"),
    ("Tempo real", "realtime"),
)

_PRIORITY_CLASSES = {
    "low": 0x00000040,          # IDLE_PRIORITY_CLASS
    "below_normal": 0x00004000, # BELOW_NORMAL_PRIORITY_CLASS
    "normal": 0x00000020,       # NORMAL_PRIORITY_CLASS
    "above_normal": 0x00008000, # ABOVE_NORMAL_PRIORITY_CLASS
    "high": 0x00000080,         # HIGH_PRIORITY_CLASS
    "realtime": 0x00000100,     # REALTIME_PRIORITY_CLASS
}

_NICE_VALUES = {"low": 19, "below_normal": 10, "normal": 0,
                "above_normal": -5, "high": -10}


def _apply_with_linux(priority: str) -> tuple[bool, str]:
    # Linux scheduling is per-thread. Include existing Qt/audio workers;
    # subsequently created threads inherit their creator's scheduling policy.
    from pathlib import Path

    thread_ids = [int(entry.name) for entry in Path("/proc/self/task").iterdir()]
    for thread_id in thread_ids:
        try:
            if priority == "realtime":
                os.sched_setscheduler(thread_id, os.SCHED_RR, os.sched_param(1))
            else:
                if os.sched_getscheduler(thread_id) in (os.SCHED_FIFO, os.SCHED_RR):
                    os.sched_setscheduler(thread_id, os.SCHED_OTHER, os.sched_param(0))
                os.setpriority(os.PRIO_PROCESS, thread_id, _NICE_VALUES[priority])
        except ProcessLookupError:
            # A worker may finish between enumerating and updating it.
            continue
    return True, ""


def normalize_process_priority(priority: object) -> str:
    """Return a supported priority key, falling back safely to Normal."""
    value = str(priority or "normal")
    return value if value in _PRIORITY_CLASSES else "normal"


def _apply_with_kernel32(priority_class: int, kernel32) -> tuple[bool, str]:
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = wintypes.HANDLE
    set_priority_class = kernel32.SetPriorityClass
    set_priority_class.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    set_priority_class.restype = wintypes.BOOL
    if not set_priority_class(get_current_process(), priority_class):
        error_code = ctypes.get_last_error()
        return False, f"O Windows recusou a prioridade selecionada (erro {error_code})."
    return True, ""


def apply_process_priority(priority: object) -> tuple[bool, str]:
    """Apply native scheduling without letting OS/API failures escape."""
    priority = normalize_process_priority(priority)
    try:
        if sys.platform.startswith("linux"):
            return _apply_with_linux(priority)
        if os.name != "nt":
            return False, "A prioridade do processo não está disponível neste sistema."
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        return _apply_with_kernel32(_PRIORITY_CLASSES[priority], kernel32)
    except PermissionError:
        return False, "O sistema recusou a prioridade selecionada. Verifique as permissões do usuário."
    except Exception as exc:
        return False, f"Não foi possível alterar a prioridade do processo: {exc}"
