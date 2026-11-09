import contextlib
import os
from pathlib import Path

from vmmanager.errors import VmmError
from vmmanager.system import Result, System


class FakeSystem(System):
    """Records commands; file methods act directly on the given paths."""

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.calls: list[tuple[list[str], bool]] = []
        self.responses: dict[tuple[str, ...], Result] = {}
        self.modes: dict[Path, int] = {}
        self.auth_mode = "ok"
        self.suspend_count = 0
        self.interactive_rc = 0

        @contextlib.contextmanager
        def suspend():
            self.suspend_count += 1
            yield

        self.suspend = suspend

    def run(self, argv, *, root=False, check=True):
        self.calls.append((list(argv), root))
        best: tuple[str, ...] = ()
        r = Result(0, "", "")
        for prefix, resp in self.responses.items():
            if tuple(argv[: len(prefix)]) == prefix and len(prefix) >= len(best):
                best, r = prefix, resp
        if check and r.rc != 0:
            raise VmmError(f"{argv[0]} failed", detail=r.err)
        return r

    def interactive(self, argv, *, root=False):
        self.calls.append((list(argv), root))
        return self.interactive_rc

    def read_file(self, path):
        try:
            return path.read_text()
        except FileNotFoundError:
            return None

    def install_file(self, content, dest, mode):
        dest.write_text(content)
        os.chmod(dest, mode)
        self.modes[dest] = mode

    def install_dir(self, path):
        path.mkdir(parents=True, exist_ok=True)

    def remove_file(self, path):
        path.unlink(missing_ok=True)

    def doas_state(self):
        return self.auth_mode
