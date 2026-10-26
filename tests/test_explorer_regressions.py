import unittest

from radiostudio.ui.explorer import ExplorerMixin


class ExplorerPathRegressionTests(unittest.TestCase):
    def test_collapsing_drive_root_includes_descendants(self):
        if __import__("os").name != "nt":
            self.skipTest("Drive-root regression is Windows-specific")

        self.assertTrue(ExplorerMixin._explorer_subtree_contains(
            "C:\\", "C:\\Music\\Library"))

    def test_path_prefix_does_not_match_a_sibling_directory(self):
        self.assertFalse(ExplorerMixin._explorer_subtree_contains(
            "C:\\Radio", "C:\\RadioBackup\\song.mp3"))

    def test_matching_directory_is_part_of_its_own_subtree(self):
        self.assertTrue(ExplorerMixin._explorer_subtree_contains(
            "C:\\Radio\\", "C:\\Radio"))


if __name__ == "__main__":
    unittest.main()
