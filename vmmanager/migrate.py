"""Move a hand-written VM's disks into ~/vmmanager/<name>/ and its vm.conf block into a managed conf."""

import os
from datetime import date, datetime, timezone
from pathlib import Path

from vmmanager import __version__, confblock, tomlio, vmctl
from vmmanager.errors import VmmError
from vmmanager.inventory import VmEntry
from vmmanager.ops import _tx, aggregate_text, require_clean_config, with_include
from vmmanager.system import System
from vmmanager.layout import Layout

MANUAL_STEPS = """To migrate by hand:
  1. Stop the VM.
  2. mkdir -p ~/vmmanager/<name> and move the disk(s) there.
  3. As root, create /etc/vmmanager/<user>/<name>.conf with the vm block, the new disk paths and "owner <user>".
  4. Add include "/etc/vmmanager/<user>/<name>.conf" to /etc/vmmanager/vmmanager.conf,
     and include "/etc/vmmanager/vmmanager.conf" to /etc/vm.conf if missing.
  5. Comment out the old block in /etc/vm.conf.
  6. doas vmd -n, then doas vmctl reload.
See vmmanager(8), MIGRATING."""


def qcow2_has_backing(sys: System, path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except PermissionError:
        out = sys.run(["od", "-An", "-tx1", "-N16", str(path)], root=True).out
        head = bytes(int(h, 16) for h in out.split())
    return head[:4] == b"QFI\xfb" and any(head[8:16])


def _block(sys: System, layout: Layout, name: str) -> tuple[str, tuple[int, int] | None]:
    """The vm.conf text and the span of the block; raises VmmError on an odd block."""
    text = sys.read_file(layout.vm_conf) or ""
    return text, confblock.find_block(text, name)


def migration_blockers(sys: System, layout: Layout, entry: VmEntry) -> list[str]:
    if entry.state in ("running", "paused"):
        return ["the VM is running or paused"]
    try:
        text, span = _block(sys, layout, entry.name)
    except VmmError:
        return ["its vm.conf block shares a line with other text"]
    if span is None:
        return ["the block isn't found in vm.conf"]
    f = confblock.facts(text[span[0]:span[1]])
    blockers = []
    if f.uses_macros:
        blockers.append("the block uses macros")
    if f.is_instance:
        blockers.append("the block is an instance of a template")
    if f.owner is not None and f.owner.strip('"').split(":")[0] != layout.user:
        blockers.append("the owner is another user")
    if entry.managed or layout.system_conf(entry.name).exists():
        blockers.append("the name is already managed")
    names = [Path(d).name for d in f.disks]
    if len(set(names)) != len(names):
        blockers.append("two disks have the same file name")
    home_dev = os.stat(layout.home).st_dev
    for disk in f.disks:
        try:
            st = os.stat(disk)
        except OSError:
            blockers.append(f"disk {disk} is missing")
            continue
        if qcow2_has_backing(sys, Path(disk)):
            blockers.append(f"disk {disk} is a qcow2 with a backing file")
        if st.st_dev != home_dev:
            blockers.append(f"disk {disk} is on a different filesystem than your home")
        if not os.access(Path(disk).parent, os.W_OK):
            blockers.append(f"the folder of disk {disk} is not writable")
    if layout.vm_dir(entry.name).exists():
        blockers.append(f"the folder {layout.vm_dir(entry.name)} already exists")
    return blockers


def _rollback(moved: list[tuple[str, Path]], vm_dir: Path, err: BaseException) -> BaseException:
    """Put disks back; returns the exception to raise, carrying any failure to do so."""
    failures = []
    for old, new in reversed(moved):
        try:
            os.rename(new, old)
        except OSError as e:
            failures.append(f"could not move {new} back to {old}: {e.strerror}; your disk is at {new}")
    try:
        vm_dir.rmdir()
    except OSError:
        pass
    if not failures or isinstance(err, KeyboardInterrupt):
        return err
    extra = "\n".join(failures)
    if isinstance(err, VmmError):
        return VmmError(err.message, detail=f"{err.detail}\n{extra}".strip())
    return VmmError(f"Migration failed: {err}", detail=extra)


def migrate_vm(sys: System, layout: Layout, entry: VmEntry) -> None:
    name = entry.name
    require_clean_config(sys, layout)
    blockers = migration_blockers(sys, layout, entry)
    if blockers:
        raise VmmError(f"Can't migrate {name} automatically", detail="\n".join(blockers) + "\n\n" + MANUAL_STEPS)
    text, span = _block(sys, layout, name)
    block = text[span[0]:span[1]]
    old_paths = confblock.facts(block).disks
    vm_dir = layout.vm_dir(name)
    mapping = {p: str(vm_dir / Path(p).name) for p in old_paths}
    conf_path = layout.system_conf(name)
    new_block = confblock.ensure_owner(confblock.replace_disks(block, mapping), layout.user)
    marker = f"# moved to vmmanager {date.today().isoformat()}: "
    moved: list[tuple[str, Path]] = []
    try:
        vm_dir.mkdir(parents=True)
        for old, new in mapping.items():
            os.rename(old, new)
            moved.append((old, Path(new)))
        tx = _tx(sys, layout)
        tx.write(conf_path, new_block)
        tx.write(layout.aggregate, aggregate_text(layout, extra=conf_path))
        tx.write(layout.vm_conf, with_include(confblock.comment_out(text, span, marker), layout))
        tx.commit()
    except BaseException as e:  # incl. Ctrl-C; RootTransaction already restored /etc
        raise _rollback(moved, vm_dir, e) from e
    # /etc now references the new disk paths: from here nothing is rolled back
    tomlio.save(layout.extras(name), {
        "migrated_from": ", ".join(old_paths),
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": __version__,
    })
    try:
        vmctl.reload(sys)
    except Exception as e:
        detail = e.detail if isinstance(e, VmmError) else str(e)
        raise VmmError(f"{name} was migrated but vmd could not reload its configuration",
                       detail=f"{detail}\nRun: doas vmctl reload") from e
