import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vmmanager.sync_exports import MARK, exports_with, main, parse_ids, sync, valid_folder, wanted_lines

STATUS = """\
   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER    STATE NAME
    1 41552     4    8.0G    8.0G   ttyp2    kixz:kixz  running debian
    2     -     4    8.0G       -       -    kixz:kixz  stopped debian-12
"""


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.etc = self.tmp / "etc"
        (self.etc / "kixz").mkdir(parents=True)

    def vm(self, name, folder, internet=True):
        conf = 'vm "%s" {\n' % name + ("  local interface\n" if internet else "") + "}\n"
        (self.etc / "kixz" / (name + ".conf")).write_text(conf)
        (self.etc / "kixz" / (name + ".share")).write_text(folder + "\n")


class WantedLinesTests(Base):
    def test_valid_folder_refuses_the_whole_filesystem(self):
        for path in ("/", "//", "/.", "/a/.."):
            self.assertFalse(valid_folder(path), path)
        self.assertTrue(valid_folder("/a/b"))

    def test_line_per_shared_vm_with_internet(self):
        share = self.tmp / "share"
        share.mkdir()
        self.vm("debian-12", str(share))
        lines, problems = wanted_lines(self.etc, {"debian-12": 2})
        self.assertEqual(lines, [MARK + " (debian-12)", f"{share} -mapall=kixz 100.64.2.3"])
        self.assertEqual(problems, [])

    def test_no_line_without_local_interface(self):
        self.vm("debian-12", str(self.tmp), internet=False)
        self.assertEqual(wanted_lines(self.etc, {"debian-12": 2}), ([], []))

    def test_no_line_without_id(self):
        self.vm("debian-12", str(self.tmp))
        self.assertEqual(wanted_lines(self.etc, {}), ([], []))

    def test_missing_or_bad_folder_is_a_problem(self):
        self.vm("a", str(self.tmp / "gone"))
        self.vm("b", "/a b")
        self.vm("c", str(self.tmp))
        lines, problems = wanted_lines(self.etc, {"a": 1, "b": 2, "c": 3})
        self.assertEqual(lines, [MARK + " (c)", f"{self.tmp} -mapall=kixz 100.64.3.3"])
        self.assertEqual(len(problems), 2)
        self.assertTrue(problems[0].startswith("a: ") and problems[1].startswith("b: "))

    def test_valid_folder(self):
        for ok in ["/home/kixz/share", "/x"]:
            self.assertTrue(valid_folder(ok))
        for bad in ["rel/path", "/a b", "/a#b", '/a"b', "/a\\b", "/a\tb", ""]:
            self.assertFalse(valid_folder(bad))

    def test_parse_ids(self):
        self.assertEqual(parse_ids(STATUS), {"debian": 1, "debian-12": 2})


class ExportsWithTests(unittest.TestCase):
    def test_exports_with_keeps_other_lines(self):
        theirs = ("/data -ro 10.0.0.5\n# dockerctl: the next line is managed by dockerctl\n"
                  "/home/kixz -mapall=kixz 100.64.6.3")
        old = theirs + "\n" + MARK + " (gone)\n/old -mapall=kixz 100.64.9.3\n"
        new = exports_with(old, [MARK + " (debian-12)", "/home/kixz/share -mapall=kixz 100.64.2.3"])
        self.assertEqual(new, theirs + "\n" + MARK + " (debian-12)\n/home/kixz/share -mapall=kixz 100.64.2.3\n")
        self.assertEqual(exports_with(new, []), theirs + "\n")


class SyncTests(Base):
    def setUp(self):
        super().setUp()
        self.exports = self.tmp / "exports"
        self.lock = self.tmp / "exports.lock"
        self.vm("debian-12", str(self.tmp))

    def test_sync_writes_0644_and_hups_mountd(self):
        self.exports.write_text("/data -ro 10.0.0.5\n")
        with mock.patch("subprocess.run") as run:
            problems = sync(self.etc, self.exports, self.lock, STATUS)
        self.assertEqual(problems, [])
        self.assertEqual(self.exports.stat().st_mode & 0o777, 0o644)
        self.assertIn("100.64.2.3", self.exports.read_text())
        run.assert_called_once_with(["pkill", "-HUP", "-x", "mountd"])

    def test_sync_unchanged_does_not_rewrite_or_hup(self):
        with mock.patch("subprocess.run"):
            sync(self.etc, self.exports, self.lock, STATUS)
        os.utime(self.exports, (1, 1))
        with mock.patch("subprocess.run") as run:
            sync(self.etc, self.exports, self.lock, STATUS)
        run.assert_not_called()
        self.assertEqual(self.exports.stat().st_mtime, 1)


class MainTests(unittest.TestCase):
    def check_no_sync(self, **run_kw):
        with mock.patch("subprocess.run", **run_kw), \
             mock.patch("vmmanager.sync_exports.sync") as sync_:
            self.assertEqual(main(), 1)
        sync_.assert_not_called()

    def test_vmctl_failure_does_not_sync(self):
        self.check_no_sync(return_value=mock.Mock(returncode=1, stdout="", stderr="vmctl: connect failed"))

    def test_missing_vmctl_does_not_sync(self):
        self.check_no_sync(side_effect=FileNotFoundError("vmctl"))


if __name__ == "__main__":
    unittest.main()
