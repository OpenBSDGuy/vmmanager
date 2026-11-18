"""Host NAT so VMs on the 100.64.0.0/10 switch can reach the internet."""

from dataclasses import dataclass

from vmmanager.errors import VmmError
from vmmanager.layout import Layout
from vmmanager.rootwrite import RootTransaction
from vmmanager.system import System

ANCHOR_RULES = ("match out on egress inet from 100.64.0.0/10 to any nat-to (egress)\n"
                "pass in quick inet proto { udp tcp } from 100.64.0.0/10 to any port domain rdr-to 127.0.0.1 port domain\n"
                "block return in quick on ! lo0 proto { tcp udp } from ! 100.64.0.0/10 to port { sunrpc nfsd }\n")
ANCHOR_LINE = 'anchor "vmmanager"'
FORWARDING = "net.inet.ip.forwarding=1"


def pf_lines(layout: Layout) -> str:
    return f'{ANCHOR_LINE}\nload anchor "vmmanager" from "{layout.pf_anchor}"\n'


@dataclass
class NetStatus:
    anchor_file: bool
    forwarding: bool
    resolver: bool
    anchor_outdated: bool = False
    pf_lines: bool = True  # the anchor lines are in /etc/pf.conf
    sysctl_saved: bool = True  # forwarding is saved in /etc/sysctl.conf

    @property
    def ok(self) -> bool:
        return self.anchor_file and self.forwarding and self.resolver


def setup_explanation(st: NetStatus) -> str:
    bullets = []
    if st.anchor_outdated:
        bullets.append("Update the vmmanager pf rules (keeps NFS off the local network): writes "
                       "/etc/vmmanager/pf.conf and adds the marked lines to /etc/pf.conf if they are missing")
    elif not st.anchor_file:
        bullets.append("Add NAT rules so VMs can reach the internet: writes /etc/vmmanager/pf.conf and adds a marked 3-line block (a comment and 2 lines) to /etc/pf.conf")
    elif not st.pf_lines:
        bullets.append("Add the vmmanager anchor lines to /etc/pf.conf")
    if not st.forwarding:
        bullets.append("Turn on IP forwarding (sysctl net.inet.ip.forwarding=1, saved in /etc/sysctl.conf)")
    elif not st.sysctl_saved:
        bullets.append(f"Add {FORWARDING} to /etc/sysctl.conf (forwarding is on now but not saved)")
    if not st.resolver:
        bullets.append("Enable and start unwind, OpenBSD's built-in DNS resolver, so VMs can look up names")
    return "\n".join("- " + b for b in bullets)


def _forwarding_on(sys: System) -> bool:
    return sys.run(["sysctl", "-n", "net.inet.ip.forwarding"], check=False).out.strip() == "1"


def _resolver_up(sys: System) -> bool:
    return any(sys.run(["rcctl", "check", svc], check=False).rc == 0 for svc in ("unwind", "unbound"))


def anchor_current(sys: System, layout: Layout) -> bool:
    return sys.read_file(layout.pf_anchor) == ANCHOR_RULES


def _forwarding_saved(sys: System, layout: Layout) -> bool:
    text = sys.read_file(layout.sysctl_conf) or ""
    return FORWARDING in (l.strip() for l in text.splitlines() if not l.lstrip().startswith("#"))


def net_status(sys: System, layout: Layout) -> NetStatus:
    return NetStatus(anchor_current(sys, layout), _forwarding_on(sys), _resolver_up(sys),
                     layout.pf_anchor.exists() and not anchor_current(sys, layout),
                     ANCHOR_LINE in (sys.read_file(layout.pf_conf) or "").splitlines(),
                     _forwarding_saved(sys, layout))


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
    if not _forwarding_saved(sys, layout):
        tx = RootTransaction(sys, lambda: None)
        tx.write(layout.sysctl_conf, text + ("" if text.endswith("\n") or not text else "\n") + FORWARDING + "\n")
        tx.commit()

    if not _resolver_up(sys):
        sys.run(["rcctl", "enable", "unwind"], root=True)
        sys.run(["rcctl", "start", "unwind"], root=True)
