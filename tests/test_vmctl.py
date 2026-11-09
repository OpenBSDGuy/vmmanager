import unittest
from pathlib import Path

from vmmanager.vmctl import VmStatus, parse_status

FIX = Path(__file__).parent / "fixtures"


class ParseStatusTests(unittest.TestCase):
    def test_parse_status_stopped_fixture(self):
        rows = parse_status((FIX / "status_stopped.txt").read_text())
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            rows[0], VmStatus(1, None, 4, "8.0G", None, None, "kixz:kixz", "stopped", "debian")
        )
        self.assertEqual(rows[0].owner_user, "kixz")

    def test_parse_status_mixed_fixture(self):
        rows = parse_status((FIX / "status_mixed.txt").read_text())
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0].pid, rows[0].tty), (51234, "ttyp3"))
        self.assertEqual(rows[1].owner_user, "root")

    def test_parse_status_empty_header_only(self):
        header = (FIX / "status_stopped.txt").read_text().splitlines()[0]
        self.assertEqual(parse_status(header + "\n"), [])


if __name__ == "__main__":
    unittest.main()
