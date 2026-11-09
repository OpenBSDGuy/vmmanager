"""Merge vmctl status, /etc/vm.conf and managed confs into one VM list."""

from dataclasses import dataclass
from pathlib import Path

from vmmanager import confblock, vmctl
from vmmanager.confblock import BlockFacts
from vmmanager.errors import VmmError
from vmmanager.layout import Layout
from vmmanager.system import System
from vmmanager.vmctl import VmStatus


@dataclass
class VmEntry:
    name: str
    status: VmStatus | None
    owner: str
    managed: bool
    mine: bool
    facts: BlockFacts | None
    conf_path: Path | None
    unreadable: bool = False  # vm.conf block shares a line with other tokens

    @property
    def state(self) -> str:
        return self.status.state if self.status else "not loaded"

    @property
    def internet(self) -> bool:
        return self.facts is not None and self.facts.local_interfaces > 0

    @property
    def actions(self) -> frozenset[str]:
        if not self.mine:
            return frozenset()
        if not self.managed:
            return frozenset({"migrate"}) if self.facts else frozenset()
        if self.state == "running":
            return frozenset({"connect", "stop"})
        if self.state == "paused":
            return frozenset({"stop"})
        return frozenset({"start", "run", "update", "delete"})

    @property
    def reason(self) -> str | None:
        if not self.mine:
            return f"owned by {self.owner}"
        if self.managed:
            return None
        if self.unreadable:
            return "can't read its vm.conf block — see vmmanager(8) MIGRATING"
        if self.facts is None:
            return "defined outside /etc/vm.conf — see vmmanager(8) MIGRATING"
        return "not managed by vmmanager — press m to migrate"


def _block_facts(text: str, name: str) -> BlockFacts | None:
    span = confblock.find_block(text, name)
    return confblock.facts(text[span[0]:span[1]]) if span else None


def load_inventory(sys: System, layout: Layout) -> list[VmEntry]:
    statuses = {s.name: s for s in vmctl.status(sys)}
    vm_conf = sys.read_file(layout.vm_conf) or ""
    entries = {}
    for path in layout.etc_dir.glob("*/*.conf"):
        owner, name = path.parent.name, path.stem
        facts = _block_facts(sys.read_file(path) or "", name)
        entries[name] = VmEntry(name, statuses.get(name), owner, True,
                                owner == layout.user, facts, path)
    for name, st in statuses.items():
        if name in entries:
            continue
        unreadable = False
        try:
            facts = _block_facts(vm_conf, name)
        except VmmError:
            facts, unreadable = None, True
        if facts:
            mine = facts.owner is None or facts.owner.split(":")[0] == layout.user
        else:
            mine = st.owner_user == layout.user
        entries[name] = VmEntry(name, st, st.owner_user, False, mine, facts, None, unreadable)
    return sorted(entries.values(), key=lambda e: e.name)
