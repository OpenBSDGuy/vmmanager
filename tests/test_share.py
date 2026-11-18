import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fakes import FakeSystem
from vmmanager import share
from vmmanager.errors import VmmError
from vmmanager.layout import Layout
from vmmanager.network import ANCHOR_RULES
from vmmanager.sync_exports import MARK
from vmmanager.system import Result

STATUS = "   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER STATE NAME\n    3  100     1    1.0G    1.0G   ttyp0         kixz running deb\n"


class ShareTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sys = FakeSystem(self.root)
        self.L = Layout(etc=self.root / "etc", home=self.root / "home/u", user="u")
        (self.L.etc_dir / "u").mkdir(parents=True)
        self.folder = self.root / "data"
        self.folder.mkdir()
        self.L.system_conf("deb").write_text('vm "deb" {\n  local interface\n}\n')

    def roots(self):
        return [a for a, r in self.sys.calls if r]

    def test_set_share_writes_file_and_runs_sync_as_root(self):
        share.set_share(self.sys, self.L, "deb", str(self.folder))
        self.assertEqual(self.L.share_file("deb").read_text(), f"{self.folder}\n")
        self.assertIn(([share.PYTHON, str(self.L.sync_script)], True), self.sys.calls)
        self.assertEqual(share.folder(self.L, "deb"), str(self.folder))

    def test_set_share_starts_only_daemons_that_are_not_running_after_sync(self):
        self.sys.responses[("rcctl", "check", "mountd")] = Result(1, "", "")
        self.sys.responses[("rcctl", "check", "nfsd")] = Result(1, "", "")
        share.set_share(self.sys, self.L, "deb", str(self.folder))
        calls = [a for a, _ in self.sys.calls]
        self.assertEqual([a for a in calls if a[:2] == ["rcctl", "start"]],
                         [["rcctl", "start", "mountd"], ["rcctl", "start", "nfsd"]])
        self.assertLess(calls.index([share.PYTHON, str(self.L.sync_script)]), calls.index(["rcctl", "start", "mountd"]))

    def test_unshare_starts_nothing(self):
        self.sys.responses[("rcctl", "check")] = Result(1, "", "")
        share.set_share(self.sys, self.L, "deb", None)
        self.assertFalse(any(a[:2] == ["rcctl", "start"] for a, _ in self.sys.calls))

    def test_set_share_rejects_bad_folder(self):
        for bad in ("/a b", "/a$b", "/a`b", "/a%b", str(self.root / "missing")):
            with self.assertRaises(VmmError) as cm:
                share.set_share(self.sys, self.L, "deb", bad)
            self.assertIn(bad, cm.exception.message)
        self.assertFalse(self.L.share_file("deb").exists())
        self.assertEqual(self.sys.calls, [])

    def test_set_share_rolls_back_when_sync_fails(self):
        self.L.share_file("deb").write_text("/old\n")
        self.sys.responses[(share.PYTHON,)] = Result(1, "", "boom")
        with self.assertRaises(VmmError) as cm:
            share.set_share(self.sys, self.L, "deb", str(self.folder))
        self.assertEqual(cm.exception.detail, "boom")
        self.assertEqual(self.L.share_file("deb").read_text(), "/old\n")

    def test_stop_sharing_deletes_file(self):
        self.L.share_file("deb").write_text("/old\n")
        share.set_share(self.sys, self.L, "deb", None)
        self.assertFalse(self.L.share_file("deb").exists())
        self.assertIsNone(share.folder(self.L, "deb"))
        self.assertIn(([share.PYTHON, str(self.L.sync_script)], True), self.sys.calls)

    def test_sync_noop_without_script(self):
        share.sync(self.sys, self.L)
        self.assertEqual(self.sys.calls, [])

    def test_sync_runs_and_reports_failure(self):
        self.L.sync_script.write_text("x")
        share.sync(self.sys, self.L)
        self.assertEqual(self.roots(), [[share.PYTHON, str(self.L.sync_script)]])
        self.sys.responses[(share.PYTHON,)] = Result(1, "", "boom")
        with self.assertRaises(VmmError):
            share.sync(self.sys, self.L)

    def test_setup_sharing_order(self):
        self.L.etc.mkdir(exist_ok=True)
        self.L.rc_local.write_text("echo hi\n")
        share.setup_sharing(self.sys, self.L)
        line = share.rc_line(self.L)
        self.assertEqual(self.L.sync_script.read_text(), share.bundled_script())
        self.assertEqual(self.sys.modes[self.L.sync_script], 0o755)
        self.assertEqual(self.L.rc_local.read_text(), "echo hi\n\n# added by vmmanager\n" + line + "\n")
        self.assertEqual(self.roots(), [
            ["rcctl", "set", "nfsd", "flags", "-t", "-n", "4"],
            ["rcctl", "enable", "portmap", "mountd", "nfsd"]])  # no start: rc_pre needs a line in /etc/exports

    def test_setup_leaves_existing_rc_line_and_creates_missing_rc_local(self):
        line = share.rc_line(self.L)
        self.L.rc_local.write_text(f"a\n{line}\n")
        share.setup_sharing(self.sys, self.L)
        self.assertEqual(self.L.rc_local.read_text(), f"a\n{line}\n")
        self.L.rc_local.unlink()
        share.setup_sharing(self.sys, self.L)
        self.assertEqual(self.L.rc_local.read_text(), "# added by vmmanager\n" + line + "\n")

    def test_setup_needed_lists_each_missing_piece(self):
        self.sys.responses[("rcctl", "get")] = Result(1, "", "")
        self.assertEqual(len(share.setup_needed(self.sys, self.L)), 4)
        self.L.sync_script.write_text(share.bundled_script())
        self.L.rc_local.write_text(share.rc_line(self.L) + "\n")
        self.L.pf_anchor.write_text(ANCHOR_RULES)
        self.sys.responses[("rcctl", "get")] = Result(0, "", "")
        self.assertEqual(share.setup_needed(self.sys, self.L), [])
        self.sys.responses[("rcctl", "get")] = Result(1, "", "")
        self.assertEqual(len(share.setup_needed(self.sys, self.L)), 1)

    def test_stale_true_when_exports_differ(self):
        self.L.share_file("deb").write_text(f"{self.folder}\n")
        self.assertTrue(share.stale(self.sys, self.L, STATUS))
        self.L.exports.write_text(f"/other\n{MARK} (deb)\n{self.folder} -mapall=u 100.64.3.3\n")
        self.assertFalse(share.stale(self.sys, self.L, STATUS))
        self.L.exports.write_text(f"{MARK} (deb)\n/old -mapall=u 100.64.3.3\n")
        self.assertTrue(share.stale(self.sys, self.L, STATUS))
        self.L.share_file("deb").unlink()
        self.L.exports.write_text("/other\n")
        self.assertFalse(share.stale(self.sys, self.L, STATUS))

    def test_stale_false_when_script_present_and_exports_match(self):
        self.L.sync_script.write_text(share.bundled_script())
        self.L.share_file("deb").write_text(f"{self.folder}\n")
        self.L.exports.write_text(f"{MARK} (deb)\n{self.folder} -mapall=u 100.64.3.3\n")
        self.assertFalse(share.stale(self.sys, self.L, STATUS))

    def test_stale_ignores_other_users_shares_and_unreadable_folders(self):
        other = self.L.etc_dir / "other"
        other.mkdir()
        (other / "vm2.conf").write_text('vm "vm2" {\n  local interface\n}\n')
        (other / "vm2.share").write_text("/home/other/private\n")  # not visible as this user
        status = STATUS + "    4  101     1    1.0G    1.0G   ttyp1        other running vm2\n"
        self.L.exports.write_text(f"{MARK} (vm2)\n/home/other/private -mapall=other 100.64.4.3\n")
        self.assertFalse(share.stale(self.sys, self.L, status))

    def test_stale_false_when_exports_unreadable(self):
        self.L.share_file("deb").write_text(f"{self.folder}\n")
        with mock.patch("vmmanager.share._read_without_root", return_value=None):
            self.assertFalse(share.stale(self.sys, self.L, STATUS))
        self.assertNotIn("cat", [a[0] for a, _ in self.sys.calls])

    def test_guest_commands_text(self):
        t = share.guest_commands("/home/kixz/share")
        self.assertIn('mount -t nfs -o vers=3,proto=tcp,nolock "$(/sbin/ip route | awk \'/default/{print $3}\'):/home/kixz/share" /mnt/share', t)
        self.assertIn('mount_nfs -T "$(/sbin/route -n get default | awk \'/gateway:/{print $2}\'):/home/kixz/share" /mnt/share', t)
        self.assertIn("@reboot for i in 1 2 3 4 5 6; do", t.replace("mkdir -p /mnt/share; ", ""))
        self.assertIn("do /sbin/mount_nfs -T", t)
        self.assertNotIn("FOLDER", t)


if __name__ == "__main__":
    unittest.main()
