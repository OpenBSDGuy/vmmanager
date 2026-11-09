"""VmmApp: the VM list, its key bindings and the flow behind each key."""

import asyncio

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Static

from vmmanager import health, inventory, manpages, migrate, network, ops
from vmmanager.errors import VmmError
from vmmanager.inventory import VmEntry
from vmmanager.layout import Layout
from vmmanager.system import System
from vmmanager.ui.dialogs import ConfirmScreen, HealthScreen, MessageScreen
from vmmanager.ui.forms import VmFormScreen

ROOT_ACTIONS = {"create", "update", "delete", "migrate"}
SOCKET_HELP = (
    f"vmmanager can't use the vmd socket {health.VMD_SOCKET}.\n\n"
    "Members of group wheel can use it. For other users, an administrator must set "
    "'socket owner' in /etc/vm.conf (see vm.conf(5)).\n"
    "If vmd is not running, an administrator can start it with: rcctl enable vmd; rcctl start vmd"
)


def _cells(e: VmEntry) -> tuple[object, ...]:
    f, st = e.facts, e.status
    cpus = (f and f.cpus) or (st and st.vcpus) or ""
    memory = (f and f.memory) or (st and st.maxmem) or ""
    internet = ("on" if e.internet else "off") if f else ""
    return e.name, e.state, cpus, memory, internet, e.owner


