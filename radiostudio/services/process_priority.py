"""Windows process priority controls, isolated from the UI layer."""

from __future__ import annotations

import ctypes
import os
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
    """Apply a Windows priority class without letting OS/API failures escape."""
    priority = normalize_process_priority(priority)
    if os.name != "nt":
        return False, "A prioridade do processo só pode ser alterada no Windows."

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        return _apply_with_kernel32(_PRIORITY_CLASSES[priority], kernel32)
    except Exception as exc:
        return False, f"Não foi possível alterar a prioridade do processo: {exc}"
