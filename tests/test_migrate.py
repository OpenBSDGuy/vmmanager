import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fakes import FakeSystem
from vmmanager import confblock, migrate, tomlio
from vmmanager.errors import VmmError
from vmmanager.inventory import load_inventory
from vmmanager.layout import Layout
from vmmanager.system import Result

FIX = Path(__file__).parent / "fixtures"
HEAD = "   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER    STATE NAME\n"


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sys = FakeSystem(self.root)
        self.layout = Layout(user="kixz", home=self.root / "home/u", etc=self.root / "etc")
        self.layout.etc.mkdir(parents=True)
        self.old = self.root / "home/u/VM"
        self.old.mkdir(parents=True)
        self.set_conf((FIX / "vm.conf.box").read_text().replace("/home/kixz/VM", str(self.old)))
        for n in ("deb_11", "debian_12", "openbsd"):
            (self.old / f"{n}.qcow2").write_bytes(b"data-" + n.encode())

    def set_conf(self, text):
        self.layout.vm_conf.write_text(text)

    def entry(self, name="debian", status=""):
        status = status or (FIX / "status_stopped.txt").read_text()
        self.sys.responses[("vmctl", "status")] = Result(0, HEAD + status, "")
        return next(e for e in load_inventory(self.sys, self.layout) if e.name == name)

    def blockers(self, name="debian", status=""):
        return migrate.migration_blockers(self.sys, self.layout, self.entry(name, status))

    def test_box_debian_migrates(self):
        before = self.layout.vm_conf.read_text()
        migrate.migrate_vm(self.sys, self.layout, self.entry())
        new = self.layout.vm_dir("debian") / "deb_11.qcow2"
        self.assertEqual(new.read_bytes(), b"data-deb_11")
        self.assertFalse((self.old / "deb_11.qcow2").exists())
        conf = self.layout.system_conf("debian", "kixz").read_text()
        self.assertIn(str(new), conf)
        self.assertIn("owner kixz", conf)
        text = self.layout.vm_conf.read_text()
        self.assertRegex(text, r'# moved to vmmanager \d{4}-\d\d-\d\d: vm "debian" \{')
        self.assertIn(self.layout.include_line, text)
        for n in ("debian-12", "openbsd"):
            span = confblock.find_block(before, n)
            self.assertIn(before[span[0]:span[1]], text)
        self.assertIn(f'include "{self.layout.system_conf("debian")}"', self.layout.aggregate.read_text())
        extras = tomlio.load(self.layout.extras("debian"))
        self.assertEqual(extras["migrated_from"], str(self.old / "deb_11.qcow2"))
        self.assertIn((["vmctl", "reload"], True), self.sys.calls)

    def test_each_blocker(self):
        deb = self.old / "deb_11.qcow2"
        self.assertEqual(self.blockers(), [])
        self.assertEqual(self.blockers(status="    1  100     1    1.0G    1.0G   ttyp0    kixz:kixz  running debian\n"),
                         ["the VM is running or paused"])
        self.assertEqual(self.blockers("debian-12"), [])
        text = self.layout.vm_conf.read_text()
        with self.subTest("not found"):
            e = self.entry()
            self.set_conf(text.replace('vm "debian"', 'vm "other"'))
            self.assertEqual(migrate.migration_blockers(self.sys, self.layout, e), ["the block isn't found in vm.conf"])
            self.set_conf(text)
        with self.subTest("shared line"):
            e = self.entry()
            self.set_conf(text.replace('vm "debian" {', 'switch "x" { } vm "debian" {'))
            self.assertEqual(migrate.migration_blockers(self.sys, self.layout, e),
                             ["its vm.conf block shares a line with other text"])
            self.set_conf(text)
        with self.subTest("macros"):
            e = self.entry()
            self.set_conf(text.replace("memory 8G\n  disable\n  disk " + str(deb), "memory $m\n  disable\n  disk " + str(deb)))
            self.assertIn("the block uses macros", migrate.migration_blockers(self.sys, self.layout, e))
            self.set_conf(text)
        with self.subTest("instance"):
            e = self.entry()
            self.set_conf(text.replace('vm "debian" {', 'vm "tpl" instance "debian" {'))
            self.assertIn("the block is an instance of a template", migrate.migration_blockers(self.sys, self.layout, e))
            self.set_conf(text)
        with self.subTest("other owner"):
            e = self.entry()
            self.set_conf(text.replace("owner kixz:kixz", "owner bob:bob", 1))
            self.assertIn("the owner is another user", migrate.migration_blockers(self.sys, self.layout, e))
            self.set_conf(text)
        with self.subTest("already managed"):
            e = self.entry()
            conf = self.layout.system_conf("debian")
            conf.parent.mkdir(parents=True)
            conf.write_text("x")
            self.assertIn("the name is already managed", migrate.migration_blockers(self.sys, self.layout, e))
            conf.unlink()
        with self.subTest("same basename"):
            e = self.entry()
            (self.old / "sub").mkdir()
            (self.old / "sub/deb_11.qcow2").write_bytes(b"x")
            self.set_conf(text.replace(f"  disk {deb}\n", f"  disk {deb}\n  disk {self.old}/sub/deb_11.qcow2\n", 1))
            self.assertIn("two disks have the same file name", migrate.migration_blockers(self.sys, self.layout, e))
            self.set_conf(text)
        with self.subTest("backing file"):
            deb.write_bytes(b"QFI\xfb" + bytes(4) + struct.pack(">Q", 512) + bytes(8))
            self.assertIn(f"disk {deb} is a qcow2 with a backing file", self.blockers())
            deb.write_bytes(b"data")
        with self.subTest("other filesystem"):
            real = os.stat
            def fake(p, *a, **k):
                r = real(p, *a, **k)
                if str(p) == str(deb):
                    return os.stat_result((*r[:2], r.st_dev + 1, *r[3:]))
                return r
            with mock.patch("vmmanager.migrate.os.stat", fake):
                self.assertIn(f"disk {deb} is on a different filesystem than your home", self.blockers())
        with self.subTest("not writable"):
            with mock.patch("vmmanager.migrate.os.access", return_value=False):
                self.assertIn(f"the folder of disk {deb} is not writable", self.blockers())
        with self.subTest("vm_dir exists"):
            self.layout.vm_dir("debian").mkdir(parents=True)
            self.assertIn(f"the folder {self.layout.vm_dir('debian')} already exists", self.blockers())
            self.layout.vm_dir("debian").rmdir()
        with self.subTest("missing"):
            e = self.entry()
            deb.unlink()
            self.assertEqual(migrate.migration_blockers(self.sys, self.layout, e), [f"disk {deb} is missing"])

    def test_blocked_migrate_raises_with_manual_steps(self):
        e = self.entry()
        (self.old / "deb_11.qcow2").unlink()
        with self.assertRaises(VmmError) as cm:
            migrate.migrate_vm(self.sys, self.layout, e)
        self.assertEqual(cm.exception.message, "Can't migrate debian automatically")
        self.assertIn("is missing", cm.exception.detail)
        self.assertTrue(cm.exception.detail.endswith("See vmmanager(8), MIGRATING."))

    def test_qcow2_backing_detection(self):
        f = self.root / "d.qcow2"
        for head, want in [(b"QFI\xfb" + bytes(4) + bytes(8), False),
                           (b"QFI\xfb" + bytes(4) + struct.pack(">Q", 1), True),
                           (b"XXXX" + bytes(4) + struct.pack(">Q", 1), False),
                           (b"", False)]:
            f.write_bytes(head)
            self.assertIs(migrate.qcow2_has_backing(self.sys, f), want)

    def test_qcow2_backing_via_root_on_permission_error(self):
        self.sys.responses[("od",)] = Result(0, " 51 46 49 fb 00 00 00 03 00 00 00 00 00 00 02 00\n", "")
        with mock.patch("builtins.open", side_effect=PermissionError):
            self.assertTrue(migrate.qcow2_has_backing(self.sys, self.root / "x"))
        self.assertIn((["od", "-An", "-tx1", "-N16", str(self.root / "x")], True), self.sys.calls)

    def test_rollback_moves_disks_back_when_check_fails(self):
        before = self.layout.vm_conf.read_bytes()
        e = self.entry()
        # clean pre-check, failing post-install check
        self.sys.responses[("vmd", "-n")] = Result(0, "", "")
        results = iter([Result(0, "", ""), Result(1, "", "vmd: bad config")])
        orig = self.sys.run
        def run(argv, **k):
            if argv[:2] == ["vmd", "-n"]:
                self.sys.calls.append((list(argv), False))
                return next(results)
            return orig(argv, **k)
        self.sys.run = run
        with self.assertRaises(VmmError) as cm:
            migrate.migrate_vm(self.sys, self.layout, e)
        self.assertIn("vmd: bad config", cm.exception.detail)
        self.assertEqual((self.old / "deb_11.qcow2").read_bytes(), b"data-deb_11")
        self.assertFalse(self.layout.vm_dir("debian").exists())
        self.assertEqual(self.layout.vm_conf.read_bytes(), before)

    def test_rollback_failure_names_where_the_disk_is(self):
        e = self.entry()
        real = os.rename
        def rename(a, b):
            if str(b).endswith("deb_11.qcow2") and str(a).startswith(str(self.layout.user_dir)):
                raise OSError(13, "Permission denied")
            real(a, b)
        with mock.patch.object(self.sys, "install_file", side_effect=RuntimeError("boom")), \
                mock.patch("vmmanager.migrate.os.rename", rename):
            with self.assertRaises(VmmError) as cm:
                migrate.migrate_vm(self.sys, self.layout, e)
        self.assertIn(str(self.layout.vm_dir("debian") / "deb_11.qcow2"), cm.exception.detail)

    def test_reload_failure_keeps_migration(self):
        e = self.entry()
        self.sys.responses[("vmctl", "reload")] = Result(1, "", "nope")
        with self.assertRaises(VmmError) as cm:
            migrate.migrate_vm(self.sys, self.layout, e)
        self.assertEqual(cm.exception.message, "debian was migrated but vmd could not reload its configuration")
        self.assertTrue(cm.exception.detail.endswith("Run: doas vmctl reload"))
        self.assertTrue((self.layout.vm_dir("debian") / "deb_11.qcow2").exists())


if __name__ == "__main__":
    unittest.main()
