import ast
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from radiostudio.ui.icons import UI_ICON_DIR, explorer_icon, ui_icon


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE_DIR = ROOT / "radiostudio" / "ui"


class UIIconAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_literal_icon_references_have_renderable_assets(self):
        references = set()
        for source in UI_SOURCE_DIR.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                name = function.id if isinstance(function, ast.Name) else None
                if name not in {"ui_icon", "explorer_icon"} or not node.args:
                    continue
                argument = node.args[0]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    references.add((name, argument.value))

        missing = sorted(
            f"{function}({name!r})" for function, name in references
            if not ((UI_ICON_DIR / "explorer" if function == "explorer_icon" else UI_ICON_DIR)
                    / f"{name}.svg").is_file())
        self.assertFalse(missing, f"UI icon references have no SVG assets: {missing}")
        for function, name in sorted(references):
            with self.subTest(icon=name):
                icon = explorer_icon(name) if function == "explorer_icon" else ui_icon(name)
                self.assertFalse(icon.isNull(), f"Could not render {name}.svg")

    def test_library_folder_tag_and_undo_icons_render(self):
        for name in ("folder_open", "tag", "undo"):
            with self.subTest(icon=name):
                self.assertTrue((UI_ICON_DIR / f"{name}.svg").is_file())
                self.assertFalse(ui_icon(name).isNull())


if __name__ == "__main__":
    unittest.main()
