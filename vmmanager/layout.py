import getpass
import re
from dataclasses import dataclass, field
from pathlib import Path

from vmmanager.errors import VmmError

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class Layout:
    etc: Path = Path("/etc")
    home: Path = field(default_factory=Path.home)
    user: str = field(default_factory=getpass.getuser)

    @property
    def vm_conf(self) -> Path:
        return self.etc / "vm.conf"

    @property
    def pf_conf(self) -> Path:
        return self.etc / "pf.conf"

    @property
    def sysctl_conf(self) -> Path:
        return self.etc / "sysctl.conf"

    @property
    def etc_dir(self) -> Path:
        return self.etc / "vmmanager"

    @property
    def aggregate(self) -> Path:
        return self.etc_dir / "vmmanager.conf"

    @property
    def pf_anchor(self) -> Path:
        return self.etc_dir / "pf.conf"

    @property
    def user_dir(self) -> Path:
        return self.home / "vmmanager"

    @property
    def state_file(self) -> Path:
        return self.user_dir / "state.toml"

    @property
    def include_line(self) -> str:
        return f'include "{self.aggregate}"'

    def vm_dir(self, name: str) -> Path:
        return self.user_dir / name

    def disk(self, name: str) -> Path:
        return self.vm_dir(name) / f"{name}.qcow2"

    def extras(self, name: str) -> Path:
        return self.vm_dir(name) / "vmmanager.toml"

    def system_conf(self, name: str, user: str | None = None) -> Path:
        return self.etc_dir / (user or self.user) / f"{name}.conf"


def validate_name(name: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise VmmError(
            f"Invalid VM name {name!r}: use letters, digits, '.', '_' or '-', starting with a letter"
        )


def quote(value: str) -> str:
    for ch, label in (('"', '"'), ("\\", "\\"), ("\n", "newline")):
        if ch in value:
            raise VmmError(f"Cannot use {label} in a vm.conf value: {value!r}")
    return f'"{value}"'
