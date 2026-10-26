"""Open and close desktop windows using a disposable application profile.

Run from the repository root with a GUI session, Xvfb, or QT_QPA_PLATFORM=offscreen.
No audio stream, physical input, Discord connection, or broadcast is started.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    with tempfile.TemporaryDirectory(prefix="radiostudio-desktop-") as directory:
        os.environ["APPDATA"] = directory
        os.environ["RADIOSTUDIO_DATA_DIR"] = directory
        os.environ["XDG_DATA_HOME"] = str(Path(directory) / "data")
        os.environ["XDG_CONFIG_HOME"] = str(Path(directory) / "config")

        from PySide6.QtWidgets import QApplication
        from radiostudio.config import STATE_FILE
        from radiostudio.i18n import install_translations
        from radiostudio.services.track_metadata import DEFAULT_STATION_METADATA
        from radiostudio.ui.main_window import RadioStudio
        from radiostudio.ui.settings_dialog import SettingsDialog

        app = QApplication([])
        install_translations(app, "en_US")
        window = RadioStudio()
        try:
            window.show()
            app.processEvents()
            assert window.isVisible()
            dialog = SettingsDialog("", "", True, DEFAULT_STATION_METADATA, parent=window)
            dialog.show()
            app.processEvents()
            assert dialog.isVisible()
            assert dialog.main_device.itemText(0) == "System default"
            dialog.reject()
            app.processEvents()
            assert not dialog.isVisible()
        finally:
            window.request_exit()
            app.processEvents()
        assert not window.isVisible()
        assert STATE_FILE.is_file()
        print("desktop startup, settings cancellation, profile save and shutdown: OK")


if __name__ == "__main__":
    main()
