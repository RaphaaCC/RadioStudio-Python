"""Application paths and shared constants."""

import hashlib
import os
from pathlib import Path

APP_NAME = "RadioStudio Python"
APP_VERSION = "1.0.0"
APP_CODENAME = "Sirius"
APP_DISPLAY_NAME = f"{APP_NAME} {APP_VERSION} — {APP_CODENAME}"

APP_DIR = Path(os.getenv("APPDATA") or Path.home()) / "RadioStudioPython"
STATE_FILE = APP_DIR / "state.json"
AUDIO_FILTER = "Áudio (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma);;Todos os arquivos (*)"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}
MAX_LOG_ROWS = 500
INSTANCE_NAME = "RadioStudioPython-" + hashlib.sha1(str(APP_DIR).encode("utf-8")).hexdigest()[:12]


def clock_text(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02}:{seconds % 60:02}"
