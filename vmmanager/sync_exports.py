#!/usr/local/bin/python3
"""Write vmmanager's lines in /etc/exports. Run as root; stdlib only (installed as /etc/vmmanager/sync-exports)."""
import fcntl
import os
import re
import subprocess
import sys
from pathlib import Path

MARK = "# vmmanager: the next line is managed by vmmanager"


def valid_folder(path: str) -> bool:
    return path.startswith("/") and not re.search(r'[\s#\\"]', path)


def parse_ids(status_text: str) -> dict[str, int]:
    ids = {}
    for line in status_text.splitlines():
        f = line.split()
        if len(f) == 9 and f[0].isdigit():
            ids[f[8]] = int(f[0])
    return ids


def wanted_lines(etc_dir: Path, ids: dict[str, int]) -> tuple[list[str], list[str]]:
    lines, problems = [], []
    for share in sorted(etc_dir.glob("*/*.share"), key=lambda p: p.stem):
        vm, owner = share.stem, share.parent.name
        conf = share.with_suffix(".conf")
        if vm not in ids or not conf.is_file():
            continue
        if not re.search(r"^\s*local\s+interface\b", conf.read_text(), re.M):
            continue
        folder = share.read_text().strip()
        if not valid_folder(folder):
            problems.append(f"{vm}: invalid folder {folder!r}")
        elif not os.path.isdir(folder):
            problems.append(f"{vm}: folder {folder} does not exist")
        else:
            lines += [f"{MARK} ({vm})", f"{folder} -mapall={owner} 100.64.{ids[vm]}.3"]
    return lines, problems


def exports_with(text: str, lines: list[str]) -> str:
    kept, skip = [], False
    for line in text.splitlines():
        if skip:
            skip = False
        elif line.startswith(MARK):
            skip = True
        else:
            kept.append(line)
    return "".join(l + "\n" for l in kept + lines)


def sync(etc_dir: Path, exports: Path, lock: Path, status_text: str) -> list[str]:
    with open(lock, "a") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        lines, problems = wanted_lines(etc_dir, parse_ids(status_text))
        old = exports.read_text() if exports.exists() else ""
        new = exports_with(old, lines)
        if new != old:
            tmp = exports.with_name(exports.name + ".vmmanager")
            tmp.write_text(new)
            tmp.chmod(0o644)
            os.replace(tmp, exports)
            subprocess.run(["pkill", "-HUP", "-x", "mountd"])
    return problems


def main() -> int:
    # no status means no ids: syncing then would strip every share, so leave /etc/exports alone
    try:
        r = subprocess.run(["vmctl", "status"], capture_output=True, text=True)
    except OSError as e:
        print(f"vmctl status: {e}", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print(f"vmctl status failed: {r.stderr.strip()}", file=sys.stderr)
        return 1
    for p in sync(Path("/etc/vmmanager"), Path("/etc/exports"), Path("/var/run/exports.lock"), r.stdout):
        print(p, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
