import difflib
import unittest
from pathlib import Path

from vmmanager.errors import VmmError
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
        with self.assertRaises(VmmError):
            edit_block("vm plain { memory 1G }\n", {"memory": "2G"})


class FixRoundTests(unittest.TestCase):
    def test_edit_refuses_multiline_nested_statement(self):
        b = 'vm "a" {\n  memory 1G\n  local interface {\n    group "g"\n  }\n  owner u\n}\n'
        with self.assertRaises(VmmError) as cm:
            edit_block(b, {"internet": False})
        self.assertIn("a", cm.exception.message)
        with self.assertRaises(VmmError):
            ensure_owner(b, "u")

    def test_memory_and_cpus_validated(self):
        b = block(BOX, "debian")
        for bad in ("1G\n  disk /etc/master.passwd", "G", "1 G", ""):
            with self.assertRaises(VmmError):
                edit_block(b, {"memory": bad})
        for bad in (0, 65, True, "2"):
            with self.assertRaises(VmmError):
                edit_block(b, {"cpus": bad})
        kw = dict(disk=Path("/d"), cdrom=None, internet=True, owner="u")
        with self.assertRaises(VmmError):
            render_block("a", memory="1G\n  x", cpus=1, **kw)
        for bad in (0, 65, True):
            with self.assertRaises(VmmError):
                render_block("a", memory="1G", cpus=bad, **kw)
        self.assertIn("memory 1.5G", edit_block(b, {"memory": "1.5G"}))

    def test_find_block_refuses_shared_lines(self):
        t = 'vm "a" { memory 1G } vm "b" { memory 1G }\n'
        with self.assertRaises(VmmError):
            find_block(t, "a")
        with self.assertRaises(VmmError):
            find_block(t, "b")
        self.assertIsNotNone(find_block('vm "a" { memory 1G } # c\n', "a"))

    def test_duplicate_key_first_wins(self):
        b = 'vm "a" {\n  memory 1G\n  memory 2G\n}\n'
        self.assertEqual(facts(b).memory, "1G")
        self.assertEqual(edit_block(b, {"memory": "4G"}), 'vm "a" {\n  memory 4G\n  memory 2G\n}\n')

    def test_cdrom_remove_replace_insert(self):
        b = 'vm "a" {\n  disk "/d1"\n  cdrom "/x.iso"\n  disk "/d2"\n  owner u\n}\n'
        self.assertNotIn("cdrom", edit_block(b, {"cdrom": None}))
        self.assertIn('  cdrom "/y z.iso"\n', edit_block(b, {"cdrom": "/y z.iso"}))
        self.assertNotIn("/x.iso", edit_block(b, {"cdrom": "/y.iso"}))
        n = 'vm "a" {\n  disk "/d1"\n  disk "/d2"\n  owner u\n}\n'
        self.assertIn('  disk "/d2"\n  cdrom "/i.iso"\n  owner u\n', edit_block(n, {"cdrom": "/i.iso"}))


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
