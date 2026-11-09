import contextlib
import io
import unittest
from unittest import mock

from vmmanager import __version__
from vmmanager.cli import main


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


if __name__ == "__main__":
    unittest.main()
