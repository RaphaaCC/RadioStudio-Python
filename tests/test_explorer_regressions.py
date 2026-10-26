import unittest
import os
import posixpath
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from radiostudio.ui.explorer import ExplorerMixin


class ExplorerPathRegressionTests(unittest.TestCase):
    def test_collapsing_drive_root_includes_descendants(self):
        if os.name != "nt":
            self.skipTest("Drive-root regression is Windows-specific")

        self.assertTrue(ExplorerMixin._explorer_subtree_contains(
            "C:\\", "C:\\Music\\Library"))

    def test_path_prefix_does_not_match_a_sibling_directory(self):
        self.assertFalse(ExplorerMixin._explorer_subtree_contains(
            str(Path("Radio").absolute()), str(Path("RadioBackup/song.mp3").absolute())))

    def test_matching_directory_is_part_of_its_own_subtree(self):
        directory = str(Path("Radio").absolute())
        self.assertTrue(ExplorerMixin._explorer_subtree_contains(
            directory + os.sep, directory))

    @patch("radiostudio.ui.explorer.os", SimpleNamespace(path=posixpath, sep="/"))
    def test_linux_root_and_literal_backslash_directory(self):
        self.assertTrue(ExplorerMixin._explorer_subtree_contains("/", "/music/song.mp3"))
        self.assertTrue(ExplorerMixin._explorer_subtree_contains(
            "/music/Radio\\", "/music/Radio\\/song.mp3"))
        self.assertFalse(ExplorerMixin._explorer_subtree_contains(
            "/music/Radio\\", "/music/Radio/song.mp3"))


if __name__ == "__main__":
    unittest.main()
