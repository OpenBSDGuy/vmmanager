"""Share a host folder with a VM over NFS: one-time setup, share/unshare, stale check, guest commands."""

import importlib.resources
import os

from vmmanager.errors import VmmError
from vmmanager.layout import Layout
from vmmanager.network import _read_without_root, anchor_current
from vmmanager.rootwrite import RootTransaction
from vmmanager.sync_exports import MARK, parse_ids, valid_folder, wanted_lines
from vmmanager.system import System

PYTHON = "/usr/local/bin/python3"
NFS_SERVICES = ["portmap", "mountd", "nfsd"]
MOUNT_LINUX = """mount -t nfs -o vers=3,proto=tcp,nolock "$(/sbin/ip route | awk '/default/{print $3}'):FOLDER" /mnt/share"""
MOUNT_OPENBSD = """mount_nfs -T "$(/sbin/route -n get default | awk '/gateway:/{print $2}'):FOLDER" /mnt/share"""
GUEST_COMMANDS = f"""Run inside the guest, as root (Debian/Ubuntu need: apt install nfs-common).
The host is the guest's default gateway.

Mount now:
  mkdir -p /mnt/share
  Linux:   {MOUNT_LINUX}
  OpenBSD: {MOUNT_OPENBSD}

Mount at every boot (add with crontab -e):
  Linux:   @reboot mkdir -p /mnt/share; for i in 1 2 3 4 5 6; do {MOUNT_LINUX} && break; sleep 10; done
  OpenBSD: @reboot mkdir -p /mnt/share; for i in 1 2 3 4 5 6; do /sbin/{MOUNT_OPENBSD} && break; sleep 10; done
"""


def rc_line(layout: Layout) -> str:
    return f"{PYTHON} {layout.sync_script}"


def bundled_script() -> str:
    return importlib.resources.files("vmmanager").joinpath("sync_exports.py").read_text()


PF_BULLET = "Update the vmmanager pf rules (keeps NFS off the local network)"


def setup_needed(sys: System, layout: Layout) -> list[str]:
    bullets = []
    if sys.read_file(layout.sync_script) != bundled_script():
        bullets.append(f"Install the sync script {layout.sync_script} (writes vmmanager's lines in /etc/exports)")
    if rc_line(layout) not in (sys.read_file(layout.rc_local) or "").splitlines():
        bullets.append("Run the sync script at boot (adds a line to /etc/rc.local)")
    if any(sys.run(["rcctl", "get", svc, "status"], check=False).rc != 0 for svc in NFS_SERVICES):
        bullets.append("Enable the NFS services portmap, mountd and nfsd (TCP only, 4 server processes)")
    if not anchor_current(sys, layout):
        bullets.append(PF_BULLET)
    return bullets


def setup_sharing(sys: System, layout: Layout) -> None:
    line = rc_line(layout)
    rc = sys.read_file(layout.rc_local)
    tx = RootTransaction(sys, lambda: None)
    tx.write(layout.sync_script, bundled_script(), 0o755)
    if rc is None:
        tx.write(layout.rc_local, "# added by vmmanager\n" + line + "\n")
    elif line not in rc.splitlines():
        tx.write(layout.rc_local, rc + "\n# added by vmmanager\n" + line + "\n")
    tx.commit()
    sys.run(["rcctl", "enable", *NFS_SERVICES], root=True)  # not started: mountd and nfsd refuse to start until /etc/exports has a non-comment line
    sys.run(["rcctl", "set", "nfsd", "flags", "-t", "-n", "4"], root=True)  # after enable: rcctl refuses flags on a disabled daemon


def folder(layout: Layout, name: str) -> str | None:
    try:
        return layout.share_file(name).read_text().strip()
    except FileNotFoundError:
        return None


def _sync_problem(sys: System, layout: Layout) -> str | None:
    r = sys.run([PYTHON, str(layout.sync_script)], root=True, check=False)
    return r.err if r.rc != 0 else None


def check_folder(folder: str) -> None:
    if folder.startswith("/") and os.path.normpath(folder).lstrip("/") == "":
        raise VmmError("Refusing to share the whole filesystem (/)", detail="choose a folder inside it")
    if not valid_folder(folder):
        raise VmmError(f"Cannot share {folder!r}", detail="use an absolute path without spaces, # \\ $ % ` or quotes")
    if not os.path.isdir(folder):
        raise VmmError(f"Cannot share {folder}", detail="the folder does not exist")


def set_share(sys: System, layout: Layout, name: str, folder: str | None) -> None:
    if folder is not None:
        check_folder(folder)
    tx = RootTransaction(sys, lambda: _sync_problem(sys, layout))
    if folder is None:
        tx.delete(layout.share_file(name))
    else:
        tx.write(layout.share_file(name), folder + "\n")
    tx.commit()
    if folder is not None:  # the sync has now written a line, so the daemons can start
        for svc in NFS_SERVICES:
            if sys.run(["rcctl", "check", svc], check=False).rc != 0:
                sys.run(["rcctl", "start", svc], root=True)


def sync(sys: System, layout: Layout) -> None:
    if not layout.sync_script.exists():
        return
    problem = _sync_problem(sys, layout)
    if problem is not None:
        raise VmmError("Updating /etc/exports failed", detail=problem)


def _marked_pairs(text: str, vms: set[str]) -> list[str]:
    lines = text.splitlines()
    return [x for i, l in enumerate(lines) if l.startswith(MARK) and l[len(MARK) + 2:-1] in vms for x in lines[i:i + 2]]


def stale(sys: System, layout: Layout, status_text: str) -> bool:
    # only this user's VMs: another user's folder can look missing from here (0700 home)
    mine = {p.stem for p in (layout.etc_dir / layout.user).glob("*.*")}
    wanted = wanted_lines(layout.etc_dir, {vm: i for vm, i in parse_ids(status_text).items() if vm in mine})[0]
    text = _read_without_root(layout.exports)
    return text is not None and _marked_pairs(text, mine) != wanted


def guest_commands(folder: str) -> str:
    return GUEST_COMMANDS.replace("FOLDER", folder)
