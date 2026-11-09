import os
from collections.abc import Callable
from pathlib import Path

from vmmanager.errors import VmmError
from vmmanager.system import System


class RootTransaction:
    """Stages root file writes/deletes; commit applies them, runs check, and restores everything on failure."""

    def __init__(self, sys: System, check: Callable[[], str | None]):
        self.sys = sys
        self.check = check
        # (path, new content or None to delete, previous content or None, mode)
        self._staged: list[tuple[Path, str | None, str | None, int]] = []

    def _stage(self, path: Path, content: str | None) -> None:
        old = self.sys.read_file(path)
        mode = 0o644 if old is None else os.stat(path).st_mode & 0o777
        self._staged.append((path, content, old, mode))

    def write(self, path: Path, content: str) -> None:
        self._stage(path, content)

    def delete(self, path: Path) -> None:
        self._stage(path, None)

    def commit(self) -> None:
        touched: list[tuple[Path, str | None, str | None, int]] = []
        try:
            for item in self._staged:
                path, content, _, mode = item
                touched.append(item)  # before applying: a failed install may be partial
                if content is None:
                    self.sys.remove_file(path)
                else:
                    if not path.parent.is_dir():
                        self.sys.install_dir(path.parent)
                    self.sys.install_file(content, path, mode)
            problem = self.check()
        except BaseException as e:  # incl. Ctrl-C at a doas prompt: never leave /etc half-modified
            failed = self._rollback(touched)
            if isinstance(e, VmmError):
                e.detail = "\n".join(filter(None, [e.detail, failed]))
            raise
        if problem is not None:
            failed = self._rollback(touched)
            if failed:
                raise VmmError("Configuration check failed; some files could not be restored", detail=f"{problem}\n{failed}")
            raise VmmError("Configuration check failed; nothing was changed", detail=problem)

    def _rollback(self, touched) -> str:
        """Restore every touched file; return a description of any that could not be restored."""
        failures = []
        for path, _, old, mode in reversed(touched):
            try:
                if old is None:
                    self.sys.remove_file(path)
                else:
                    self.sys.install_file(old, path, mode)
            except Exception as e:
                detail = f"{e.message} {e.detail}" if isinstance(e, VmmError) else str(e)
                failures.append(f"{path}: {detail}".strip())
        return "Rollback failed for: " + "; ".join(failures) if failures else ""
