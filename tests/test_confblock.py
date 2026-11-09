import difflib
import unittest
from pathlib import Path

from vmmanager.confblock import (
    comment_out,
    edit_block,
    ensure_owner,
    facts,
    find_block,
    render_block,
    replace_disks,
)

FIX = Path(__file__).parent / "fixtures"
BOX = (FIX / "vm.conf.box").read_text()
TRICKY = (FIX / "vm.conf.tricky").read_text()


def block(text, name):
    s, e = find_block(text, name)
    return text[s:e]


class FindBlockTests(unittest.TestCase):
    def test_find_block_box_fixture(self):
        b = block(BOX, "debian")
        self.assertTrue(b.startswith('vm "debian" {'))
        self.assertTrue(b.endswith("}\n"))
        self.assertNotIn("debian-12", b)
        self.assertTrue(block(BOX, "debian-12").startswith('vm "debian-12" {'))

    def test_find_block_ignores_comments_and_nested_braces(self):
        self.assertIsNone(find_block(TRICKY, "fake"))
        b = block(TRICKY, "nested")
        self.assertIn('interface { switch "uplink" }', b)
        self.assertTrue(b.endswith("  owner u\n}\n"))
        self.assertNotIn("instance", b)

    def test_find_block_unquoted_name(self):
        self.assertEqual(block(TRICKY, "plain"), "vm plain { memory 1G }\n")

    def test_find_block_missing(self):
        self.assertIsNone(find_block(BOX, "nope"))


class FactsTests(unittest.TestCase):
    def test_facts_box_debian(self):
        f = facts(block(BOX, "debian"))
        self.assertEqual(
            (f.memory, f.cpus, f.disks, f.owner, f.local_interfaces),
            ("8G", 4, ["/home/kixz/VM/deb_11.qcow2"], "kixz:kixz", 1),
        )
        self.assertFalse(f.other_interfaces or f.uses_macros or f.is_instance)

    def test_facts_flags(self):
        self.assertTrue(facts(block(TRICKY, "macro_vm")).uses_macros)
        self.assertTrue(facts(block(TRICKY, "inst")).is_instance)
        self.assertTrue(facts(block(TRICKY, "nested")).other_interfaces)
        self.assertEqual(facts(block(TRICKY, "plain")).memory, "1G")


class RenderTests(unittest.TestCase):
    def test_render_block_exact(self):
        kw = dict(memory="1G", cpus=1, disk=Path("/h/u/vmmanager/deb/deb.qcow2"), owner="u")
        out = render_block("deb", cdrom="/h/u/i.iso", internet=True, **kw)
        self.assertEqual(
            out,
            'vm "deb" {\n  memory 1G\n  cpus 1\n  disable\n  disk "/h/u/vmmanager/deb/deb.qcow2"\n'
            '  cdrom "/h/u/i.iso"\n  local interface\n  owner u\n}\n',
        )
        self.assertNotIn("local interface", render_block("deb", cdrom=None, internet=False, **kw))
        self.assertNotIn("cdrom", render_block("deb", cdrom=None, internet=True, **kw))


class EditTests(unittest.TestCase):
    def test_edit_block_line_level_keeps_unknown_lines(self):
        old = block(TRICKY, "nested")
        new = edit_block(old, {"memory": "2G"})
        diff = [d for d in difflib.ndiff(old.splitlines(), new.splitlines()) if d[0] in "+-"]
        self.assertEqual(diff, ["-   memory 1G", "+   memory 2G"])

    def test_edit_block_internet_toggle_and_cdrom_remove(self):
        deb = block(BOX, "debian")
        off = edit_block(deb, {"internet": False, "cdrom": None})
        self.assertNotIn("local interface", off)
        on = edit_block(off, {"internet": True})
        self.assertIn("  local interface\n  owner kixz:kixz\n", on)
        self.assertEqual(edit_block(deb, {"internet": True}), deb)

    def test_edit_block_adds_missing_cpus(self):
        new = edit_block(block(BOX, "openbsd"), {"cpus": 2})
        self.assertIn("  memory 8G\n  cpus 2\n  disable\n", new)

    def test_edit_block_refuses_one_liner(self):
        from vmmanager.errors import VmmError

        with self.assertRaises(VmmError):
            edit_block("vm plain { memory 1G }\n", {"memory": "2G"})


class OtherTests(unittest.TestCase):
    def test_comment_out_prefixes_every_line(self):
        s, e = find_block(BOX, "debian")
        out = comment_out(BOX, (s, e), "# moved: ")
        self.assertEqual(out[:s], BOX[:s])
        self.assertEqual(out[len(out) - (len(BOX) - e):], BOX[e:])
        for line in out[s : len(out) - (len(BOX) - e)].splitlines():
            self.assertTrue(line.startswith("# moved: "))

    def test_replace_disks_and_ensure_owner(self):
        deb = block(BOX, "debian")
        new = replace_disks(deb, {"/home/kixz/VM/deb_11.qcow2": "/x y/d.qcow2"})
        self.assertIn('  disk "/x y/d.qcow2"\n', new)
        self.assertEqual(ensure_owner(deb, "bob"), deb)
        self.assertTrue(ensure_owner("vm \"a\" {\n  memory 1G\n}\n", "bob").endswith("  owner bob\n}\n"))


if __name__ == "__main__":
    unittest.main()
