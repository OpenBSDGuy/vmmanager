import tempfile
import unittest
from pathlib import Path

from textual.app import App

from vmmanager.ui.dialogs import IsoPickerScreen, IsoTree


class IsoPickerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for p in ["a.iso", ".b.iso", "notes.txt", "dir/x", ".cache/y"]:
            (self.root / p).parent.mkdir(parents=True, exist_ok=True)
            (self.root / p).write_text("")

    def shown(self, tree):
        return sorted(p.name for p in tree.filter_paths(self.root.iterdir()))

    async def test_dot_key_toggles_hidden_entries(self):
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(IsoPickerScreen(self.root))
            await pilot.pause()
            tree = app.screen.query_one(IsoTree)
            self.assertEqual(self.shown(tree), ["a.iso", "dir"])
            await pilot.press("full_stop")
            self.assertEqual(self.shown(tree), [".b.iso", ".cache", "a.iso", "dir"])
            await pilot.press("full_stop")
            self.assertEqual(self.shown(tree), ["a.iso", "dir"])
