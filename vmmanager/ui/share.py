"""ShareScreen: choose the host folder to share with a VM."""

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Static

from vmmanager import share
from vmmanager.ui.dialogs import Dialog, FolderPickerScreen


class ShareScreen(Dialog):
    """Dismisses with ("share", folder), ("stop", None) or None."""

    DEFAULT_CSS = "ShareScreen Horizontal Input { width: 1fr; }"
    AUTO_FOCUS = "#folder"

    def __init__(self, name: str, folder: str | None, home: Path):
        super().__init__()
        self.vm, self.folder, self.home = name, folder, home

    def compose(self) -> ComposeResult:
        start = self.folder or f"{self.home}/"
        with VerticalScroll():
            yield Static(f"Share a folder with {self.vm}", classes="title", markup=False)
            with Horizontal():
                yield Input(start, id="folder")
                yield Button("Browse", id="browse")
            with Horizontal():
                yield Button("Share", id="share", variant="primary")
                if self.folder:
                    yield Button("Stop sharing", id="stop")
                yield Button("Cancel", id="cancel")
            yield Static(share.guest_commands(start), id="guest", markup=False)

    def _chosen(self) -> str:
        return self.query_one("#folder", Input).value.strip().rstrip("/") or "/"

    @on(Input.Changed)
    def changed(self) -> None:
        self.query_one("#guest", Static).update(share.guest_commands(self._chosen()))

    @on(Input.Submitted)
    def submitted(self) -> None:
        self.dismiss(("share", self._chosen()))

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "browse":
                chosen = Path(self._chosen())
                self.app.push_screen(FolderPickerScreen(chosen if chosen.is_dir() else self.home), self._picked)
            case "share":
                self.dismiss(("share", self._chosen()))
            case "stop":
                self.dismiss(("stop", None))
            case _:
                self.dismiss(None)

    def _picked(self, folder: str | None) -> None:
        if folder:
            self.query_one("#folder", Input).value = folder
