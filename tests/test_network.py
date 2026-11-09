import os
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeSystem
from vmmanager.errors import VmmError
from vmmanager.layout import Layout
from vmmanager.network import ANCHOR_RULES, net_status, pf_lines, setup_explanation, setup_network
from vmmanager.system import Result

PF = "set skip on lo\npass\n"


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sys = FakeSystem(self.root)
        self.layout = Layout(etc=self.root / "etc", home=self.root / "home/u", user="u")
        self.layout.etc.mkdir(parents=True)
        self.layout.pf_conf.write_text(PF)
        os.chmod(self.layout.pf_conf, 0o600)
        self.sys.responses[("sysctl", "-n")] = Result(0, "0\n", "")
        self.sys.responses[("rcctl", "check")] = Result(1, "", "")

    def argvs(self):
        return [a for a, _ in self.sys.calls]

    def test_status_all_missing(self):
        st = net_status(self.sys, self.layout)
        self.assertEqual((st.anchor_file, st.forwarding, st.resolver), (False, False, False))
        self.assertFalse(st.ok)
        self.assertEqual(setup_explanation(st).count("\n- ") + 1, 3)
        self.assertFalse(any(root for _, root in self.sys.calls))

    def test_setup_appends_pf_lines_once_and_loads(self):
        setup_network(self.sys, self.layout)
        pf = self.layout.pf_conf.read_text()
        self.assertEqual(pf, PF + "\n# added by vmmanager\n" + pf_lines(self.layout))
        self.assertEqual(self.layout.pf_anchor.read_text(), ANCHOR_RULES)
        self.assertEqual(self.sys.modes[self.layout.pf_conf], 0o600)
        n, f = ["pfctl", "-nf", str(self.layout.pf_conf)], ["pfctl", "-f", str(self.layout.pf_conf)]
        self.assertLess(self.argvs().index(n), self.argvs().index(f))
        setup_network(self.sys, self.layout)
        self.assertEqual(self.layout.pf_conf.read_text(), pf)
        self.assertEqual(self.argvs().count(f), 1)

    def test_setup_rolls_back_pf_conf_when_pfctl_n_fails(self):
        self.sys.responses[("pfctl", "-nf")] = Result(1, "", "syntax error")
        with self.assertRaises(VmmError) as cm:
            setup_network(self.sys, self.layout)
        self.assertIn("syntax error", cm.exception.detail)
        self.assertEqual(self.layout.pf_conf.read_text(), PF)
        self.assertFalse(self.layout.pf_anchor.exists())

    def test_setup_rolls_back_when_pfctl_load_fails(self):
        self.sys.responses[("pfctl", "-f")] = Result(1, "", "load failed")
        with self.assertRaises(VmmError) as cm:
            setup_network(self.sys, self.layout)
        self.assertIn("load failed", cm.exception.detail)
        self.assertEqual(self.layout.pf_conf.read_text(), PF)
        self.assertFalse(self.layout.pf_anchor.exists())

    def test_setup_refuses_missing_pf_conf(self):
        self.layout.pf_conf.unlink()
        with self.assertRaises(VmmError) as cm:
            setup_network(self.sys, self.layout)
        self.assertIn("pf.conf not found", cm.exception.message)
        self.assertFalse(self.layout.pf_conf.exists())
        self.assertFalse(self.layout.pf_anchor.exists())

    def test_sysctl_conf_appended_even_when_forwarding_already_on(self):
        self.sys.responses[("sysctl", "-n")] = Result(0, "1\n", "")
        setup_network(self.sys, self.layout)
        self.assertEqual(self.layout.sysctl_conf.read_text(), "net.inet.ip.forwarding=1\n")
        self.assertNotIn(["sysctl", "net.inet.ip.forwarding=1"], self.argvs())

    def test_runtime_forwarding_set_without_touching_saved_sysctl_conf(self):
        self.layout.sysctl_conf.write_text("net.inet.ip.forwarding=1\n")
        setup_network(self.sys, self.layout)
        self.assertEqual(self.layout.sysctl_conf.read_text(), "net.inet.ip.forwarding=1\n")
        self.assertIn(["sysctl", "net.inet.ip.forwarding=1"], self.argvs())

    def test_setup_skips_present_pieces(self):
        self.layout.pf_conf.write_text(PF + pf_lines(self.layout))
        self.layout.pf_anchor.parent.mkdir()
        self.layout.pf_anchor.write_text(ANCHOR_RULES)
        self.layout.sysctl_conf.write_text("net.inet.ip.forwarding=1\n")
        self.sys.responses[("sysctl", "-n")] = Result(0, "1\n", "")
        self.sys.responses[("rcctl", "check", "unwind")] = Result(0, "", "")
        self.assertTrue(net_status(self.sys, self.layout).ok)
        setup_network(self.sys, self.layout)
        self.assertEqual(self.layout.sysctl_conf.read_text(), "net.inet.ip.forwarding=1\n")
        self.assertFalse(any(a[0] in ("pfctl", "rcctl") and a[1] != "check" or a[0] == "sysctl" and "-n" not in a
                             for a in self.argvs()))

    def test_setup_forwarding_and_resolver(self):
        self.layout.sysctl_conf.write_text("#net.inet.ip.forwarding=1\n")
        setup_network(self.sys, self.layout)
        self.assertEqual(self.layout.sysctl_conf.read_text(), "#net.inet.ip.forwarding=1\nnet.inet.ip.forwarding=1\n")
        self.assertIn(["sysctl", "net.inet.ip.forwarding=1"], self.argvs())
        self.assertLess(self.argvs().index(["rcctl", "enable", "unwind"]), self.argvs().index(["rcctl", "start", "unwind"]))

    def test_explanation_lists_only_missing(self):
        self.layout.pf_anchor.parent.mkdir()
        self.layout.pf_anchor.write_text(ANCHOR_RULES)
        self.sys.responses[("sysctl", "-n")] = Result(0, "1\n", "")
        text = setup_explanation(net_status(self.sys, self.layout))
        self.assertIn("unwind", text)
        self.assertEqual(setup_explanation(net_status(self.sys, self.layout)).count("\n"), 0)
        self.assertNotIn("forwarding", text)
        self.assertNotIn("NAT", text)


if __name__ == "__main__":
    unittest.main()
