import tempfile
import tomllib
import unittest
from pathlib import Path

from vmmanager import tomlio


class TomlioTests(unittest.TestCase):
    def test_roundtrip_through_tomllib(self):
        data = {"notes": 'say "hi" \\ back\nline2 é☃', "version": 2, "man_declined": True}
        self.assertEqual(tomllib.loads(tomlio.dump(data)), data)

    def test_dump_sorts_keys(self):
        self.assertEqual(tomlio.dump({"b": 1, "a": "x"}), 'a = "x"\nb = 1\n')

    def test_load_missing_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(tomlio.load(Path(d) / "nope.toml"), {})

    def test_save_then_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "vmmanager.toml"
            tomlio.save(p, {"iso": "a b.iso", "created": "2026-11-09T00:00:00Z"})
            self.assertEqual(tomlio.load(p)["iso"], "a b.iso")
            self.assertEqual(list(Path(d).iterdir()), [p])
