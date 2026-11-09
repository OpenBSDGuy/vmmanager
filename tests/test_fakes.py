import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeSystem
from vmmanager.errors import VmmError
from vmmanager.system import Result


class FakeSystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fs = FakeSystem(Path(self.tmp.name))

    def test_password_mode_root_run_suspends_once(self):
        self.fs.auth_mode = "password"
        self.fs.run(["vmctl", "reload"], root=True)
        self.assertEqual(self.fs.suspend_count, 1)

    def test_ok_mode_root_run_does_not_suspend(self):
        self.fs.run(["vmctl", "reload"], root=True)
        self.assertEqual(self.fs.suspend_count, 0)

    def test_interactive_suspends(self):
        self.assertEqual(self.fs.interactive(["vmctl", "console", "a"]), 0)
        self.assertEqual(self.fs.suspend_count, 1)

    def test_longest_prefix_wins(self):
        self.fs.responses[("vmctl",)] = Result(0, "short", "")
        self.fs.responses[("vmctl", "status")] = Result(0, "long", "")
        self.assertEqual(self.fs.run(["vmctl", "status"]).out, "long")
        self.assertEqual(self.fs.run(["vmctl", "start", "a"]).out, "short")

    def test_check_raises(self):
        self.fs.responses[("vmd",)] = Result(1, "", "bad")
        with self.assertRaises(VmmError) as cm:
            self.fs.run(["vmd", "-n"])
        self.assertEqual(cm.exception.detail, "bad")

    def test_file_roundtrip_preserves_bytes(self):
        src, dst = Path(self.tmp.name) / "a", Path(self.tmp.name) / "b"
        src.write_bytes(b"a\r\nb\xff\n")
        self.fs.install_file(self.fs.read_file(src), dst, 0o600)
        self.assertEqual(dst.read_bytes(), b"a\r\nb\xff\n")
        self.assertEqual(self.fs.modes[dst], 0o600)


if __name__ == "__main__":
    unittest.main()
