"""VmFormScreen: the create and update form."""

import re
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label, Static, Switch

from vmmanager.errors import VmmError
from vmmanager.inventory import VmEntry
from vmmanager.layout import quote, validate_name
from vmmanager.ops import VmSpec
from vmmanager.ui.dialogs import Dialog, IsoPickerScreen

MEMORY_RE = re.compile(r"^\d+(\.\d+)?[KMGTkmgt]?$")  # same as confblock's check
DISK_RE = re.compile(r"^\d+[KMGTkmgt]?$")  # same as ops' check


def kept_lines(block: str) -> list[str]:
    """The block's lines the form does not edit; update keeps them as written."""
    kept = []
    for line in block.strip().splitlines()[1:-1]:
        words = line.split()
        if words and words[0] not in ("memory", "cpus", "cdrom") and words[:2] != ["local", "interface"]:
            kept.append(line.strip())
    return kept


class VmFormScreen(Dialog):
    """Create mode dismisses with a VmSpec; update mode with a dict of the changed keys."""

    DEFAULT_CSS = "VmFormScreen Horizontal Input { width: 1fr; }"

    def __init__(self, mode: str = "create", entry: VmEntry | None = None, block: str = ""):
        super().__init__()
        self.mode, self.entry, self.block = mode, entry, block
        f = entry.facts if entry else None
        if mode == "create":
            self.initial = {"memory": "1G", "cpus": "1", "iso": "", "internet": True}
        else:  # a missing memory/cpus line shows vmd's default
            self.initial = {
                "memory": (f and f.memory) or "512M",
                "cpus": str((f and f.cpus) or 1),
                "iso": (f and f.cdrom) or "",
                "internet": entry.internet,
            }

    def compose(self) -> ComposeResult:
        create = self.mode == "create"
        other_interfaces = bool(self.entry and self.entry.facts and self.entry.facts.other_interfaces)
        with VerticalScroll():
            yield Static("Create a VM" if create else f"Update {self.entry.name}", classes="title", markup=False)
            if create:
                yield Label("Name")
                yield Input(id="name")
            yield Label("Memory")
            yield Input(self.initial["memory"], id="memory")
            yield Label("CPUs")
            yield Input(self.initial["cpus"], id="cpus")
            if create:
                yield Label("Disk size")
                yield Input("20G", id="disk")
            yield Label("Install ISO (optional)")
            with Horizontal():
                yield Input(self.initial["iso"], id="iso")
                yield Button("Browse…", id="browse")
            yield Label("Internet" + (" (the VM has other interfaces; edit vm.conf by hand)" if other_interfaces else ""))
            yield Switch(self.initial["internet"], id="internet", disabled=other_interfaces)
            if create:
                yield Label("OS label")
                yield Input(id="os")
                yield Label("Notes")
                yield Input(id="notes")
            else:
                yield Label("Kept as written")
                yield Static("\n".join(kept_lines(self.block)), id="kept", markup=False)
            yield Static(id="errors", markup=False)
            with Horizontal():
                yield Button("Create" if create else "Save", id="submit", variant="primary")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#browse")
    def browse(self) -> None:
        def picked(path: str | None) -> None:
            if path:
                self.query_one("#iso", Input).value = path
        self.app.push_screen(IsoPickerScreen(Path.home()), picked)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    def _errors(self, v: dict[str, str]) -> list[str]:
        errors = []
        if self.mode == "create":
            try:
                validate_name(v["name"])
            except VmmError as e:
                errors.append(e.message)
            if not DISK_RE.fullmatch(v["disk"]):
                errors.append(f"Invalid disk size {v['disk']!r}: use a size like 20G")
        if not MEMORY_RE.fullmatch(v["memory"]):
            errors.append(f"Invalid memory {v['memory']!r}: use a size like 512M or 2G")
        if not (v["cpus"].isdigit() and 1 <= int(v["cpus"]) <= 64):
            errors.append(f"Invalid CPUs {v['cpus']!r}: use a whole number from 1 to 64")
        if v["iso"]:
            try:
                quote(v["iso"])
            except VmmError as e:
                errors.append(e.message)
        return errors

    @on(Input.Submitted)
    @on(Button.Pressed, "#submit")
    def submit(self) -> None:
        v = {i.id: i.value.strip() for i in self.query(Input)}
        errors = self._errors(v)
        if errors:
            self.query_one("#errors", Static).update("\n".join(errors))
            return
        internet = self.query_one("#internet", Switch).value
        if self.mode == "create":
            self.dismiss(VmSpec(v["name"], v["memory"], int(v["cpus"]), v["disk"],
                                v["iso"] or None, internet, v["os"], v["notes"]))
            return
        changes: dict[str, object] = {}
        if v["memory"] != self.initial["memory"]:
            changes["memory"] = v["memory"]
        if v["cpus"] != self.initial["cpus"]:
            changes["cpus"] = int(v["cpus"])
        if v["iso"] != self.initial["iso"]:
            changes["cdrom"] = v["iso"] or None
        if internet != self.initial["internet"]:
            changes["internet"] = internet
        self.dismiss(changes)
