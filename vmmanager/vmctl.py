"""Wrappers around vmctl and vmd."""

from dataclasses import dataclass
from pathlib import Path

from vmmanager.system import System


@dataclass(frozen=True)
class VmStatus:
    id: int
    pid: int | None
    vcpus: int
    maxmem: str
    curmem: str | None
    tty: str | None
    owner: str
    state: str
    name: str

    @property
    def owner_user(self) -> str:
        return self.owner.split(":")[0]


def _dash(value: str) -> str | None:
    return None if value == "-" else value


def parse_status(text: str) -> list[VmStatus]:
    rows = []
    for line in text.splitlines():
        f = line.split()
        if len(f) != 9 or not f[0].isdigit():
            continue
        pid = _dash(f[1])
        if pid is not None and not pid.isdigit() or not f[2].isdigit():
            continue
        rows.append(
            VmStatus(int(f[0]), int(pid) if pid else None, int(f[2]), f[3],
                     _dash(f[4]), _dash(f[5]), f[6], f[7], f[8])
        )
    return rows


def status(sys: System) -> list[VmStatus]:
    return parse_status(sys.run(["vmctl", "status"]).out)


def check_config(sys: System, path: Path) -> str | None:
    r = sys.run(["vmd", "-n", "-f", str(path)], check=False)
    return None if r.rc == 0 else r.err


def reload(sys: System) -> None:
    sys.run(["vmctl", "reload"], root=True)


def start(sys: System, name: str) -> None:
    sys.run(["vmctl", "start", name])


def stop(sys: System, name: str, force: bool = False) -> None:
    sys.run(["vmctl", "stop", *(["-f"] if force else []), name])


def create_disk(sys: System, path: Path, size: str) -> None:
    sys.run(["vmctl", "create", "-s", size, str(path)])


def run_console(sys: System, name: str) -> int:
    return sys.interactive(["vmctl", "start", "-c", name])


def connect_console(sys: System, name: str) -> int:
    return sys.interactive(["vmctl", "console", name])
