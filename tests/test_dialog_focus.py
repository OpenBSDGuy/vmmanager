import unittest

from textual.app import App
from textual.containers import VerticalScroll

from vmmanager.ui.dialogs import ConfirmScreen, MessageScreen


class DialogFocusTests(unittest.IsolatedAsyncioTestCase):
    async def focus_ids(self, screen):
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            box = app.screen.query_one(VerticalScroll)
            return [w.id for w in app.screen.focus_chain], box.can_focus

    async def test_confirm_tabs_only_between_yes_and_no(self):
        ids, _ = await self.focus_ids(ConfirmScreen("Delete x?"))
        self.assertEqual(ids, ["yes", "no"])

    async def test_long_text_stays_keyboard_scrollable(self):
        _, box_focusable = await self.focus_ids(MessageScreen("Error", "line\n" * 200))
        self.assertTrue(box_focusable)
