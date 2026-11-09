"""Run commands and touch root-owned files, via doas when root is needed."""

import contextlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager

from vmmanager.errors import VmmError

AUTH_REQUIRED = "Authentication required"


@dataclass
class Result:
    rc: int
    out: str
    err: str


class System:
    def __init__(self) -> None:
        self.suspend: Callable[[], ContextManager] = contextlib.nullcontext

    def _spawn(self, argv: list[str], capture: bool) -> Result:
        p = subprocess.run(argv, capture_output=capture, text=True)
        return Result(p.returncode, p.stdout or "", p.stderr or "")

    def run(self, argv: list[str], *, root: bool = False, check: bool = True) -> Result:
        if not root:
            r = self._spawn(argv, True)
        else:
            r = self._spawn(["doas", "-n", *argv], True)
            if AUTH_REQUIRED in r.err:
                # doas prompts on /dev/tty, so capturing output does not hide it
                with self.suspend():
                    r = self._spawn(["doas", *argv], True)
        if check and r.rc != 0:
            raise VmmError(f"{argv[0]} failed", detail=r.err)
        return r

    def interactive(self, argv: list[str], *, root: bool = False) -> int:
        with self.suspend():
            return self._spawn(["doas", *argv] if root else argv, False).rc

    def read_file(self, path: Path) -> str | None:
        try:
            return path.read_text()
        except FileNotFoundError:
            return None
        except PermissionError:
            return self.run(["cat", str(path)], root=True).out

    def install_file(self, content: str, dest: Path, mode: int) -> None:
        with tempfile.NamedTemporaryFile(delete=False, mode="w") as tmp:
            tmp.write(content)
        try:
            self.run(["install", "-o", "root", "-g", "wheel", "-m", f"{mode:04o}", tmp.name, str(dest)], root=True)
        finally:
            os.unlink(tmp.name)

    def install_dir(self, path: Path) -> None:
        self.run(["install", "-d", "-o", "root", "-g", "wheel", "-m", "0755", str(path)], root=True)

    def remove_file(self, path: Path) -> None:
        self.run(["rm", "-f", str(path)], root=True)

    def doas_state(self) -> str:
        if shutil.which("doas") is None:
            return "missing"
        r = self._spawn(["doas", "-n", "true"], True)
        if r.rc == 0:
            return "ok"
        return "password" if AUTH_REQUIRED in r.err else "denied"
