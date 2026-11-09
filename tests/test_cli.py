import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vmmanager import __version__
from vmmanager.cli import main
from vmmanager.errors import VmmError
from vmmanager.layout import Layout


class CliTests(unittest.TestCase):
    def test_version_prints_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--version"]), 0)
        self.assertEqual(out.getvalue(), f"vmmanager {__version__}\n")

    def test_refuses_non_openbsd(self):
        err = io.StringIO()
        with mock.patch("platform.system", return_value="Linux"), \
                contextlib.redirect_stderr(err):
            self.assertEqual(main([]), 1)
        self.assertIn("vmmanager only runs on OpenBSD", err.getvalue())

    def test_man_flag_calls_view(self):
        with mock.patch("vmmanager.manpages.view", return_value=0) as view, \
                mock.patch("platform.system", return_value="Linux"):
            self.assertEqual(main(["--man"]), 0)
        view.assert_called_once()

    def test_install_man_refuses_non_openbsd(self):
        err = io.StringIO()
        with mock.patch("platform.system", return_value="Linux"), \
                mock.patch("vmmanager.manpages.install") as install, \
                contextlib.redirect_stderr(err):
            self.assertEqual(main(["--install-man"]), 1)
        install.assert_not_called()

    def test_install_man_reports_error(self):
        err = io.StringIO()
        with mock.patch("platform.system", return_value="OpenBSD"), \
                mock.patch("vmmanager.manpages.install", side_effect=VmmError("boom", "why")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(main(["--install-man"]), 1)
        self.assertIn("boom", err.getvalue())
        self.assertIn("why", err.getvalue())

    def test_man_flag_reports_error(self):
        err = io.StringIO()
        with mock.patch("vmmanager.manpages.view", side_effect=VmmError("no man", "detail")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(main(["--man"]), 1)
        self.assertIn("no man", err.getvalue())
        self.assertIn("detail", err.getvalue())

    def test_install_man_success_without_user_dir(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as home, \
                mock.patch("platform.system", return_value="OpenBSD"), \
                mock.patch("vmmanager.cli.Layout", return_value=Layout(home=Path(home), user="u")), \
                mock.patch("vmmanager.manpages.install"), \
                contextlib.redirect_stdout(out):
            self.assertEqual(main(["--install-man"]), 0)
            self.assertTrue((Path(home) / "vmmanager/state.toml").exists())
        self.assertIn("Installed man pages", out.getvalue())


if __name__ == "__main__":
    unittest.main()
