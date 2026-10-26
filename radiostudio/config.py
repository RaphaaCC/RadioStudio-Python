"""Application paths and shared constants."""

import hashlib
import builtins
import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "RadioStudio Python Sirius"
APP_VERSION = "v1.0.2 Stable"
APP_CODENAME = "Sirius"
APP_DISPLAY_NAME = f"{APP_NAME} — {APP_VERSION}"
ASSET_DIR = Path(__file__).resolve().parent / "assets"
APP_ICON_FILE = ASSET_DIR / ("icon.ico" if sys.platform == "win32" else "icon.png")
ABOUT_LOGO_FILE = ASSET_DIR / "logo-about-window.png"
USER_MANUAL_FILE = ASSET_DIR / "manual_usuario.pdf"
LICENSE_FILE = ASSET_DIR / "LICENSE.txt"
PROJECT_REPOSITORY_URL = "https://github.com/RaphaaCC/RadioStudio-Python"
PROJECT_ISSUES_URL = f"{PROJECT_REPOSITORY_URL}/issues"
DISCORD_APPLICATION_ID = "1561176476290908250"

def _profile_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming") / "RadioStudioPython"
    xdg_home = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    if not xdg_home.is_absolute():
        xdg_home = Path.home() / ".local" / "share"
    profile_dir = xdg_home / "RadioStudioPython"
    # Early source versions used this directory on Linux. Keep existing data
    # in place, without silently copying or overwriting a second profile.
    legacy_dir = Path.home() / "RadioStudioPython"
    if not os.getenv("XDG_DATA_HOME") and not profile_dir.exists() and legacy_dir.is_dir():
        return legacy_dir
    return profile_dir


def _app_data_dir() -> Path:
    override = os.getenv("RADIOSTUDIO_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    profile_dir = _profile_data_dir()
    if not getattr(builtins, "_RADIOSTUDIO_PORTABLE", False):
        return profile_dir

    # Keep portable settings beside the executable so the profile can travel with it.
    portable_dir = Path(sys.executable).resolve().parent / "RadioStudioPythonData"
    try:
        portable_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".write-check-", dir=portable_dir):
            pass
        return portable_dir
    except OSError:
        # Read-only locations (for example Program Files or a protected USB drive)
        # fall back to the regular user profile on either OS.
        return profile_dir


APP_DIR = _app_data_dir()
STATE_FILE = APP_DIR / "state.json"
LANGUAGE_FILE = APP_DIR / "language.cfg"
INSTALL_LANGUAGE_FILE = Path(sys.executable).resolve().parent / "language.cfg"
MUSIC_LIBRARY_FILE = APP_DIR / "music_library.sqlite3"
AUDIO_EXTENSIONS = {
    ".mp2", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a", ".m4b",
    ".mp4", ".aac", ".wma", ".aif", ".aiff", ".ac3", ".ape", ".mka",
}
AUDIO_FILTER = ("Áudio (" + " ".join(f"*{extension}" for extension in sorted(AUDIO_EXTENSIONS))
                + ");;Todos os arquivos (*)")
MAX_LOG_ROWS = 500
INSTANCE_NAME = "RadioStudioPython-" + hashlib.sha1(str(APP_DIR).encode("utf-8")).hexdigest()[:12]


def clock_text(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02}:{seconds % 60:02}"
