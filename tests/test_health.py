import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fakes import FakeSystem
from vmmanager import health
from vmmanager.errors import VmmError
from vmmanager.inventory import VmEntry
from vmmanager.layout import Layout
from vmmanager.system import Result

ERR = "vmd: /etc/vmmanager/u/deb.conf:4: failed to parse disks: /home/u/vmmanager/deb/deb.qcow2\n"


def entry(name, managed=True, mine=True, internet=True):
    facts = mock.Mock(local_interfaces=1 if internet else 0)
    return VmEntry(name, None, "u", managed, mine, facts, Path("/x"))


class HealthTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sys = FakeSystem(self.root)
        self.layout = Layout(etc=self.root / "etc", home=self.root / "home/u", user="u")
        self.layout.etc_dir.mkdir(parents=True)
        (self.layout.etc_dir / "u").mkdir()
        self.sock = self.root / "vmd.sock"
        self.bios = self.root / "vmm-bios"
        for name, val in (("VMD_SOCKET", self.sock), ("VMM_BIOS", self.bios)):
            p = mock.patch.object(health, name, val)
            p.start()
            self.addCleanup(p.stop)

    def test_broken_vm_detected_from_vmd_error(self):
        layout = Layout(etc=Path("/etc"), home=Path("/home/u"), user="u")
        self.assertEqual(health.broken_vms(ERR + "vmd: /etc/vmmanager/u/deb.conf:9: x\n", layout), ["deb"])
        self.assertEqual(health.broken_vms("nothing here", layout), [])

    def test_check_reports_broken_vm(self):
        self.sys.responses[("vmd", "-n")] = Result(1, "", ERR.replace("/etc", str(self.layout.etc)))
        h = health.check(self.sys, self.layout, [])
        self.assertEqual(h.broken, ["deb"])
        self.assertIsNotNone(h.config_error)

    def test_net_not_checked_without_internet_vms(self):
        health.check(self.sys, self.layout, [entry("a", internet=False), entry("b", mine=False)])
        self.assertFalse(any(a[0] == "sysctl" for a, _ in self.sys.calls))
        self.assertIsNone(health.check(self.sys, self.layout, []).net)
        h = health.check(self.sys, self.layout, [entry("a")])
        self.assertIsNotNone(h.net)

    def test_blocking_when_no_socket(self):
        h = health.check(self.sys, self.layout, [])
        self.assertTrue(h.blocking)
        self.assertFalse(h.firmware)
        self.sock.touch()
        self.bios.touch()
        h = health.check(self.sys, self.layout, [])
        self.assertFalse(h.blocking)
        self.assertTrue(h.firmware)

    def test_check_never_raises_and_never_goes_root(self):
        self.sys.run = mock.Mock(side_effect=VmmError("boom"))
        self.sys.doas_state = mock.Mock(side_effect=OSError("x"))
        h = health.check(self.sys, self.layout, [entry("a")])
        self.assertEqual((h.vmd_running, h.doas, h.config_error, h.net), (False, "missing", None, None))

    def test_remove_broken_rewrites_aggregate(self):
        conf = self.layout.system_conf("deb")
        other = self.layout.system_conf("ok")
        conf.write_text("vm deb {}\n")
        other.write_text("vm ok {}\n")
        self.layout.aggregate.write_text(f'include "{conf}"\ninclude "{other}"\n')
        health.remove_broken(self.sys, self.layout, "deb")
        self.assertFalse(conf.exists())
        self.assertNotIn("deb", self.layout.aggregate.read_text())
        self.assertIn(str(other), self.layout.aggregate.read_text())
        self.assertIn((["vmctl", "reload"], True), self.sys.calls)

    def test_remove_broken_skips_reload_when_vmd_down(self):
        conf = self.layout.system_conf("deb")
        conf.write_text("vm deb {}\n")
        self.sys.responses[("rcctl", "check", "vmd")] = Result(1, "", "")
        health.remove_broken(self.sys, self.layout, "deb")
        self.assertNotIn((["vmctl", "reload"], True), self.sys.calls)

    def test_remove_broken_requires_existing_conf(self):
        with self.assertRaises(VmmError):
            health.remove_broken(self.sys, self.layout, "nope")
        with self.assertRaises(VmmError):
            health.remove_broken(self.sys, self.layout, "../x")

    def _two_broken(self):
        a, b = self.layout.system_conf("a"), self.layout.system_conf("b")
        a.write_text("vm a {}\n")
        b.write_text("vm b {}\n")
        self.layout.aggregate.write_text(f'include "{a}"\ninclude "{b}"\n')
        err = "".join(f"vmd: {c}:1: bad\n" for c in (a, b))

        def vmd_n(argv, *, root=False, check=True):
            # fail while b's conf exists; mimics vmd -n naming remaining broken confs
            self.sys.calls.append((list(argv), root))
            left = "".join(f"vmd: {c}:1: bad\n" for c in (a, b) if c.exists())
            return Result(1 if left else 0, "", left) if argv[0] == "vmd" else Result(0, "", "")

        self.sys.run = vmd_n
        return a, b, err

    def test_remove_one_of_two_broken_commits_without_reload(self):
        a, b, _ = self._two_broken()
        health.remove_broken(self.sys, self.layout, "a")
        self.assertFalse(a.exists())
        self.assertTrue(b.exists())
        self.assertNotIn(str(a), self.layout.aggregate.read_text())
        self.assertNotIn((["vmctl", "reload"], True), self.sys.calls)

    def test_remove_broken_rolls_back_when_it_stays_broken(self):
        a, b, err = self._two_broken()
        self.sys.run = lambda argv, *, root=False, check=True: Result(1, "", err) if argv[0] == "vmd" else Result(0, "", "")
        with self.assertRaises(VmmError):
            health.remove_broken(self.sys, self.layout, "b")
        self.assertTrue(b.exists())

    def test_remove_broken_reload_failure_is_reported(self):
        conf = self.layout.system_conf("deb")
        conf.write_text("vm deb {}\n")
        self.sys.responses[("vmctl", "reload")] = Result(1, "", "nope")
        with self.assertRaises(VmmError) as cm:
            health.remove_broken(self.sys, self.layout, "deb")
        self.assertIn("deb was removed but vmd could not reload", cm.exception.message)
        self.assertIn("doas vmctl reload", cm.exception.detail)

    def test_check_makes_no_root_calls(self):
        health.check(self.sys, self.layout, [entry("a")])
        self.assertFalse(any(root for _, root in self.sys.calls))

    def test_enable_vmd_and_fetch_firmware(self):
        health.enable_vmd(self.sys)
        self.assertEqual(self.sys.calls, [(["rcctl", "enable", "vmd"], True), (["rcctl", "start", "vmd"], True)])
        self.sys.calls.clear()
        self.sys.interactive_rc = 3
        self.assertEqual(health.fetch_firmware(self.sys), 3)
        self.assertEqual(self.sys.calls, [(["fw_update", "vmm"], True)])


if __name__ == "__main__":
    unittest.main()
