import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeSystem
from vmmanager.errors import VmmError
from vmmanager.rootwrite import RootTransaction


class RootTransactionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sys = FakeSystem(self.root)
        self.old = self.root / "pf.conf"
        self.old.write_bytes(b"old\r\n\xff")
        self.old.chmod(0o600)
        self.new = self.root / "sub" / "new.conf"

    def test_commit_installs_with_modes(self):
        tx = RootTransaction(self.sys, lambda: None)
        tx.write(self.old, "changed")
        tx.write(self.new, "fresh")
        tx.commit()
        self.assertEqual(self.sys.modes[self.old], 0o600)
        self.assertEqual(self.sys.modes[self.new], 0o644)
        self.assertEqual(self.new.read_text(), "fresh")

    def test_commit_rolls_back_on_check_failure(self):
        before = self.old.read_bytes()
        tx = RootTransaction(self.sys, lambda: "bad line 3")
        tx.write(self.old, "changed")
        tx.write(self.new, "fresh")
        with self.assertRaises(VmmError) as cm:
            tx.commit()
        self.assertEqual(cm.exception.detail, "bad line 3")
        self.assertEqual(self.old.read_bytes(), before)
        self.assertEqual(self.old.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.new.exists())

    def test_install_failure_midway_rolls_back_earlier_files(self):
        before = self.old.read_bytes()
        real = self.sys.install_file

        def flaky(content, dest, mode):
            if dest == self.new:
                raise VmmError("install failed", detail="boom")
            real(content, dest, mode)

        self.sys.install_file = flaky
        tx = RootTransaction(self.sys, lambda: None)
        tx.write(self.old, "changed")
        tx.write(self.new, "fresh")
        with self.assertRaises(VmmError):
            tx.commit()
        self.assertEqual(self.old.read_bytes(), before)

    def test_delete_is_restored_on_failure(self):
        before = self.old.read_bytes()
        tx = RootTransaction(self.sys, lambda: "bad")
        tx.delete(self.old)
        with self.assertRaises(VmmError):
            tx.commit()
        self.assertEqual(self.old.read_bytes(), before)

    def test_restore_errors_reported_and_others_still_restored(self):
        other = self.root / "other.conf"
        other.write_text("o")
        real = self.sys.install_file
        state = {"fail": False}

        def flaky(content, dest, mode):
            # other is staged last, so it is restored first in rollback
            if state["fail"] and dest == other:
                raise OSError("disk gone")
            real(content, dest, mode)

        def check():
            state["fail"] = True
            return "bad"

        before = self.old.read_bytes()
        self.sys.install_file = flaky
        tx = RootTransaction(self.sys, check)
        tx.write(self.old, "changed")
        tx.write(other, "changed")
        with self.assertRaises(VmmError) as cm:
            tx.commit()
        self.assertIn(str(other), cm.exception.detail)
        self.assertIn("bad", cm.exception.detail)
        self.assertEqual(self.old.read_bytes(), before)

    def test_keyboard_interrupt_rolls_back_and_propagates(self):
        before = self.old.read_bytes()
        real = self.sys.install_file

        def interrupted(content, dest, mode):
            if dest == self.new:
                raise KeyboardInterrupt
            real(content, dest, mode)

        self.sys.install_file = interrupted
        tx = RootTransaction(self.sys, lambda: None)
        tx.write(self.old, "changed")
        tx.write(self.new, "fresh")
        with self.assertRaises(KeyboardInterrupt):
            tx.commit()
        self.assertEqual(self.old.read_bytes(), before)

    def test_existing_parent_dir_is_not_reinstalled(self):
        dirs = []
        real = self.sys.install_dir
        self.sys.install_dir = lambda p: (dirs.append(p), real(p))
        tx = RootTransaction(self.sys, lambda: None)
        tx.write(self.old, "again")
        tx.write(self.new, "fresh")
        tx.commit()
        self.assertEqual(dirs, [self.new.parent])
