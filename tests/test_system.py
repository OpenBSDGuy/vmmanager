import contextlib
import os
from pathlib import Path
import unittest
from unittest import mock

from vmmanager.errors import VmmError
from vmmanager.system import Result, System


class Scripted(System):
    def __init__(self, *results):
        super().__init__()
        self.results = list(results)
        self.argvs = []
        self.suspended = 0
        self.in_suspend = []

        @contextlib.contextmanager
        def suspend():
            self.suspended += 1
            try:
                yield
            finally:
                self.suspended -= 1

        self.suspend = suspend

    def _spawn(self, argv, capture):
        self.argvs.append(argv)
        self.in_suspend.append(self.suspended > 0)
        return self.results.pop(0)


class SystemTests(unittest.TestCase):
    def test_root_run_uses_doas_n(self):
        s = Scripted(Result(0, "", ""))
        s.run(["vmctl", "reload"], root=True)
        self.assertEqual(s.argvs, [["doas", "-n", "vmctl", "reload"]])

    def test_root_falls_back_to_interactive_when_auth_required(self):
        s = Scripted(Result(1, "", "doas: Authentication required"), Result(0, "ok", ""))
        r = s.run(["vmctl", "reload"], root=True)
        self.assertEqual(s.argvs[1], ["doas", "vmctl", "reload"])
        self.assertEqual(s.in_suspend, [False, True])
        self.assertEqual(r.out, "ok")

    def test_root_failure_raises_with_detail(self):
        s = Scripted(Result(1, "", "boom"))
        with self.assertRaises(VmmError) as cm:
            s.run(["vmctl", "reload"], root=True)
        self.assertEqual(cm.exception.detail, "boom")

    def test_doas_state_maps_messages(self):
        for result, want in (
            (Result(0, "", ""), "ok"),
            (Result(1, "", "doas: Authentication required"), "password"),
            (Result(1, "", "doas: Operation not permitted"), "denied"),
        ):
            with mock.patch("shutil.which", return_value="/usr/bin/doas"):
                self.assertEqual(Scripted(result).doas_state(), want)
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(Scripted().doas_state(), "missing")

    def test_no_retry_when_rc_zero_with_auth_text(self):
        s = Scripted(Result(0, "", "Authentication required"))
        s.run(["x"], root=True)
        self.assertEqual(len(s.argvs), 1)

    def test_missing_binary_raises_vmm_error(self):
        with self.assertRaises(VmmError) as cm:
            System().run(["vmmanager-no-such-binary"])
        self.assertIn("command not found", cm.exception.message)

    def test_install_file_preserves_bytes_and_removes_temp(self):
        seen = {}

        class Copy(System):
            def _spawn(self, argv, capture):
                tmp = argv[-2]
                seen["tmp"] = tmp
                with open(tmp, "rb") as f:
                    seen["data"] = f.read()
                return Result(0, "", "")

        Copy().install_file("a\r\nb\udcff\n", Path("/x"), 0o644)
        self.assertEqual(seen["data"], b"a\r\nb\xff\n")
        self.assertFalse(os.path.exists(seen["tmp"]))

    def test_install_file_removes_temp_when_write_fails(self):
        with mock.patch("tempfile.NamedTemporaryFile") as m:
            fh = m.return_value
            fh.__enter__.return_value = fh
            fh.write.side_effect = OSError("disk full")
            fh.name = "/nonexistent-tmp"
            with mock.patch("os.unlink") as unlink, self.assertRaises(OSError):
                System().install_file("x", Path("/x"), 0o644)
            unlink.assert_called_once_with("/nonexistent-tmp")


if __name__ == "__main__":
    unittest.main()
