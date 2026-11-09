"""Modal dialogs: ConfirmScreen, MessageScreen, IsoPickerScreen and HealthScreen."""

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Static


class Dialog(ModalScreen):
    """A centred box; Escape closes it with no answer."""

    DEFAULT_CSS = """
    Dialog { align: center middle; }
    Dialog > VerticalScroll {
        width: 90; max-width: 100%; height: auto; max-height: 90%;
        border: thick $primary; background: $surface; padding: 1 2;
    }
    Dialog .title { text-style: bold; margin-bottom: 1; }
    Dialog Horizontal { height: auto; margin-top: 1; }
    Dialog Button { margin-right: 2; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmScreen(Dialog):
    """A yes/no question; dismisses with True or False."""

    AUTO_FOCUS = "#no"  # Enter must never confirm by accident
    BINDINGS = [("y", "answer(True)", "Yes"), ("n", "answer(False)", "No")]

    def __init__(self, question: str, detail: str = ""):
        super().__init__()
        self.question, self.detail = question, detail

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self.question, classes="title", markup=False)
            if self.detail:
                yield Static(self.detail, markup=False)
            with Horizontal():
                yield Button("Yes", id="yes", variant="primary")
                yield Button("No", id="no")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)

    def action_close(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class MessageScreen(Dialog):
    """An error or notice with optional detail text, such as vmd's output."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__()
        self.message, self.detail = message, detail

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self.message, classes="title", markup=False)
            if self.detail:
                yield Static(self.detail, markup=False)
            with Horizontal():
                yield Button("OK", id="ok", variant="primary")

    @on(Button.Pressed)
    def pressed(self) -> None:
        self.dismiss(None)


class IsoTree(DirectoryTree):
    def filter_paths(self, paths):
        return [p for p in paths if p.is_dir() or p.suffix.lower() in (".iso", ".img")]


class IsoPickerScreen(Dialog):
    """Browse for an install image; dismisses with its path or None."""

    def __init__(self, start: Path):
        super().__init__()
        self.start = start

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Choose an .iso or .img file (Escape to cancel)", classes="title")
            yield IsoTree(self.start)

    @on(DirectoryTree.FileSelected)
    def picked(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(str(event.path))


class HealthScreen(Dialog):
    """Start-up problems with a button per fix.

    Dismisses with the chosen fix key, or None for Continue/Quit. A blocking
    problem offers only Quit.
    """

    def __init__(self, text: str, fixes: list[tuple[str, str]], blocking: bool = False):
        super().__init__()
        self.text, self.fixes, self.blocking = text, fixes, blocking

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Health check", classes="title")
            yield Static(self.text, id="problems", markup=False)
            with Horizontal():
                for i, (_, label) in enumerate(self.fixes):
                    yield Button(label, id=f"fix-{i}", variant="primary")
                if self.blocking:
                    yield Button("Quit", id="quit")
                else:
                    yield Button("Continue", id="continue")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        self.dismiss(self.fixes[int(bid[4:])][0] if bid.startswith("fix-") else None)
