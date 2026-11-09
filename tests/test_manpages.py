import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fakes import FakeSystem
from vmmanager import __version__, manpages, tomlio
from vmmanager.layout import Layout


class ManpagesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.layout = Layout(etc=self.root / "etc", home=self.root / "home/u", user="u")
        self.layout.user_dir.mkdir(parents=True)

    def test_bundled_pages_present(self):
        self.assertEqual(sorted(p.name for p in manpages.bundled()), ["vmmanager.8", "vmmanager.toml.5"])

    def test_view_runs_man_l_without_root(self):
        sys = FakeSystem(self.root)
        self.assertEqual(manpages.view(sys), 0)
        argv, root = sys.calls[0]
        self.assertEqual(argv[:2], ["man", "-l"])
        self.assertTrue(argv[2].endswith("vmmanager.8"))
        self.assertFalse(root)

    def test_should_offer_matrix(self):
        l = self.layout
        self.assertTrue(manpages.should_offer(l, "ok"))
        self.assertFalse(manpages.should_offer(l, "denied"))
        self.assertFalse(manpages.should_offer(l, "missing"))
        tomlio.save(l.state_file, {"man_declined": True})
        self.assertFalse(manpages.should_offer(l, "ok"))
        tomlio.save(l.state_file, {"man_installed_version": __version__})
        self.assertFalse(manpages.should_offer(l, "password"))
        tomlio.save(l.state_file, {"man_installed_version": "0.0.0"})
        self.assertTrue(manpages.should_offer(l, "password"))

    def test_record(self):
        manpages.record(self.layout, True)
        self.assertEqual(tomlio.load(self.layout.state_file), {"man_installed_version": __version__})
        manpages.record(self.layout, False)
        self.assertEqual(tomlio.load(self.layout.state_file), {"man_declined": True, "man_installed_version": __version__})

    def test_install_targets_and_makewhatis(self):
        sys = FakeSystem(self.root)
        man = self.root / "man"
        with mock.patch.object(manpages, "MAN_DIR", man):
            manpages.install(sys)
        self.assertEqual(sys.modes[man / "man8/vmmanager.8"], 0o644)
        self.assertEqual(sys.modes[man / "man5/vmmanager.toml.5"], 0o644)
        self.assertIn("Dt VMMANAGER 8", (man / "man8/vmmanager.8").read_text())
        self.assertEqual(sys.calls[-1], (["makewhatis", str(man)], True))

    def test_install_dir_only_when_section_missing(self):
        man = self.root / "man"
        (man / "man8").mkdir(parents=True)
        sys = FakeSystem(self.root)
        with mock.patch.object(manpages, "MAN_DIR", man), \
                mock.patch.object(sys, "install_dir", wraps=sys.install_dir) as install_dir:
            manpages.install(sys)
        install_dir.assert_called_once_with(man / "man5")

    def test_record_creates_user_dir(self):
        layout = Layout(etc=self.root / "etc", home=self.root / "fresh", user="u")
        manpages.record(layout, True)
        self.assertEqual(tomlio.load(layout.state_file), {"man_installed_version": __version__})


if __name__ == "__main__":
    unittest.main()