class VmmApp(App):
    TITLE = "vmmanager"
    CSS = "#status { height: 1; padding: 0 1; }"
    BINDINGS = [
        ("n", "create", "Create"),
        ("s", "start", "Start"),
        ("r", "run", "Run"),
        ("o", "connect", "Connect"),
        ("x", "stop", "Stop"),
        ("e", "update", "Update"),
        ("d", "delete", "Delete"),
        ("m", "migrate", "Migrate"),
        ("question_mark", "manual", "Manual"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, sys: System, layout: Layout):
        super().__init__()
        self.sys, self.layout = sys, layout
        self.entries: list[VmEntry] = []
        self.doas: str | None = None  # known once the health check ran
        self._stopping: dict[str, int] = {}  # VM name -> refreshes seen since a graceful stop
        # kept as attributes: a refresh tick during shutdown finds nothing to query_one
        self.table = DataTable(cursor_type="row")
        self.status_bar = Static(id="status", markup=False)

    def compose(self) -> ComposeResult:
        yield self.table
        yield self.status_bar
        yield Footer()

    def on_mount(self) -> None:
        self.sys.suspend = self.suspend  # doas prompts and consoles get the real terminal
        self.table.add_columns("Name", "State", "CPUs", "Memory", "Internet", "Owner")
        self.refresh_list()
        self.set_interval(3, self.refresh_list)
        self.startup()

    # --- the list ---

    def _selected(self) -> VmEntry | None:
        table = self.table
        if not table.row_count:
            return None
        name = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return self._entry(name)

    def _entry(self, name: str | None) -> VmEntry | None:
        return next((e for e in self.entries if e.name == name), None)

    def refresh_list(self) -> None:
        try:
            entries = inventory.load_inventory(self.sys, self.layout)
        except (VmmError, OSError) as e:
            self.status_bar.update(e.message if isinstance(e, VmmError) else str(e))
            return
        table = self.table
        selected = self._selected()
        self.entries = entries
        table.clear()
        for e in entries:
            style = "dim" if not e.actions or e.actions == {"migrate"} else ""
            table.add_row(*(Text(str(c), style=style) for c in _cells(e)), key=e.name)
        names = [e.name for e in entries]
        if selected and selected.name in names:
            table.move_cursor(row=names.index(selected.name))
        self.show_reason()
        self._check_stopping()

    @on(DataTable.RowHighlighted)
    def show_reason(self) -> None:
        e = self._selected()
        self.status_bar.update((e.reason or "") if e else "")

    def _check_stopping(self) -> None:
        """Offer a force stop once a gracefully stopped VM is still up after 2 refreshes."""
        for name, seen in list(self._stopping.items()):
            e = self._entry(name)
            if e is None or e.state not in ("running", "paused"):
                del self._stopping[name]
            elif seen + 1 < 2:
                self._stopping[name] = seen + 1
            else:
                del self._stopping[name]
                self._offer_force_stop(e)

    # --- helpers for the flows ---

    def _can_root(self) -> bool:
        if self.doas in ("denied", "missing"):
            self.notify("needs doas — ask an admin", severity="warning", markup=False)
            return False
        return True

    def _ready_for(self, action: str) -> VmEntry | None:
        """The selected VM if it allows the action; otherwise say why and return None."""
        e = self._selected()
        if e is None:
            self.notify("No VM selected", severity="warning")
            return None
        if action not in e.actions:
            self.notify(e.reason or f"{e.name} is {e.state}", severity="warning", markup=False)
            return None
        if action in ROOT_ACTIONS and not self._can_root():
            return None
        return e

    async def _confirm(self, question: str, detail: str = "") -> bool:
        return await self.push_screen_wait(ConfirmScreen(question, detail))

    async def _call(self, fn, *args) -> bool:
        """Run a core function; show any error in a MessageScreen. True on success."""
        try:
            fn(*args)
            return True
        except VmmError as e:
            message, detail = e.message, e.detail
        except KeyboardInterrupt:  # Ctrl-C at a doas prompt
            message, detail = "Cancelled", ""
        except OSError as e:
            message, detail = str(e), ""
        await self.push_screen_wait(MessageScreen(message, detail))
        return False

    async def _offer_network(self, st: network.NetStatus | None = None) -> None:
        if st is None:
            try:
                st = network.net_status(self.sys, self.layout)
            except VmmError as e:
                await self.push_screen_wait(MessageScreen(e.message, e.detail))
                return
        if not st.ok and await self._confirm(
                "Set up networking so VMs can reach the internet?",
                network.setup_explanation(st) + "\n\nThis needs doas."):
            await self._call(network.setup_network, self.sys, self.layout)

    # --- start-up ---

    @work
    async def startup(self) -> None:
        while True:
            h = await asyncio.to_thread(health.check, self.sys, self.layout, self.entries)
            self.doas = h.doas
            if h.blocking:
                await self.push_screen_wait(HealthScreen(SOCKET_HELP, [], blocking=True))
                self.exit()
                return
            text, fixes = self._problems(h)
            if not text:
                break
            fix = await self.push_screen_wait(HealthScreen(text, fixes))
            if fix is None:
                break
            await self._fix(fix, h)
        if manpages.should_offer(self.layout, self.doas):
            if await self._confirm(
                    "Install man pages system-wide? (needs doas)",
                    f"Copies vmmanager(8) and vmmanager.toml(5) into {manpages.MAN_DIR} and runs makewhatis."):
                if await self._call(manpages.install, self.sys):
                    await self._call(manpages.record, self.layout, True)
            else:
                await self._call(manpages.record, self.layout, False)

    def _problems(self, h: health.Health) -> tuple[str, list[tuple[str, str]]]:
        lines, fixes = [], []
        if not h.vmd_running:
            lines.append("vmd is not running.")
            fixes.append(("vmd", "Enable vmd"))
        if not h.firmware:
            lines.append(f"The VM firmware {health.VMM_BIOS} is missing.")
            fixes.append(("firmware", "Fetch firmware"))
        own = [n for n in h.broken if self.layout.system_conf(n).exists()]
        for name in h.broken:
            if name in own:
                lines.append(f"{name}: its configuration is broken (was its folder deleted?)")
                fixes.append((f"remove {name}", f"Remove {name} config"))
            else:
                lines.append(f"{name}: its configuration is broken; it belongs to another user")
        if h.config_error and not own:
            lines.append(f"vm.conf has errors:\n{h.config_error.strip()}")
        if h.net and not h.net.ok:
            lines.append("Networking for VMs is not set up:\n" + network.setup_explanation(h.net))
            fixes.append(("network", "Set up networking"))
        return "\n\n".join(lines), fixes

    async def _fix(self, key: str, h: health.Health) -> None:
        if key == "vmd":
            if await self._confirm("Enable and start vmd?",
                                   "Runs rcctl enable vmd (adds it to /etc/rc.conf.local) and rcctl start vmd. "
                                   "This needs doas."):
                await self._call(health.enable_vmd, self.sys)
        elif key == "firmware":
            if await self._confirm("Fetch the VM firmware?",
                                   f"Runs fw_update vmm, which installs {health.VMM_BIOS}. This needs doas."):
                await self._call(health.fetch_firmware, self.sys)
        elif key == "network":
            await self._offer_network(h.net)
        else:
            name = key.removeprefix("remove ")
            if await self._confirm(
                    f"Remove the configuration of {name}?",
                    f"Deletes {self.layout.system_conf(name)} and its line in {self.layout.aggregate}, "
                    f"then reloads vmd. {self.layout.vm_dir(name)} is not touched. This needs doas."):
                await self._call(health.remove_broken, self.sys, self.layout, name)
                self.refresh_list()

    # --- key actions ---

    @work
    async def action_create(self) -> None:
        if not self._can_root():
            return
        spec = await self.push_screen_wait(VmFormScreen("create"))
        if spec is None:
            return
        L = self.layout
        if not await self._confirm(
                f"Create {spec.name}?",
                f"Writes {L.system_conf(spec.name)}, adds it to {L.aggregate}, adds the include line "
                f"to {L.vm_conf} if missing, then reloads vmd. The disk goes in {L.vm_dir(spec.name)}/. "
                "This needs doas."):
            return
        ok = await self._call(ops.create_vm, self.sys, L, spec)
        self.refresh_list()
        if not ok:
            return
        if spec.internet:
            await self._offer_network()
        e = self._entry(spec.name)
        if e and "run" in e.actions and await self._confirm("Run now (attach console to install)?"):
            await self._call(ops.run_vm, self.sys, L, e)
            self.refresh_list()

    @work
    async def action_start(self) -> None:
        if e := self._ready_for("start"):
            await self._call(ops.start_vm, self.sys, self.layout, e)
            self.refresh_list()

    @work
    async def action_run(self) -> None:
        if e := self._ready_for("run"):
            await self._call(ops.run_vm, self.sys, self.layout, e)
            self.refresh_list()

    @work
    async def action_connect(self) -> None:
        if e := self._ready_for("connect"):
            await self._call(ops.connect_vm, self.sys, e)
            self.refresh_list()

    @work
    async def action_stop(self) -> None:
        if e := self._ready_for("stop"):
            ok = await self._call(ops.stop_vm, self.sys, e)
            self.refresh_list()
            if ok:
                self._stopping[e.name] = 0

    @work
    async def _offer_force_stop(self, e: VmEntry) -> None:
        if await self._confirm(f"{e.name} is still running. Force stop?",
                               "Like pulling the power cord: anything the VM has not saved is lost."):
            await self._call(ops.stop_vm, self.sys, e, True)
            self.refresh_list()

    @work
    async def action_update(self) -> None:
        if not (e := self._ready_for("update")):
            return
        changes = await self.push_screen_wait(VmFormScreen("update", e, self.sys.read_file(e.conf_path) or ""))
        if changes is None:
            return
        if not changes:
            self.notify("Nothing changed")
            return
        if not await self._confirm(
                f"Update {e.name}?",
                f"Rewrites {e.conf_path} ({', '.join(changes)}), then reloads vmd. This needs doas."):
            return
        ok = await self._call(ops.update_vm, self.sys, self.layout, e.name, changes)
        self.refresh_list()
        if ok and changes.get("internet"):
            await self._offer_network()

    @work
    async def action_delete(self) -> None:
        if not (e := self._ready_for("delete")):
            return
        if not await self._confirm(
                f"Delete {e.name}?",
                f"Removes {e.conf_path} and its line in {self.layout.aggregate}, then reloads vmd. "
                "This needs doas."):
            return
        ok = await self._call(ops.delete_vm, self.sys, self.layout, e.name)
        self.refresh_list()
        if ok and await self._confirm(f"Also delete ~/vmmanager/{e.name}/ and its disk?",
                                      "This cannot be undone."):
            await self._call(ops.remove_vm_files, self.layout, e.name)

    @work
    async def action_migrate(self) -> None:
        if not (e := self._ready_for("migrate")):
            return
        L = self.layout
        if not await self._confirm(
                f"Migrate {e.name} to vmmanager?",
                f"1. Moves its disks into {L.vm_dir(e.name)}/.\n"
                f"2. Writes {L.system_conf(e.name)} (the block with the new disk paths and your owner line) "
                f"and adds it to {L.aggregate}.\n"
                f"3. Adds the include line to {L.vm_conf} if missing and comments out the old block there "
                "(nothing is deleted).\n"
                "4. Checks with vmd -n and reloads vmd. On failure the disks are moved back and every file "
                "is restored.\n\nThe VM must be stopped. This needs doas."):
            return
        ok = await self._call(migrate.migrate_vm, self.sys, L, e)
        self.refresh_list()
        if ok and e.internet:
            await self._offer_network()

    @work
    async def action_manual(self) -> None:
        await self._call(manpages.view, self.sys)
