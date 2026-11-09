import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textual.app import App
from textual.widgets import Button, DataTable, Input, Static, Switch

from tests.fakes import FakeSystem
from vmmanager import tomlio
from vmmanager.errors import VmmError
from vmmanager.health import Health
from vmmanager.layout import Layout
from vmmanager.ops import VmSpec
from vmmanager.system import Result
from vmmanager.ui.app import VmmApp
from vmmanager.ui.dialogs import ConfirmScreen, HealthScreen, MessageScreen
from vmmanager.ui.forms import VmFormScreen

FIX = Path(__file__).parent / "fixtures"
HEALTHY = Health(socket_ok=True, vmd_running=True, firmware=True, doas="ok",
                 config_error=None, broken=[], net=None)
RUNNING = (
    "   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER    STATE NAME\n"
    "    1  100     1    1.0G    1.0G   ttyp0    kixz:kixz  running web\n"
)
WEB = 'vm "web" {\n  memory 1G\n  cpus 1\n  disable\n  disk "/x/web.qcow2"\n  local interface\n  owner kixz\n}\n'


class UiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.sys = FakeSystem(root)
        self.layout = Layout(etc=root / "etc", home=root / "home/u", user="kixz")
        self.layout.etc.mkdir(parents=True)
        self.status((FIX / "status_stopped.txt").read_text())
        self.layout.vm_conf.write_text((FIX / "vm.conf.box").read_text())
        self.health = HEALTHY
        self.patch("vmmanager.health.check", side_effect=lambda *a: self.health)
        self.offer_man = self.patch("vmmanager.manpages.should_offer", return_value=False)

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)  # unittest's debug mode logs every slow Textual callback

    def patch(self, target, **kw):
        p = mock.patch(target, **kw)
        self.addCleanup(p.stop)
        return p.start()

    def status(self, text):
        self.sys.responses[("vmctl", "status")] = Result(0, text, "")

    def managed(self, name="web", text=WEB):
        conf = self.layout.system_conf(name)
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(text)

    async def until(self, pilot, cond):
        for _ in range(200):
            if cond():
                return
            await pilot.pause()
        self.fail("condition never became true")

    async def screen(self, pilot, kind):
        # is_mounted: the screen's widgets are composed too (3.11 schedules this later than 3.14)
        await self.until(pilot, lambda: isinstance(pilot.app.screen, kind) and pilot.app.screen.is_mounted)
        return pilot.app.screen

    async def started(self, pilot):
        await self.until(pilot, lambda: pilot.app.doas is not None and not pilot.app.workers)

    def text(self, widget):
        return str(widget.render())

    async def test_lists_box_vms_dimmed_with_migrate_reason(self):
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            table = app.query_one(DataTable)
            self.assertEqual(table.row_count, 3)
            self.assertEqual([str(c) for c in table.get_row_at(0)],
                             ["debian", "stopped", "4", "8G", "on", "kixz"])
            self.assertTrue(all(c.style == "dim" for c in table.get_row_at(0)))
            self.assertEqual(self.text(app.query_one("#status", Static)),
                             "not managed by vmmanager — press m to migrate")

    async def test_refresh_tick_during_shutdown_does_not_crash(self):
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
        app.refresh_list()

    async def test_create_flow_calls_create_vm(self):
        create = self.patch("vmmanager.ops.create_vm")
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("n")
            form = await self.screen(pilot, VmFormScreen)
            form.query_one("#name", Input).value = "web"
            form.query_one("#memory", Input).value = "2G"
            form.query_one("#iso", Input).value = "/home/u/My ISOs/debian 12.iso"
            form.query_one("#submit", Button).press()
            confirm = await self.screen(pilot, ConfirmScreen)
            self.assertIn("needs doas", confirm.detail)
            self.assertIn(str(self.layout.system_conf("web")), confirm.detail)
            await pilot.click("#yes")
            net = await self.screen(pilot, ConfirmScreen)  # network not set up in the fake
            self.assertIsNot(net, confirm)
            self.assertIn("pf.conf", net.detail)
            await pilot.click("#no")
            await self.until(pilot, lambda: not app.workers)
        create.assert_called_once_with(self.sys, self.layout, VmSpec(
            "web", "2G", 1, "20G", "/home/u/My ISOs/debian 12.iso", True, "", ""))

    async def test_create_form_shows_errors_inline_and_does_not_submit(self):
        create = self.patch("vmmanager.ops.create_vm")
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("n")
            form = await self.screen(pilot, VmFormScreen)
            form.query_one("#name", Input).value = "9bad"
            form.query_one("#memory", Input).value = "lots"
            form.query_one("#iso", Input).value = '/a"b.iso'
            form.query_one("#iso", Input).focus()
            await pilot.press("enter")
            await pilot.pause()
            self.assertIs(app.screen, form)
            errors = self.text(form.query_one("#errors", Static))
            for word in ("name", "memory", "vm.conf value"):
                self.assertIn(word, errors)
        create.assert_not_called()

    async def test_start_disabled_for_unmanaged_notifies_reason(self):
        start = self.patch("vmmanager.ops.start_vm")
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            with mock.patch.object(app, "notify") as notify:
                await pilot.press("s")
                await pilot.pause()
            notify.assert_called_once()
            self.assertEqual(notify.call_args.args[0], "not managed by vmmanager — press m to migrate")
        start.assert_not_called()

    async def test_migrate_on_managed_vm_says_already_managed(self):
        self.status("")
        self.managed()
        migrate = self.patch("vmmanager.migrate.migrate_vm")
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            with mock.patch.object(app, "notify") as notify:
                await pilot.press("m")
                await pilot.pause()
            self.assertEqual(notify.call_args.args[0], "web is already managed by vmmanager")
        migrate.assert_not_called()

    async def test_root_actions_need_doas(self):
        self.health = Health(True, True, True, "denied", None, [], None)
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            with mock.patch.object(app, "notify") as notify:
                await pilot.press("n")
                await pilot.pause()
            self.assertEqual(notify.call_args.args[0], "needs doas — ask an admin")
            self.assertNotIsInstance(app.screen, VmFormScreen)

    async def test_vmm_error_shows_message_screen(self):
        self.patch("vmmanager.migrate.migrate_vm",
                   side_effect=VmmError("Can't migrate debian automatically", "the VM is running"))
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("m")
            confirm = await self.screen(pilot, ConfirmScreen)
            self.assertIn("needs doas", confirm.detail)
            await pilot.click("#yes")
            msg = await self.screen(pilot, MessageScreen)
            self.assertEqual(msg.message, "Can't migrate debian automatically")
            self.assertEqual(msg.detail, "the VM is running")

    async def test_cancelled_doas_prompt_shows_cancelled(self):
        self.status("")
        self.managed()
        self.patch("vmmanager.ops.delete_vm", side_effect=KeyboardInterrupt)
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("d")
            await self.screen(pilot, ConfirmScreen)
            await pilot.click("#yes")
            msg = await self.screen(pilot, MessageScreen)
            self.assertEqual(msg.message, "Cancelled")

    async def test_delete_then_offers_folder_removal(self):
        self.status("")
        self.managed()
        delete = self.patch("vmmanager.ops.delete_vm")
        remove = self.patch("vmmanager.ops.remove_vm_files")
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("d")
            first = await self.screen(pilot, ConfirmScreen)
            self.assertEqual(first.question, "Delete web?")
            await pilot.click("#yes")
            await self.until(pilot, lambda: isinstance(app.screen, ConfirmScreen) and app.screen is not first)
            self.assertEqual(app.screen.question, "Also delete ~/vmmanager/web/ and its disk?")
            await pilot.click("#yes")
            await self.until(pilot, lambda: not app.workers)
        delete.assert_called_once_with(self.sys, self.layout, "web")
        remove.assert_called_once_with(self.layout, "web")

    async def test_update_sends_only_changed_keys_and_shows_kept_lines(self):
        self.status("")
        self.managed()
        update = self.patch("vmmanager.ops.update_vm")
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("e")
            form = await self.screen(pilot, VmFormScreen)
            self.assertEqual(form.query_one("#memory", Input).value, "1G")
            kept = self.text(form.query_one("#kept", Static))
            self.assertEqual(kept.split("\n"), ["disable", 'disk "/x/web.qcow2"', "owner kixz"])
            form.query_one("#memory", Input).value = "2G"
            form.query_one("#submit", Button).press()
            await self.screen(pilot, ConfirmScreen)
            await pilot.click("#yes")
            await self.until(pilot, lambda: not app.workers)
        update.assert_called_once_with(self.sys, self.layout, "web", {"memory": "2G"})

    async def test_stop_offers_force_after_two_refreshes(self):
        self.status(RUNNING)
        self.managed()
        stop = self.patch("vmmanager.ops.stop_vm")
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("x")
            await self.until(pilot, lambda: not app.workers)
            stop.assert_called_once()
            app.refresh_list()  # refreshes after other actions don't count
            app.refresh_list()
            app.refresh_tick()
            await pilot.pause()
            self.assertNotIsInstance(app.screen, ConfirmScreen)
            app.refresh_tick()
            confirm = await self.screen(pilot, ConfirmScreen)
            self.assertEqual(confirm.question, "web is still running. Force stop?")
            await pilot.click("#yes")
            await self.until(pilot, lambda: stop.call_count == 2)
        self.assertEqual(stop.call_args.args[2:], (True,))

    async def test_blocking_health_shows_quit_only(self):
        self.health = Health(False, True, False, "ok", None, [], None)
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            screen = await self.screen(pilot, HealthScreen)
            self.assertIn("socket owner", self.text(screen.query_one("#problems", Static)))
            self.assertEqual([b.id for b in screen.query(Button)], ["quit"])
            await pilot.click("#quit")
            await self.until(pilot, lambda: not app.is_running)

    async def test_socket_missing_with_vmd_stopped_offers_enable_vmd(self):
        self.health = Health(False, False, True, "ok", None, [], None)
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            screen = await self.screen(pilot, HealthScreen)
            self.assertEqual([str(b.label) for b in screen.query(Button)], ["Enable vmd", "Continue"])

    async def test_suspend_resumes_when_body_raises(self):
        events = []

        @contextlib.contextmanager
        def suspend(app):  # like Textual's App.suspend: no try/finally around yield
            events.append("enter")
            yield
            events.append("exit")

        p = mock.patch.object(App, "suspend", suspend)
        p.start()
        self.addCleanup(p.stop)
        self.status("")
        self.managed()
        app = VmmApp(self.sys, self.layout)

        def delete(*a):
            with app.sys.suspend():  # Ctrl-C at the doas prompt
                raise KeyboardInterrupt
        self.patch("vmmanager.ops.delete_vm", side_effect=delete)
        async with app.run_test() as pilot:
            await self.started(pilot)
            self.assertEqual(self.sys.suspend, app._suspend)
            await pilot.press("d")
            await self.screen(pilot, ConfirmScreen)
            await pilot.click("#yes")
            msg = await self.screen(pilot, MessageScreen)
            self.assertEqual(msg.message, "Cancelled")
        self.assertEqual(events, ["enter", "exit"])

    async def test_unexpected_error_shows_message_and_app_keeps_running(self):
        self.status("")
        self.managed()
        self.patch("vmmanager.ops.start_vm", side_effect=ValueError("boom"))
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("s")
            msg = await self.screen(pilot, MessageScreen)
            self.assertEqual((msg.message, msg.detail), ("Unexpected error", "ValueError: boom"))
            self.assertTrue(app.is_running)

    async def test_man_page_offer_error_is_shown(self):
        self.offer_man.side_effect = ValueError("bad state.toml")
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            msg = await self.screen(pilot, MessageScreen)
            self.assertEqual(msg.message, "Unexpected error")

    async def test_root_action_waits_for_permission_check(self):
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            app.doas = None
            with mock.patch.object(app, "notify") as notify:
                await pilot.press("n")
                await pilot.pause()
            self.assertEqual(notify.call_args.args[0], "Still checking permissions — try again in a moment")
            self.assertNotIsInstance(app.screen, VmFormScreen)

    async def test_internet_switch_disabled_with_other_interfaces(self):
        self.status("")
        self.managed(text=WEB.replace("  local interface\n", "  interface tap0\n"))
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("e")
            form = await self.screen(pilot, VmFormScreen)
            self.assertTrue(form.query_one("#internet", Switch).disabled)

    async def test_create_offers_run_now_and_reports_console_exit_status(self):
        self.status("")
        self.patch("vmmanager.ops.create_vm", side_effect=lambda *a: self.managed())
        run = self.patch("vmmanager.ops.run_vm", return_value=1)
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("n")
            form = await self.screen(pilot, VmFormScreen)
            form.query_one("#name", Input).value = "web"
            form.query_one("#internet", Switch).value = False
            form.query_one("#submit", Button).press()
            first = await self.screen(pilot, ConfirmScreen)
            await pilot.click("#yes")
            await self.until(pilot, lambda: isinstance(app.screen, ConfirmScreen) and app.screen is not first)
            self.assertEqual(app.screen.question, "Run now (attach console to install)?")
            await pilot.click("#yes")
            msg = await self.screen(pilot, MessageScreen)
            self.assertEqual(msg.message, "vmctl exited with status 1")
        run.assert_called_once()

    async def test_health_offers_remove_only_for_own_broken_vm(self):
        self.status("")
        self.managed("mine")
        self.health = Health(True, True, True, "ok", "vmd: bad", ["mine", "bobs"], None)
        remove = self.patch("vmmanager.health.remove_broken")
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            screen = await self.screen(pilot, HealthScreen)
            labels = [str(b.label) for b in screen.query(Button)]
            self.assertEqual(labels, ["Remove mine config", "Continue"])
            self.assertIn("bobs", self.text(screen.query_one("#problems", Static)))
            await pilot.click("#fix-0")
            confirm = await self.screen(pilot, ConfirmScreen)
            self.assertIn("needs doas", confirm.detail)
            self.health = HEALTHY
            await pilot.click("#yes")
            await self.until(pilot, lambda: not app.workers)
        remove.assert_called_once_with(self.sys, self.layout, "mine")

    async def test_man_page_offer_records_decline(self):
        self.offer_man.return_value = True
        app = VmmApp(self.sys, self.layout)
        async with app.run_test() as pilot:
            confirm = await self.screen(pilot, ConfirmScreen)
            self.assertEqual(confirm.question, "Install man pages system-wide? (needs doas)")
            await pilot.click("#no")
            await self.until(pilot, lambda: not app.workers)
        self.assertTrue(tomlio.load(self.layout.state_file)["man_declined"])


if __name__ == "__main__":
    unittest.main()
