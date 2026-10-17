"""Crash containment and persistent diagnostics for optional application actions."""

from __future__ import annotations

import logging
import sys
import threading
import time
import tempfile
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

_logger = logging.getLogger("radiostudio")
_recent_failures: dict[str, float] = {}
_failure_lock = threading.Lock()


def configure_exception_logging(app_dir: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        app_dir.mkdir(parents=True, exist_ok=True)
        log_path = app_dir / "app.log"
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
        if not any(isinstance(item, RotatingFileHandler) and
                   Path(item.baseFilename) == log_path.resolve() for item in root.handlers):
            root.addHandler(handler)
        else:
            handler.close()
    except OSError:
        # A read-only or unavailable profile directory must not prevent startup.
        try:
            fallback = Path(tempfile.gettempdir()) / "RadioStudioPython-app.log"
            handler = RotatingFileHandler(fallback, maxBytes=2_000_000,
                                          backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
            root.addHandler(handler)
        except OSError:
            logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    previous_hook = sys.excepthook
    def handle_exception(exc_type, exc_value, traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc_value, traceback)
            return
        _logger.critical("Exceção não tratada na thread principal",
                         exc_info=(exc_type, exc_value, traceback))
    sys.excepthook = handle_exception

    if hasattr(threading, "excepthook"):
        def handle_thread_exception(args):
            thread_name = args.thread.name if args.thread else "?"
            _logger.critical("Exceção não tratada na thread %s", thread_name,
                             exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        threading.excepthook = handle_thread_exception


def guarded(function):
    """Keep a failing UI callback or timer from terminating the whole application."""
    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            key = function.__qualname__
            now = time.monotonic()
            with _failure_lock:
                report = now - _recent_failures.get(key, 0.0) >= 5.0
                if report:
                    _recent_failures[key] = now
            if report:
                _logger.exception("Falha isolada em %s", key)
                owner = args[0] if args else None
                if owner is not None and function.__name__ != "log_event":
                    log_event = getattr(owner, "log_event", None)
                    if callable(log_event):
                        try:
                            log_event("error", f"Falha em {function.__name__}: {exc}")
                        except Exception:
                            _logger.exception("Não foi possível registrar a falha na interface")
            return None
    return invoke
