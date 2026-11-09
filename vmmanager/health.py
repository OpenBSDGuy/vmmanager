"""Startup health check: is vmd usable, and which managed VMs are broken."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from vmmanager import network, ops, vmctl
from vmmanager.errors import VmmError
from vmmanager.inventory import VmEntry
from vmmanager.layout import Layout, validate_name
from vmmanager.network import NetStatus
from vmmanager.system import System

VMD_SOCKET = Path("/var/run/vmd.sock")
VMM_BIOS = Path("/etc/firmware/vmm-bios")


@dataclass
class Health:
    socket_ok: bool
    vmd_running: bool
    firmware: bool
    doas: str
    config_error: str | None
    broken: list[str]
    net: NetStatus | None

    @property
    def blocking(self) -> bool:
        return not self.socket_ok


def broken_vms(error: str, layout: Layout) -> list[str]:
    pat = re.compile(re.escape(str(layout.etc_dir)) + r"/[^/\s:]+/([^/\s:]+)\.conf")
    return list(dict.fromkeys(pat.findall(error)))


def _safe(fn, default):
    try:
        return fn()
    except (VmmError, OSError):
        return default


def _vmd_running(sys: System) -> bool:
    return sys.run(["rcctl", "check", "vmd"], check=False).rc == 0


def check(sys: System, layout: Layout, entries: list[VmEntry]) -> Health:
    config_error = _safe(lambda: ops.vm_conf_check(sys, layout), None)
    wants_net = any(e.managed and e.mine and e.internet for e in entries)
    return Health(
        socket_ok=os.access(VMD_SOCKET, os.R_OK | os.W_OK),
        vmd_running=_safe(lambda: _vmd_running(sys), False),
        firmware=VMM_BIOS.exists(),
        doas=_safe(sys.doas_state, "missing"),
        config_error=config_error,
        broken=broken_vms(config_error, layout) if config_error else [],
        net=_safe(lambda: network.net_status(sys, layout), None) if wants_net else None,
    )


def remove_broken(sys: System, layout: Layout, name: str) -> None:
    validate_name(name)
    conf = layout.system_conf(name)
    if not conf.exists():
        raise VmmError(f"No config for '{name}' under {layout.etc_dir / layout.user}")
    tx = ops._tx(sys, layout)
    tx.delete(conf)
    tx.write(layout.aggregate, ops.aggregate_text(layout, exclude=conf))
    tx.commit()
    if _vmd_running(sys):
        vmctl.reload(sys)


def enable_vmd(sys: System) -> None:
    sys.run(["rcctl", "enable", "vmd"], root=True)
    sys.run(["rcctl", "start", "vmd"], root=True)


def fetch_firmware(sys: System) -> int:
    return sys.interactive(["fw_update", "vmm"], root=True)
