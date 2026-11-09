import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeSystem
from vmmanager.inventory import load_inventory
from vmmanager.layout import Layout
from vmmanager.system import Result

FIX = Path(__file__).parent / "fixtures"
RUNNING = (
    "   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER    STATE NAME\n"
    "    1  100     1    1.0G    1.0G   ttyp0    kixz:kixz  running web\n"
)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sys = FakeSystem(self.root)
        self.layout = Layout(etc=self.root / "etc", home=self.root / "home/u", user="kixz")
        self.layout.etc.mkdir(parents=True)

    def status(self, text):
        self.sys.responses[("vmctl", "status")] = Result(0, text, "")

    def vm_conf(self, text):
        self.layout.vm_conf.write_text(text)

    def managed(self, user, name, text="vm \"%s\" {\n  disable\n  owner %s\n}\n"):
        p = self.layout.etc_dir / user / f"{name}.conf"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text % (name, user) if "%s" in text else text)

    def test_box_vms_are_unmanaged_and_migratable(self):
        self.status((FIX / "status_stopped.txt").read_text())
        self.vm_conf((FIX / "vm.conf.box").read_text())
        entries = load_inventory(self.sys, self.layout)
        self.assertEqual([e.name for e in entries], ["debian", "debian-12", "openbsd"])
        for e in entries:
            self.assertFalse(e.managed)
            self.assertEqual(e.actions, {"migrate"})
        self.assertTrue(entries[0].internet)

    def test_other_users_vm_read_only(self):
        self.status(RUNNING.replace("kixz:kixz", "bob:bob"))
        e = load_inventory(self.sys, self.layout)[0]
        self.assertFalse(e.mine)
        self.assertEqual(e.actions, frozenset())
        self.assertEqual(e.reason, "owned by bob")

    def test_managed_running_actions(self):
        self.status(RUNNING)
        self.managed("kixz", "web")
        e = load_inventory(self.sys, self.layout)[0]
        self.assertTrue(e.managed and e.mine)
        self.assertEqual(e.actions, {"connect", "stop"})
        self.assertIsNone(e.reason)

    def test_managed_conf_without_status_entry(self):
        self.status("")
        self.managed("kixz", "gone")
        e = load_inventory(self.sys, self.layout)[0]
        self.assertEqual(e.state, "not loaded")
        self.assertEqual(e.actions, {"start", "run", "update", "delete"})

    def test_unmanaged_block_in_other_file_has_reason(self):
        self.status(RUNNING)
        e = load_inventory(self.sys, self.layout)[0]
        self.assertEqual(e.actions, frozenset())
        self.assertIn("defined outside /etc/vm.conf", e.reason)

    def test_unreadable_block_is_unmanaged_without_facts(self):
        self.status(RUNNING)
        self.vm_conf('vm "web" {\n  disable\n} vm "x" {\n}\n')
        e = load_inventory(self.sys, self.layout)[0]
        self.assertIsNone(e.facts)
        self.assertEqual(e.actions, frozenset())
        self.assertIn("can't read its vm.conf block", e.reason)


if __name__ == "__main__":
    unittest.main()
