"""Profile selection must preserve data and respect each desktop OS."""

import builtins
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from radiostudio import config


class ConfigPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.contexts.enter_context(patch.dict(os.environ, {}, clear=True))
        self.contexts.enter_context(patch.object(Path, "home", return_value=self.home))
        self.contexts.enter_context(patch.object(builtins, "_RADIOSTUDIO_PORTABLE", False, create=True))

    def test_windows_keeps_existing_roaming_profile(self):
        with patch.object(config.sys, "platform", "win32"):
            os.environ["APPDATA"] = str(self.home / "Roaming")
            self.assertEqual(config._app_data_dir(), self.home / "Roaming" / "RadioStudioPython")
            del os.environ["APPDATA"]
            self.assertEqual(config._app_data_dir(), self.home / "AppData" / "Roaming" / "RadioStudioPython")

    def test_linux_uses_xdg_and_ignores_windows_environment(self):
        with patch.object(config.sys, "platform", "linux"):
            os.environ["APPDATA"] = str(self.home / "Windows")
            self.assertEqual(config._app_data_dir(), self.home / ".local" / "share" / "RadioStudioPython")
            os.environ["XDG_DATA_HOME"] = str(self.home / "xdg")
            self.assertEqual(config._app_data_dir(), self.home / "xdg" / "RadioStudioPython")
            os.environ["XDG_DATA_HOME"] = "relative-path-is-invalid"
            self.assertEqual(config._app_data_dir(), self.home / ".local" / "share" / "RadioStudioPython")

    def test_linux_keeps_legacy_data_without_overwriting_xdg_profile(self):
        with patch.object(config.sys, "platform", "linux"):
            legacy = self.home / "RadioStudioPython"
            legacy.mkdir()
            state = legacy / "state.json"
            state.write_text('{"station": "Existing"}')
            self.assertEqual(config._app_data_dir(), legacy)
            modern = self.home / ".local" / "share" / "RadioStudioPython"
            modern.mkdir(parents=True)
            self.assertEqual(config._app_data_dir(), modern)
            self.assertEqual(state.read_text(), '{"station": "Existing"}')

    def test_portable_profile_and_readonly_fallback_on_both_systems(self):
        for platform in ("win32", "linux"):
            with self.subTest(platform=platform), patch.object(config.sys, "platform", platform), \
                    patch.object(config.sys, "executable", str(self.home / "app" / "RadioStudio")), \
                    patch.object(builtins, "_RADIOSTUDIO_PORTABLE", True):
                self.assertEqual(config._app_data_dir(), self.home / "app" / "RadioStudioPythonData")
                with patch.object(config.tempfile, "NamedTemporaryFile", side_effect=PermissionError):
                    self.assertEqual(config._app_data_dir(), config._profile_data_dir())

    def test_explicit_data_directory_also_isolates_portable_execution(self):
        os.environ["RADIOSTUDIO_DATA_DIR"] = str(self.home / "isolated")
        with patch.object(builtins, "_RADIOSTUDIO_PORTABLE", True):
            self.assertEqual(config._app_data_dir(), self.home / "isolated")
