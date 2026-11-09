"""Host NAT so VMs on the 100.64.0.0/10 switch can reach the internet."""

from dataclasses import dataclass

from vmmanager.errors import VmmError
from vmmanager.layout import Layout
from vmmanager.rootwrite import RootTransaction
from vmmanager.system import System

ANCHOR_RULES = ("match out on egress inet from 100.64.0.0/10 to any nat-to (egress)\n"
                "pass in quick inet proto { udp tcp } from 100.64.0.0/10 to any port domain rdr-to 127.0.0.1 port domain\n")
ANCHOR_LINE = 'anchor "vmmanager"'
FORWARDING = "net.inet.ip.forwarding=1"


def pf_lines(layout: Layout) -> str:
    return f'{ANCHOR_LINE}\nload anchor "vmmanager" from "{layout.pf_anchor}"\n'


@dataclass
class NetStatus:
    anchor_file: bool
    forwarding: bool
    resolver: bool

    @property
    def ok(self) -> bool:
        return self.anchor_file and self.forwarding and self.resolver


def setup_explanation(st: NetStatus) -> str:
    bullets = []
    if not st.anchor_file:
        bullets.append("Add NAT rules so VMs can reach the internet: writes /etc/vmmanager/pf.conf and adds a marked 3-line block (a comment and 2 lines) to /etc/pf.conf")
    if not st.forwarding:
        bullets.append("Turn on IP forwarding (sysctl net.inet.ip.forwarding=1, saved in /etc/sysctl.conf)")
    if not st.resolver:
        bullets.append("Start unwind, OpenBSD's built-in DNS resolver, so VMs can look up names")
    return "\n".join("- " + b for b in bullets)


def _forwarding_on(sys: System) -> bool:
    return sys.run(["sysctl", "-n", "net.inet.ip.forwarding"], check=False).out.strip() == "1"


def _resolver_up(sys: System) -> bool:
    return any(sys.run(["rcctl", "check", svc], check=False).rc == 0 for svc in ("unwind", "unbound"))


def net_status(sys: System, layout: Layout) -> NetStatus:
    return NetStatus(layout.pf_anchor.exists(), _forwarding_on(sys), _resolver_up(sys))


def setup_network(sys: System, layout: Layout) -> None:
    pf_conf = str(layout.pf_conf)
    pf = sys.read_file(layout.pf_conf)
    if pf is None:
        raise VmmError("/etc/pf.conf not found \u2014 refusing to create one", detail="vmmanager only adds lines to an existing pf.conf")
    has_line = ANCHOR_LINE in pf.splitlines()
    if not has_line or sys.read_file(layout.pf_anchor) != ANCHOR_RULES:
        def check():
            for argv in (["pfctl", "-nf", pf_conf], ["pfctl", "-f", pf_conf]):  # load inside the check so a failure rolls back
                r = sys.run(argv, root=True, check=False)
                if r.rc != 0:
                    return r.err
            return None

        tx = RootTransaction(sys, check)
        tx.write(layout.pf_anchor, ANCHOR_RULES)
        if not has_line:
            tx.write(layout.pf_conf, pf + "\n# added by vmmanager\n" + pf_lines(layout))
        tx.commit()

    if not _forwarding_on(sys):
        sys.run(["sysctl", FORWARDING], root=True)
    text = sys.read_file(layout.sysctl_conf) or ""
    if FORWARDING not in (l.strip() for l in text.splitlines() if not l.lstrip().startswith("#")):
        tx = RootTransaction(sys, lambda: None)
        tx.write(layout.sysctl_conf, text + ("" if text.endswith("\n") or not text else "\n") + FORWARDING + "\n")
        tx.commit()

    if not _resolver_up(sys):
        sys.run(["rcctl", "enable", "unwind"], root=True)
        sys.run(["rcctl", "start", "unwind"], root=True)
