"""The bundled man pages: view them in place or install them system-wide."""

from importlib import resources
from pathlib import Path

from vmmanager import __version__, tomlio
from vmmanager.layout import Layout
from vmmanager.system import System

MAN_DIR = Path("/usr/local/man")


def bundled() -> list[Path]:
    return sorted(p for p in (resources.files("vmmanager") / "man").iterdir() if p.is_file())  # type: ignore[arg-type]


def view(sys: System, page: str = "vmmanager.8") -> int:
    return sys.interactive(["man", "-l", str(resources.files("vmmanager") / "man" / page)])


def install(sys: System) -> None:
    for page in bundled():
        section = MAN_DIR / f"man{page.suffix[1:]}"
        if not section.exists():
            sys.install_dir(section)
        sys.install_file(page.read_text(encoding="utf-8"), section / page.name, 0o644)
    sys.run(["makewhatis", str(MAN_DIR)], root=True)


def should_offer(layout: Layout, doas_state: str) -> bool:
    if doas_state not in ("ok", "password"):
        return False
    state = tomlio.load(layout.state_file)
    return not state.get("man_declined") and state.get("man_installed_version") != __version__


def record(layout: Layout, installed: bool) -> None:
    state = tomlio.load(layout.state_file)
    state["man_installed_version" if installed else "man_declined"] = __version__ if installed else True
    tomlio.save(layout.state_file, state)
