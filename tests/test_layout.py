import unittest
from pathlib import Path

from vmmanager.errors import VmmError
from vmmanager.layout import Layout, quote, validate_name


class LayoutTests(unittest.TestCase):
    def test_paths_default_layout(self):
        lay = Layout(etc=Path("/etc"), home=Path("/home/u"), user="u")
        self.assertEqual(lay.system_conf("deb"), Path("/etc/vmmanager/u/deb.conf"))
        self.assertEqual(lay.disk("deb"), Path("/home/u/vmmanager/deb/deb.qcow2"))
        self.assertEqual(lay.include_line, 'include "/etc/vmmanager/vmmanager.conf"')

    def test_validate_name_accepts(self):
        for name in ("deb-12", "a", "x.y_z"):
            validate_name(name)

    def test_validate_name_rejects(self):
        for name in ("", "1abc", "a b", "a/b", "-x"):
            with self.assertRaises(VmmError, msg=name):
                validate_name(name)

    def test_quote_plain_and_spaces(self):
        self.assertEqual(quote("/a b/c.iso"), '"/a b/c.iso"')

    def test_quote_rejects(self):
        for value, shown in (('a"b', '"'), ("a\\b", "\\"), ("a\nb", "newline")):
            with self.assertRaises(VmmError) as cm:
                quote(value)
            self.assertIn(shown, cm.exception.message)


if __name__ == "__main__":
    unittest.main()
