import os
import pwd
import stat
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tests.fakes import FakeSystem
from vmmanager import __version__, confblock, ops, tomlio
from vmmanager.errors import VmmError
from vmmanager.inventory import VmEntry
from vmmanager.layout import Layout
from vmmanager.ops import VmSpec
from vmmanager.system import Result

VM_CONF = '# my vm.conf\nswitch "local" {\n  interface bridge0\n}\n'
STATUS_HEAD = "   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER    STATE NAME\n"


class OpsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sys = FakeSystem(self.root)
        self.layout = Layout(etc=self.root / "etc", home=self.root / "home/u", user="u")
        self.layout.etc.mkdir(parents=True)
        self.layout.vm_conf.write_text(VM_CONF)
        self.layout.user_dir.mkdir(parents=True)

    def status(self, *rows):
        self.sys.responses[("vmctl", "status")] = Result(0, STATUS_HEAD + "".join(rows), "")

    def snapshot(self):
        return {p: p.read_bytes() for p in self.layout.etc.rglob("*") if p.is_file()}

    def commands(self):
        return [a for a, _ in self.sys.calls]

    def test_create_writes_all_files(self):
        ops.create_vm(self.sys, self.layout, VmSpec("deb", iso="/isos/d.iso", os_label="Debian", notes="hi"))
        conf = self.layout.system_conf("deb").read_text()
        expect = confblock.render_block("deb", memory="1G", cpus=1, disk=self.layout.disk("deb"),
                                        cdrom="/isos/d.iso", internet=True, owner="u")
        self.assertEqual(conf, expect)
        self.assertIn(f'include "{self.layout.etc}/vmmanager/u/deb.conf"', self.layout.aggregate.read_text())
        self.assertEqual(self.layout.vm_conf.read_text(),
                         VM_CONF + "\n# added by vmmanager\n" + self.layout.include_line + "\n")
        self.assertIn((["vmctl", "reload"], True), self.sys.calls)
        extras = tomlio.load(self.layout.extras("deb"))
        self.assertEqual(extras["version"], __version__)
        self.assertEqual(extras["iso"], "/isos/d.iso")
        self.assertRegex(extras["created"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")

    def test_create_twice_include_added_once(self):
        ops.create_vm(self.sys, self.layout, VmSpec("a"))
        ops.create_vm(self.sys, self.layout, VmSpec("b"))
        self.assertEqual(self.layout.vm_conf.read_text().count(self.layout.include_line), 1)
        agg = self.layout.aggregate.read_text()
        self.assertIn("a.conf", agg)
        self.assertIn("b.conf", agg)

    def test_create_rejects_taken_name_names_owner(self):
        self.status("    1  100     1    1.0G    1.0G   ttyp0    bob:bob  running web\n")
        with self.assertRaises(VmmError) as cm:
            ops.create_vm(self.sys, self.layout, VmSpec("web"))
        self.assertIn("'web' is already used by bob", cm.exception.message)
        other = self.layout.etc_dir / "amy" / "db.conf"
        other.parent.mkdir(parents=True)
        other.write_text("x")
        with self.assertRaises(VmmError) as cm:
            ops.create_vm(self.sys, self.layout, VmSpec("db"))
        self.assertIn("amy", cm.exception.message)

    def test_create_rejects_iso_path_with_quote(self):
        with self.assertRaises(VmmError):
            ops.create_vm(self.sys, self.layout, VmSpec("x", iso='/a"b.iso'))
        self.assertFalse(self.layout.vm_dir("x").exists())
        self.assertFalse(self.layout.etc_dir.exists())

    def test_create_rolls_back_everything_when_vmd_check_fails(self):
        before = self.snapshot()
        # pass the pre-flight vmd -n, fail the post-install one
        calls = iter([Result(0, "", ""), Result(1, "", "bad iso")])
        orig = self.sys.run

        def run(argv, **kw):
            if argv[:2] == ["vmd", "-n"]:
                self.sys.calls.append((list(argv), False))
                return next(calls)
            return orig(argv, **kw)

        self.sys.run = run
        with self.assertRaises(VmmError) as cm:
            ops.create_vm(self.sys, self.layout, VmSpec("deb"))
        self.assertEqual(cm.exception.detail, "bad iso")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.layout.vm_dir("deb").exists())
        self.assertEqual(set(self.layout.user_dir.iterdir()), set())
        self.assertNotIn(["vmctl", "reload"], self.commands())

    def test_create_keeps_everything_when_reload_fails(self):
        self.sys.responses[("vmctl", "reload")] = Result(1, "", "no vmd")
        with self.assertRaises(VmmError) as cm:
            ops.create_vm(self.sys, self.layout, VmSpec("deb"))
        self.assertEqual(cm.exception.message, "deb was created but vmd could not reload its configuration")
        self.assertEqual(cm.exception.detail, "no vmd\nRun: doas vmctl reload")
        self.assertTrue(self.layout.vm_dir("deb").is_dir())
        self.assertTrue(self.layout.system_conf("deb").is_file())

    def test_create_refuses_existing_folder(self):
        self.layout.vm_dir("deb").mkdir()
        before = self.snapshot()
        with self.assertRaises(VmmError) as cm:
            ops.create_vm(self.sys, self.layout, VmSpec("deb"))
        self.assertIn("already exists", cm.exception.message)
        self.assertEqual(self.snapshot(), before)

    def test_bad_names_rejected(self):
        victim = self.layout.user_dir / "keep"
        victim.write_text("x")
        for bad in ("", "../x"):
            for call in (lambda n: ops.update_vm(self.sys, self.layout, n, {"memory": "2G"}),
                         lambda n: ops.delete_vm(self.sys, self.layout, n),
                         lambda n: ops.remove_vm_files(self.layout, n)):
                with self.assertRaises(VmmError):
                    call(bad)
        self.assertTrue(victim.exists())

    def test_update_and_delete_refuse_when_config_broken(self):
        ops.create_vm(self.sys, self.layout, VmSpec("deb"))
        self.sys.responses[("vmd", "-n")] = Result(1, "", "syntax error")
        before = self.snapshot()
        for call in (lambda: ops.update_vm(self.sys, self.layout, "deb", {"memory": "2G"}),
                     lambda: ops.delete_vm(self.sys, self.layout, "deb")):
            with self.assertRaises(VmmError) as cm:
                call()
            self.assertIn("already has errors", cm.exception.message)
        self.assertEqual(self.snapshot(), before)

    def test_update_saves_extras_even_if_reload_fails(self):
        ops.create_vm(self.sys, self.layout, VmSpec("deb", iso="/a.iso"))
        self.sys.responses[("vmctl", "reload")] = Result(1, "", "no vmd")
        with self.assertRaises(VmmError):
            ops.update_vm(self.sys, self.layout, "deb", {"cdrom": "/b.iso"})
        self.assertEqual(tomlio.load(self.layout.extras("deb"))["iso"], "/b.iso")

    def test_disk_problems_accepts_root_owned_regular_file(self):
        fake = os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, 0, 0, 0, 0, 0, 0))
        with mock.patch("os.lstat", return_value=fake):
            self.assertEqual(ops.disk_problems(self.block('disk "/x"'), os.getuid() + 1), [])

    def test_create_refuses_when_config_already_broken(self):
        self.sys.responses[("vmd", "-n")] = Result(1, "", "line 3: syntax error")
        with self.assertRaises(VmmError) as cm:
            ops.create_vm(self.sys, self.layout, VmSpec("deb"))
        self.assertIn("vm.conf already has errors", cm.exception.message)
        self.assertIn("syntax error", cm.exception.detail)
        self.assertFalse(self.layout.vm_dir("deb").exists())

    def test_update_changes_only_memory_line(self):
        ops.create_vm(self.sys, self.layout, VmSpec("deb"))
        before = self.layout.system_conf("deb").read_text()
        ops.update_vm(self.sys, self.layout, "deb", {"memory": "2G"})
        self.assertEqual(self.layout.system_conf("deb").read_text(), before.replace("memory 1G", "memory 2G"))

    def test_update_cdrom_updates_extras_iso(self):
        ops.create_vm(self.sys, self.layout, VmSpec("deb", iso="/a.iso"))
        ops.update_vm(self.sys, self.layout, "deb", {"cdrom": "/b.iso"})
        self.assertEqual(tomlio.load(self.layout.extras("deb"))["iso"], "/b.iso")

    def test_update_refuses_running_vm(self):
        ops.create_vm(self.sys, self.layout, VmSpec("deb"))
        before = self.snapshot()
        self.status("    1  100     1    1.0G    1.0G   ttyp0    u:u  running deb\n")
        with self.assertRaises(VmmError):
            ops.update_vm(self.sys, self.layout, "deb", {"memory": "2G"})
        self.assertEqual(self.snapshot(), before)

    def test_delete_removes_conf_and_aggregate_line_keeps_folder(self):
        ops.create_vm(self.sys, self.layout, VmSpec("deb"))
        ops.create_vm(self.sys, self.layout, VmSpec("other"))
        ops.delete_vm(self.sys, self.layout, "deb")
        self.assertFalse(self.layout.system_conf("deb").exists())
        agg = self.layout.aggregate.read_text()
        self.assertNotIn("deb.conf", agg)
        self.assertIn("other.conf", agg)
        self.assertTrue(self.layout.vm_dir("deb").is_dir())
        ops.remove_vm_files(self.layout, "deb")
        self.assertFalse(self.layout.vm_dir("deb").exists())

    def block(self, *lines):
        return 'vm "t" {\n' + "".join(f"  {l}\n" for l in lines) + "}\n"

    def test_disk_problems_symlink_device_missing_wrong_owner(self):
        uid = os.getuid()
        good = self.root / "good.qcow2"
        good.write_text("x")
        link = self.root / "link.qcow2"
        link.symlink_to(good)
        self.assertEqual(ops.disk_problems(self.block(f'disk "{good}"'), uid), [])
        self.assertEqual(len(ops.disk_problems(self.block(f'disk "{link}"'), uid)), 1)
        self.assertEqual(len(ops.disk_problems(self.block('disk "/dev/null"'), uid)), 1)
        self.assertEqual(len(ops.disk_problems(self.block(f'disk "{self.root}/nope"'), uid)), 1)
        self.assertEqual(len(ops.disk_problems(self.block(f'cdrom "{self.root}/nope.iso"'), uid)), 1)
        if uid != 0:
            self.assertEqual(len(ops.disk_problems(self.block(f'disk "{good}"'), uid + 1)), 1)

    def entry(self, name="deb"):
        return VmEntry(name, None, pwd.getpwuid(os.getuid()).pw_name, True, True, None, self.layout.system_conf(name))

    def test_start_refuses_on_disk_problem(self):
        ops.create_vm(self.sys, self.layout, VmSpec("deb"))  # disk file was never really created
        with self.assertRaises(VmmError) as cm:
            ops.start_vm(self.sys, self.layout, self.entry())
        self.assertIn("deb.qcow2", cm.exception.detail)
        self.assertNotIn(["vmctl", "start", "deb"], self.commands())

    def test_start_runs_when_disk_ok(self):
        ops.create_vm(self.sys, self.layout, VmSpec("deb"))
        self.layout.disk("deb").write_text("x")
        ops.start_vm(self.sys, self.layout, self.entry())
        self.assertIn(["vmctl", "start", "deb"], self.commands())


if __name__ == "__main__":
    unittest.main()
