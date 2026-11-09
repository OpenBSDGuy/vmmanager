# vmmanager v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A PyPI-installable Textual TUI that creates, starts, runs, connects, stops, updates, deletes and migrates OpenBSD vmd VMs in a VirtualBox-like per-VM folder layout, with automatic NAT networking and system-wide man pages.

**Architecture:** A UI-free core (`vmmanager/*.py`) does all the logic and talks to the OS only through one `System` object (subprocess, doas, root file writes). Tests swap in a `FakeSystem` that works on a temp directory, so the whole core is tested on Linux. A thin Textual layer (`vmmanager/ui/`) calls core functions and hands the terminal to doas and `vmctl console` when needed.

**Tech Stack:** Python ≥ 3.11 (stdlib `tomllib`), Textual ≥ 8.2, unittest, setuptools, GitHub Actions. Live target: OpenBSD 8.0 amd64.

**Spec:** `docs/superpowers/specs/2026-11-09-vmmanager-design.md`. Read it before any task. This plan doesn't repeat its reasoning.

## Global Constraints

- Name `vmmanager` everywhere: PyPI project, import package, console script, `~/vmmanager/`, `/etc/vmmanager/`.
- `requires-python = ">=3.11"`. Runtime deps: `textual>=8.2` only.
- License `GPL-3.0-or-later`; author/URLs/classifiers styled like `/home/kixz/Private/mygit/wifzf/pyproject.toml` (repo URL `https://github.com/kasramp/vmmanager`).
- Runtime is OpenBSD only. `vmmanager` exits with a clear message on other OSes, except `--version` and `--man`. Tests must pass on Linux.
- The system files `/etc/vm.conf`, `/etc/pf.conf` and `/etc/sysctl.conf` get only these edits, never others:
  - `/etc/vm.conf`: the line `include "/etc/vmmanager/vmmanager.conf"`, and migrated blocks commented out with the marker `# moved to vmmanager YYYY-MM-DD: `.
  - `/etc/pf.conf`: the lines `anchor "vmmanager"` and `load anchor "vmmanager" from "/etc/vmmanager/pf.conf"`.
  - `/etc/sysctl.conf`: the line `net.inet.ip.forwarding=1`.
- Files written under `/etc/vmmanager` are `root:wheel 0644`, dirs `0755`. Existing files keep their mode (pf.conf is `0600`).
- After every root write, `vmd -n` passes for vm.conf changes, or `pfctl -nf /etc/pf.conf` passes for pf changes. Otherwise every staged file is restored byte-for-byte.
- Every block the tool writes contains `owner <user>` and `disable`.
- VM name regex `^[A-Za-z][A-Za-z0-9._-]*$`. Create defaults: memory `1G`, cpus `1`, disk `20G`, internet on.
- Tests use `unittest` (`python -m unittest discover -s tests -v`), as in wifzf. No pytest.
- Never run privileged commands on the dev (Linux) machine. Live testing happens only on the author's OpenBSD 8.0 test machine.

## Review Focus

1. **doas without `persist`** (OpenBSD's example doas.conf has none): every root command must still work, prompting once per command on the real terminal. It must never hang or report a failure. Tested in Task 3, `test_root_falls_back_to_interactive_when_auth_required`.
2. **A check that fails after files were installed** (e.g. a typo'd ISO path makes `vmd -n` fail): `/etc` must end up byte-identical to before, and the user must see vmd's error text. Tested in Task 6 `test_commit_rolls_back_on_check_failure` and Task 8 `test_create_rolls_back_everything_when_vmd_check_fails`.
3. **A VM folder deleted by hand**: the inventory still loads, the health check names the broken VM, and the owner can remove its config. Tested in Task 7 `test_managed_conf_without_status_entry` and Task 11 `test_broken_vm_detected_from_vmd_error`.
4. **Paths with spaces or quotes** (`/home/u/My ISOs/debian 12.iso`): spaces are quoted correctly, and `"`, `\` or a newline is refused with a message, never written. Tested in Task 2 `test_quote_*` and Task 8 `test_create_rejects_iso_path_with_quote`.
5. **Unexpected `vmctl status` lines** (paused/running VMs, owner without a group, a blank line or a warning line): known rows parse, and unknown lines are skipped without crashing. Tested in Task 3 `test_parse_status_*`.

---

## File Structure

```
pyproject.toml  requirements.txt  LICENSE  README.md  publish_pypi.sh  .gitignore
.github/workflows/test.yml
vmmanager/
  __init__.py      __version__ = "0.1.0"
  cli.py           argparse entry point
  errors.py        VmmError
  layout.py        Layout (all paths), name validation, vm.conf quoting
  system.py        System: run / root run / interactive / root file ops / doas state
  vmctl.py         VmStatus, parse_status, vmctl + vmd wrappers
  confblock.py     text operations on vm "name" { ... } blocks
  tomlio.py        flat TOML dump/read (extras + state)
  rootwrite.py     RootTransaction (stage, install, check, rollback)
  inventory.py     VmEntry, load_inventory, allowed actions
  ops.py           create / update / delete / start / stop / run / connect, disk safety
  network.py       NetStatus, host NAT setup
  migrate.py       migration blockers + migrate_vm
  health.py        Health report + fixes
  manpages.py      bundled pages: view / install / offer logic
  man/vmmanager.8  man/vmmanager.toml.5
  ui/__init__.py
  ui/app.py        VmmApp, main table screen, key bindings
  ui/forms.py      VmFormScreen (create + update)
  ui/dialogs.py    ConfirmScreen, MessageScreen, IsoPickerScreen, HealthScreen
tests/
  __init__.py  fakes.py  fixtures/
  test_cli.py test_layout.py test_system.py test_vmctl.py test_confblock.py
  test_tomlio.py test_rootwrite.py test_inventory.py test_ops.py
  test_network.py test_migrate.py test_health.py test_manpages.py test_ui.py
```

Commit after each task. The repo isn't a git repo yet; Task 1 runs `git init`. Never push: the user creates the GitHub remote.

---

### Task 1: Project skeleton, CLI stub, CI

**Files:**
- Create: `pyproject.toml`, `requirements.txt` (`textual>=8.2`), `LICENSE` (copy `/home/kixz/Private/mygit/wifzf/LICENSE`), `README.md` (title, one-paragraph description, `pipx install vmmanager`, "OpenBSD only"), `.gitignore` (venv/, dist/, build/, *.egg-info, __pycache__/), `vmmanager/__init__.py`, `vmmanager/errors.py`, `vmmanager/cli.py`, `.github/workflows/test.yml`, `tests/__init__.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `vmmanager.__version__: str`. `class VmmError(Exception): __init__(self, message: str, detail: str = "")` with attributes `.message` and `.detail`. `cli.main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1:** `git init`, then create the venv: `python3 -m venv venv && venv/bin/pip install -r requirements.txt`.
- [ ] **Step 2: Write the failing test** `tests/test_cli.py`:
  - `test_version_prints_version`: `main(["--version"])` returns 0 and stdout (via `contextlib.redirect_stdout`) equals `f"vmmanager {__version__}\n"`.
  - `test_refuses_non_openbsd`: patch `platform.system` to return `"Linux"`. `main([])` returns 1 and stderr contains `"vmmanager only runs on OpenBSD"`.
- [ ] **Step 3:** Run `venv/bin/python -m unittest tests.test_cli -v`. Expected: FAIL (ImportError).
- [ ] **Step 4:** Implement `pyproject.toml` (wifzf's layout, with `packages = ["vmmanager", "vmmanager.ui"]`, `[tool.setuptools.package-data] vmmanager = ["man/*"]`, script `vmmanager = "vmmanager.cli:main"`, classifiers OpenBSD/Console/System :: Emulators). Implement `cli.main` with argparse flags `--version`, `--man`, `--install-man`. Add `--man`/`--install-man` handling later in Task 12, and the UI launch in Task 13. Leave a `# Task 13` placeholder that returns 0.
- [ ] **Step 5:** Run the tests again. Expected: PASS.
- [ ] **Step 6:** Write `.github/workflows/test.yml` copying wifzf's, with these changes:
  - matrix `["3.11", "3.14"]`
  - step `pip install -r requirements.txt`
  - `py_compile vmmanager/*.py vmmanager/ui/*.py`
  - unittest discover
  - an extra step `sudo apt-get install -y mandoc && mandoc -Tlint -Wwarning vmmanager/man/*`, guarded with `if: hashFiles('vmmanager/man/vmmanager.8') != ''`
- [ ] **Step 7:** Commit: `git add -A && git commit -m "Project skeleton with CLI stub and CI"`.

### Task 2: Layout, names, quoting

**Files:** Create `vmmanager/layout.py`, `tests/test_layout.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Layout:
      etc: Path = Path("/etc"); home: Path = Path.home(); user: str = getpass.getuser()
      vm_conf, pf_conf, sysctl_conf: Path       # etc/"vm.conf" etc.
      etc_dir: Path                             # etc/"vmmanager"
      aggregate: Path                           # etc_dir/"vmmanager.conf"
      pf_anchor: Path                           # etc_dir/"pf.conf"
      user_dir: Path                            # home/"vmmanager"
      state_file: Path                          # user_dir/"state.toml"
      def vm_dir(self, name) -> Path
      def disk(self, name) -> Path              # vm_dir/f"{name}.qcow2"
      def extras(self, name) -> Path            # vm_dir/"vmmanager.toml"
      def system_conf(self, name, user=None) -> Path   # etc_dir/(user or self.user)/f"{name}.conf"
      include_line: str                         # f'include "{aggregate}"'
  def validate_name(name: str) -> None          # raises VmmError
  def quote(value: str) -> str                  # vm.conf double-quoted string; raises VmmError
  ```
  Implement the path members as `@property`.
- [ ] **Step 1: Failing tests:**
  - `test_paths_default_layout`: `Layout(etc=Path("/etc"), home=Path("/home/u"), user="u")`. `system_conf("deb")` is `/etc/vmmanager/u/deb.conf`, `disk("deb")` is `/home/u/vmmanager/deb/deb.qcow2`, and `include_line == 'include "/etc/vmmanager/vmmanager.conf"'`.
  - `test_validate_name_accepts`: `deb-12`, `a`, `x.y_z`.
  - `test_validate_name_rejects`: `""`, `"1abc"`, `"a b"`, `"a/b"`, `"-x"`. Each raises VmmError.
  - `test_quote_plain_and_spaces`: `quote("/a b/c.iso") == '"/a b/c.iso"'`.
  - `test_quote_rejects`: values containing `"`, `\\` or `\n` raise VmmError, with a message naming the character.
- [ ] **Step 2:** Run `venv/bin/python -m unittest tests.test_layout -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `layout.py`.
- [ ] **Step 4:** Run the tests. Expected: PASS. Commit: `"Add Layout, name validation and vm.conf quoting"`.

### Task 3: System runner and vmctl wrappers

**Files:** Create `vmmanager/system.py`, `vmmanager/vmctl.py`, `tests/fakes.py`, `tests/test_system.py`, `tests/test_vmctl.py`, `tests/fixtures/status_stopped.txt` (copy below), `tests/fixtures/status_mixed.txt`

`status_stopped.txt` (real output from the box, 2026-11-09):
```
   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER    STATE NAME
    1     -     4    8.0G       -       -    kixz:kixz  stopped debian
    2     -     4    8.0G       -       -    kixz:kixz  stopped debian-12
    3     -     1    8.0G       -       -    kixz:kixz  stopped openbsd
```
`status_mixed.txt`: the header, then a running row (`4 51234 1 1.0G 512M ttyp3 alice running web`), a paused row with owner `root` (no group), a blank line, and a `vmctl: warning text` line.

**Interfaces:**
- Produces (system.py):
  ```python
  @dataclass class Result: rc: int; out: str; err: str
  class System:
      suspend: Callable[[], ContextManager]   # default contextlib.nullcontext; UI sets app.suspend
      def run(self, argv: list[str], *, root: bool = False, check: bool = True) -> Result
      def interactive(self, argv: list[str], *, root: bool = False) -> int  # inside self.suspend(), inherits the terminal
      def read_file(self, path: Path) -> str | None      # None if missing; uses root `cat` on PermissionError
      def install_file(self, content: str, dest: Path, mode: int) -> None   # temp in user_dir/.tmp → doas install -o root -g wheel -m <mode>
      def install_dir(self, path: Path) -> None          # doas install -d -o root -g wheel -m 0755
      def remove_file(self, path: Path) -> None          # doas rm -f
      def doas_state(self) -> str                        # "ok" | "password" | "denied" | "missing"
  ```
  The root run tries `["doas", "-n", *argv]` first. If its stderr contains `Authentication required`, it reruns `["doas", *argv]` inside `self.suspend()` with stdout/stderr captured. doas prompts on `/dev/tty`, so capturing doesn't hide the prompt. With `check=True` and rc≠0, it raises `VmmError(f"{argv[0]} failed", detail=err)`. `doas_state`: no doas binary → `"missing"`; `doas -n true` rc 0 → `"ok"`; `Authentication required` → `"password"`; anything else (`Operation not permitted`) → `"denied"`. Confirm these messages on the box in Task 14.
- Produces (vmctl.py):
  ```python
  @dataclass(frozen=True)
  class VmStatus: id: int; pid: int | None; vcpus: int; maxmem: str; curmem: str | None; tty: str | None; owner: str; state: str; name: str
      owner_user: str   # property: owner.split(":")[0]
  def parse_status(text: str) -> list[VmStatus]
  def status(sys: System) -> list[VmStatus]                      # vmctl status
  def check_config(sys: System, path: Path) -> str | None        # vmd -n -f path; None if OK else stderr
  def reload(sys) ; start(sys, name) ; stop(sys, name, force=False) ; create_disk(sys, path: Path, size: str)
  def run_console(sys, name) -> int    # interactive vmctl start -c name
  def connect_console(sys, name) -> int  # interactive vmctl console name
  ```
- Produces (tests/fakes.py): `FakeSystem(System)` with `root: Path` (a temp dir that stands in for `/`, so it maps absolute paths to `root/<path>`), `calls: list[tuple[list[str], bool]]`, and `responses: dict[tuple[str, ...], Result]` keyed by argv-prefix (longest match wins, default `Result(0, "", "")`). File methods act on the mapped temp paths and record modes in `modes: dict[Path, int]`. Also `auth_mode: str = "ok"`, with `"password"` making `doas -n` report `Authentication required`, and `suspend_count: int`. Later tasks build their fakes from this, using `Layout(etc=root/"etc", home=root/"home/u", user="u")`.

- [ ] **Step 1: Failing tests** in `test_vmctl.py`:
  - `test_parse_status_stopped_fixture`: 3 rows. Row 0 is `VmStatus(1, None, 4, "8.0G", None, None, "kixz:kixz", "stopped", "debian")`, and `owner_user == "kixz"`.
  - `test_parse_status_mixed_fixture`: the running row has pid 51234 and tty `ttyp3`; the paused row has `owner_user == "root"`; the blank and warning lines are skipped (2 rows).
  - `test_parse_status_empty_header_only`: returns `[]`.
- [ ] **Step 2: Failing tests** in `test_system.py`. They use a `System` subclass whose `_spawn(argv, capture: bool) -> Result` is scripted, so put the subprocess call behind `_spawn`:
  - `test_root_run_uses_doas_n`: argv sent is `["doas", "-n", "vmctl", "reload"]`.
  - `test_root_falls_back_to_interactive_when_auth_required`: the first spawn returns rc 1 with err `"doas: Authentication required"`. Second argv is `["doas", "vmctl", "reload"]`, and it ran inside `suspend` (a counter-incrementing context manager).
  - `test_root_failure_raises_with_detail`: rc 1 with err `"boom"` raises VmmError with `.detail == "boom"`.
  - `test_doas_state_maps_messages`: covers ok, password, denied, and missing (`shutil.which` patched to None).
- [ ] **Step 3:** Run both test files. Expected: FAIL.
- [ ] **Step 4:** Implement `system.py`, `vmctl.py` and `tests/fakes.py`. `parse_status` splits each line on whitespace and accepts only lines with exactly 9 fields whose first field is a digit. In each column, `-` means None.
- [ ] **Step 5:** Run the tests. Expected: PASS. Commit: `"Add System runner and vmctl wrappers"`.

### Task 4: vm.conf block operations

**Files:** Create `vmmanager/confblock.py`, `tests/test_confblock.py`, `tests/fixtures/vm.conf.box` (copy of `/tmp/claude-1000/-home-kixz-Private-mygit-vmm-manager/79b6cb39-3f3b-42e5-827b-cacce3eda31b/scratchpad/vm.conf.orig`, the author's real file), `tests/fixtures/vm.conf.tricky`

`vm.conf.tricky` must contain all of these:
- a macro `ramdisk="/bsd.rd"` and a VM `macro_vm` using `boot $ramdisk`
- a VM `nested` with `interface { switch "uplink" }` plus `local interface`
- an instance `vm "nested" instance "inst" { ... }`
- a comment containing `{` and the text `vm "fake" {`
- a VM name given unquoted: `vm plain { memory 1G }`
- a `switch "uplink" { interface bridge0 }`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class BlockFacts: memory: str | None; cpus: int | None; cdrom: str | None; disks: list[str]
      owner: str | None; local_interfaces: int; other_interfaces: bool; uses_macros: bool; is_instance: bool
  def find_block(text: str, name: str) -> tuple[int, int] | None   # [start, end) incl. trailing newline
  def facts(block: str) -> BlockFacts
  def render_block(name: str, *, memory: str, cpus: int, disk: Path, cdrom: str | None, internet: bool, owner: str) -> str
  def edit_block(block: str, changes: dict[str, object]) -> str    # keys: "memory", "cpus", "cdrom" (None = remove), "internet" (bool)
  def comment_out(text: str, span: tuple[int, int], marker: str) -> str
  def replace_disks(block: str, mapping: dict[str, str]) -> str
  def ensure_owner(block: str, user: str) -> str                   # adds "  owner <user>" before closing } if absent
  ```
- [ ] **Step 1: Failing tests:**
  - `test_find_block_box_fixture`: the span for `debian` starts with `vm "debian" {` and its text ends with `}\n`. `debian-12` is found separately; `debian` doesn't match `debian-12`.
  - `test_find_block_ignores_comments_and_nested_braces`: in the tricky fixture, `fake` returns None, and `nested`'s span includes the inner `{ switch "uplink" }` and ends at its own closing brace.
  - `test_find_block_unquoted_name`: `plain` is found.
  - `test_facts_box_debian`: memory `8G`, cpus 4, disks `["/home/kixz/VM/deb_11.qcow2"]`, owner `kixz:kixz`, local_interfaces 1, other_interfaces False, uses_macros False, is_instance False.
  - `test_facts_flags`: `macro_vm` has uses_macros True; `inst` has is_instance True; `nested` has other_interfaces True.
  - `test_render_block_exact`: `render_block("deb", memory="1G", cpus=1, disk=Path("/h/u/vmmanager/deb/deb.qcow2"), cdrom="/h/u/i.iso", internet=True, owner="u")` must equal exactly:
    ```
    vm "deb" {
      memory 1G
      cpus 1
      disable
      disk "/h/u/vmmanager/deb/deb.qcow2"
      cdrom "/h/u/i.iso"
      local interface
      owner u
    }
    ```
    With `internet=False` the `local interface` line is absent; with `cdrom=None` the `cdrom` line is absent.
  - `test_edit_block_line_level_keeps_unknown_lines`: editing `nested` with `{"memory": "2G"}` changes only the memory line, so the diff is exactly that one line.
  - `test_edit_block_internet_toggle_and_cdrom_remove`: on `debian`, `{"internet": False, "cdrom": None}` removes `local interface`. `{"internet": True}` on a block without one inserts `  local interface` before `owner`.
  - `test_edit_block_adds_missing_cpus`: the box's `openbsd` block has no cpus line, so `{"cpus": 2}` inserts `  cpus 2` after `memory`.
  - `test_comment_out_prefixes_every_line`: every line in the span starts with the marker, and the text outside it is unchanged.
  - `test_replace_disks_and_ensure_owner`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. Use a small tokenizer that skips `#` comments to end of line, treats `"..."` as one token, and counts braces. `find_block` matches the token sequence `vm <name> {` or `vm <parent> instance <name> {`. `facts` and `edit_block` work on lines (each statement sits on its own line in blocks the tool writes and in the user's file). If a block has a statement it can't place, `edit_block` raises `VmmError`.
- [ ] **Step 4:** Run the tests. Expected: PASS. Commit: `"Add vm.conf block operations"`.

### Task 5: Flat TOML I/O

**Files:** Create `vmmanager/tomlio.py`, `tests/test_tomlio.py`

**Interfaces:**
- Produces: `dump(data: dict[str, str | int | bool]) -> str`, `load(path: Path) -> dict` (returns `{}` if the file is missing), `save(path: Path, data: dict) -> None` (writes a temp file in the same dir, then `os.replace`).
- Extras keys: `iso`, `os`, `notes`, `created` (ISO-8601 UTC), `version`, `migrated_from` (optional). State keys: `man_installed_version`, `man_declined` (bool).
- [ ] **Step 1: Failing tests:** `test_roundtrip_through_tomllib` with notes containing `"`, `\`, a newline and unicode; `dump` sorts keys; `test_load_missing_is_empty`.
- [ ] **Step 2:** Run. FAIL. **Step 3:** Implement `dump` with `json.dumps(value, ensure_ascii=False)` for strings (a JSON string is a valid TOML basic string), and `str(v).lower()` for bools. **Step 4:** Run. PASS. Commit: `"Add flat TOML I/O"`.

### Task 6: RootTransaction

**Files:** Create `vmmanager/rootwrite.py`, `tests/test_rootwrite.py`

**Interfaces:**
- Consumes: `System.read_file/install_file/install_dir/remove_file`.
- Produces:
  ```python
  class RootTransaction:
      def __init__(self, sys: System, check: Callable[[], str | None]): ...
      def write(self, path: Path, content: str) -> None   # stage; mode = existing file's mode, else 0o644
      def delete(self, path: Path) -> None
      def commit(self) -> None   # apply in staging order → check() → on error restore all → raise VmmError("Configuration check failed; nothing was changed", detail=<check output>)
  ```
  The previous content is captured with `read_file` when a file is staged, and missing parent dirs are created with `install_dir`. On rollback, a file that existed is reinstalled with its previous content and mode; a new file is removed. Created dirs are left in place.
- [ ] **Step 1: Failing tests** (FakeSystem):
  - `test_commit_installs_with_modes`: a new file gets mode 0o644; an existing 0o600 file keeps 0o600.
  - `test_commit_rolls_back_on_check_failure`: `check` returns `"bad line 3"`. All staged files are byte-identical to before, the new file is gone, and the raised VmmError has `.detail == "bad line 3"`.
  - `test_install_failure_midway_rolls_back_earlier_files`: the second `install_file` raises, and the first file is restored.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). Commit: `"Add RootTransaction with rollback"`.

### Task 7: Inventory

**Files:** Create `vmmanager/inventory.py`, `tests/test_inventory.py`

**Interfaces:**
- Consumes: `vmctl.status`, `confblock.find_block/facts`, `Layout`.
- Produces:
  ```python
  @dataclass class VmEntry:
      name: str; status: VmStatus | None; owner: str; managed: bool; mine: bool
      facts: BlockFacts | None; conf_path: Path | None
      state: str          # property: status.state or "not loaded"
      internet: bool      # property: facts.local_interfaces > 0
      actions: frozenset[str]   # property, rules below
      reason: str | None  # property: greyed-row explanation
  def load_inventory(sys: System, layout: Layout) -> list[VmEntry]   # sorted by name
  ```
  - Managed means the file `layout.etc_dir/<user>/<name>.conf` exists. `owner` is that `<user>` for managed VMs, else `status.owner_user`.
  - Facts for a managed VM come from its conf file. For an unmanaged VM, they come from `find_block` in `/etc/vm.conf`, or None if it isn't found there.
  - Actions:
    - not mine → `∅`, reason `"owned by <owner>"`.
    - unmanaged and mine → `{"migrate"}` if (facts and (facts.owner is None or owner_user(facts.owner) == me)), else `∅`. Reason: `"not managed by vmmanager — press m to migrate"`, or `"defined outside /etc/vm.conf — see vmmanager(8) MIGRATING"` when the block isn't found.
    - managed, mine, stopped or not loaded → `{"start","run","update","delete"}`.
    - running → `{"connect","stop"}`.
    - paused → `{"stop"}`.
- [ ] **Step 1: Failing tests:**
  - `test_box_vms_are_unmanaged_and_migratable`: uses the stopped fixture and `vm.conf.box`, with user `kixz`. All 3 have `actions == {"migrate"}`.
  - `test_other_users_vm_read_only`.
  - `test_managed_running_actions`.
  - `test_managed_conf_without_status_entry`: there's a conf file with no status row. The entry is listed with state `"not loaded"` and no exception is raised.
  - `test_unmanaged_block_in_other_file_has_reason`.
- [ ] **Step 2–4:** FAIL, implement, PASS. Commit: `"Add VM inventory"`.

### Task 8: Operations

**Files:** Create `vmmanager/ops.py`, `tests/test_ops.py`

**Interfaces:**
- Consumes: Tasks 2–7.
- Produces:
  ```python
  @dataclass class VmSpec: name: str; memory: str = "1G"; cpus: int = 1; disk_size: str = "20G"
      iso: str | None = None; internet: bool = True; os_label: str = ""; notes: str = ""
  def vm_conf_check(sys, layout) -> str | None          # vmctl.check_config(sys, layout.vm_conf)
  def aggregate_text(layout) -> str                     # one include line per etc_dir/*/*.conf, sorted, header comment
  def with_include(vm_conf_text: str, layout) -> str    # appends "\n# added by vmmanager\n" + include_line + "\n" if absent
  def create_vm(sys, layout, spec: VmSpec) -> None
  def update_vm(sys, layout, name: str, changes: dict[str, object]) -> None
  def delete_vm(sys, layout, name: str) -> None         # config only
  def remove_vm_files(layout, name: str) -> None        # shutil.rmtree(vm_dir)
  def disk_problems(block: str, owner: str) -> list[str]
  def start_vm(sys, layout, entry: VmEntry) -> None     # disk_problems → VmmError, else vmctl.start
  def run_vm(sys, layout, entry: VmEntry) -> int        # same check, then vmctl.run_console
  ```
  - `create_vm` order:
    1. validate the name, `quote(iso)` and the spec;
    2. refuse if `vm_conf_check` already fails (`"vm.conf already has errors; fix them first"` + detail);
    3. refuse if the name is in `vmctl.status` or any `etc_dir/*/<name>.conf` exists (`"name '<n>' is already used by <owner>"`);
    4. `vm_dir.mkdir(parents=True)`, `create_disk`, `tomlio.save(extras)`;
    5. tx.write the system_conf (`render_block`), the aggregate and the vm.conf with the include;
    6. tx.commit (check = `vm_conf_check`);
    7. `vmctl.reload`.
    Any failure after step 4 → `shutil.rmtree(vm_dir)`, then re-raise.
  - `update_vm`, `delete_vm` and `migrate_vm` (Task 10) refuse the same way as create step 2 when `vm_conf_check` already fails. Put the shared helper `require_clean_config(sys, layout) -> None` here.
  - `update_vm`: the VM must be stopped or not loaded; apply `edit_block` to the system_conf text; tx.commit; reload; and update extras `iso` when `cdrom` changes.
  - `delete_vm`: stopped; tx.delete the system_conf and rewrite the aggregate; commit; reload.
  - `disk_problems`: for every disk and the cdrom in `facts(block)`, use `os.lstat`. Report a problem if the path is missing, is not a regular file (this catches symlinks and devices), or isn't owned by the owner's uid (`pwd.getpwnam`) or uid 0. Root-owned disks are allowed because the author's existing disks are `root:kixz 0600`.
- [ ] **Step 1: Failing tests** (FakeSystem; `vmctl status` and `vmd -n` scripted via `responses`):
  - `test_create_writes_all_files`: conf text equals the `render_block` output; the aggregate contains `include "<etc>/vmmanager/u/deb.conf"`; vm.conf gets exactly one include line appended and nothing else changes; reload is called; extras has `created` and `version`.
  - `test_create_twice_include_added_once`.
  - `test_create_rejects_taken_name_names_owner`.
  - `test_create_rejects_iso_path_with_quote`: no files are created.
  - `test_create_rolls_back_everything_when_vmd_check_fails`: `vmd -n` returns rc 1. `/etc` is byte-identical, the vm_dir is gone, and no reload happens.
  - `test_create_refuses_when_config_already_broken`.
  - `test_update_changes_only_memory_line`.
  - `test_update_refuses_running_vm`.
  - `test_delete_removes_conf_and_aggregate_line_keeps_folder`.
  - `test_disk_problems_symlink_device_missing_wrong_owner`: uses real temp files and a symlink.
  - `test_start_refuses_on_disk_problem`.
- [ ] **Step 2–4:** FAIL, implement, PASS. Commit: `"Add VM operations"`.

### Task 9: Network setup

**Files:** Create `vmmanager/network.py`, `tests/test_network.py`

**Interfaces:**
- Produces:
  ```python
  ANCHOR_RULES = ("match out on egress inet from 100.64.0.0/10 to any nat-to (egress)\n"
                  "pass in quick inet proto { udp tcp } from 100.64.0.0/10 to any port domain rdr-to 127.0.0.1 port domain\n")
  PF_LINES = 'anchor "vmmanager"\nload anchor "vmmanager" from "/etc/vmmanager/pf.conf"\n'  # path from layout.pf_anchor
  @dataclass class NetStatus: anchor_in_pf_conf: bool; anchor_loaded: bool; forwarding: bool; resolver: bool
      ok: bool   # property: all four
  def net_status(sys, layout) -> NetStatus
  def setup_explanation(st: NetStatus) -> str   # bullet list of only the missing pieces, plain words, for the dialog
  def setup_network(sys, layout) -> None
  ```
  - `net_status` checks:
    - `anchor_in_pf_conf`: `read_file(pf_conf)` contains `anchor "vmmanager"`.
    - `anchor_loaded`: root `pfctl -a vmmanager -sr` output is non-empty.
    - `forwarding`: `sysctl -n net.inet.ip.forwarding` == `"1"`.
    - `resolver`: `rcctl check unwind` or `rcctl check unbound` has rc 0.
  - `setup_network` steps, each only if missing:
    1. In one tx with check = root `pfctl -nf <pf_conf>` (its stderr becomes the detail): write the anchor file, and the pf.conf with `"\n# added by vmmanager\n" + PF_LINES` appended. Commit, then root `pfctl -f <pf_conf>`.
    2. If forwarding is off: root `sysctl net.inet.ip.forwarding=1`. Write sysctl.conf with the line appended (in a tx with no check), unless an uncommented `net.inet.ip.forwarding=1` line is already there.
    3. If there's no resolver: root `rcctl enable unwind`, then `rcctl start unwind`.
- [ ] **Step 1: Failing tests:**
  - `test_status_all_missing`.
  - `test_setup_appends_pf_lines_once_and_loads`: the pf.conf mode stays 0o600, and `pfctl -f` comes after a successful `-nf`.
  - `test_setup_rolls_back_pf_conf_when_pfctl_n_fails`.
  - `test_setup_skips_present_pieces`: with forwarding already on, sysctl.conf is unchanged.
  - `test_explanation_lists_only_missing`.
- [ ] **Step 2–4:** FAIL, implement, PASS. Commit: `"Add host NAT network setup"`.

### Task 10: Migration

**Files:** Create `vmmanager/migrate.py`, `tests/test_migrate.py`

**Interfaces:**
- Produces:
  ```python
  def migration_blockers(sys, layout, entry: VmEntry) -> list[str]
  def migrate_vm(sys, layout, entry: VmEntry) -> None
  def qcow2_has_backing(sys, path: Path) -> bool
  MANUAL_STEPS: str   # short numbered manual procedure; ends "See vmmanager(8), MIGRATING."
  ```
  - Blockers, each a fixed sentence:
    - the VM is running or paused
    - the block isn't found in vm.conf
    - `uses_macros`
    - `is_instance`
    - owner is another user
    - the name is already managed
    - any disk is missing
    - a disk is qcow2 with a backing file
    - `os.stat(disk).st_dev != os.stat(layout.home).st_dev`
    - the disk's parent dir isn't writable (`os.access(W_OK)`)
    - the vm_dir already exists
  - `qcow2_has_backing`: read the first 16 bytes (directly, or via root `od -An -tx1 -N16` on PermissionError). It returns True when the magic is `QFI\xfb` and bytes 8–15 (backing_file_offset, big-endian) are non-zero.
  - `migrate_vm`:
    1. blockers → raise `VmmError("Can't migrate <name> automatically", detail="\n".join(blockers) + "\n\n" + MANUAL_STEPS)`;
    2. mkdir the vm_dir and `os.rename` each disk to `vm_dir/<basename>`;
    3. new block = `ensure_owner(replace_disks(block, mapping), user)`;
    4. tx.write the system_conf, the aggregate, and vm.conf = `with_include(comment_out(text, span, f"# moved to vmmanager {date.today()}: "))`;
    5. commit;
    6. reload;
    7. save extras with `migrated_from` = the old disk paths joined by `", "`.
    On any failure after step 2: rename disks back, rmdir the vm_dir, re-raise.
- [ ] **Step 1: Failing tests:**
  - `test_box_debian_migrates`: real temp files stand in for disks under `root/home/u/VM`, and the block's disk paths are rewritten to them. Checks: the disk moved, the conf has the new path and owner, vm.conf has the debian block commented with the marker plus the include line, and the other two blocks are untouched.
  - `test_each_blocker`: one sub-test per blocker sentence.
  - `test_qcow2_backing_detection`: synthetic 16-byte headers.
  - `test_rollback_moves_disks_back_when_check_fails`.
- [ ] **Step 2–4:** FAIL, implement, PASS. Commit: `"Add migration of hand-written VMs"`.

### Task 11: Health check

**Files:** Create `vmmanager/health.py`, `tests/test_health.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass class Health:
      socket_ok: bool; vmd_running: bool; firmware: bool; doas: str
      config_error: str | None; broken: list[str]      # managed VM names named in config_error
      net: NetStatus | None                            # only computed if any managed mine VM has internet
      blocking: bool    # property: not socket_ok
  def check(sys, layout, entries: list[VmEntry]) -> Health
  def broken_vms(error: str, layout) -> list[str]      # parse "vmd: <etc_dir>/<user>/<name>.conf:<line>: ..." → name
  def remove_broken(sys, layout, name: str) -> None    # tx.delete conf + rewrite aggregate; check = vm_conf_check; commit; reload only if vmd running
  def enable_vmd(sys) -> None                          # root rcctl enable vmd; rcctl start vmd
  def fetch_firmware(sys) -> int                       # interactive root fw_update vmm
  ```
  - `socket_ok`: `os.access("/var/run/vmd.sock", os.R_OK | os.W_OK)`.
  - `vmd_running`: `rcctl check vmd` rc 0.
  - `firmware`: `Path("/etc/firmware/vmm-bios").exists()`.
- [ ] **Step 1: Failing tests:** `test_broken_vm_detected_from_vmd_error` uses the real message shape seen on the box: `vmd: /etc/vmmanager/u/deb.conf:4: failed to parse disks: /home/u/vmmanager/deb/deb.qcow2`. Also `test_net_not_checked_without_internet_vms`, `test_remove_broken_rewrites_aggregate`, `test_blocking_when_no_socket`.
- [ ] **Step 2–4:** FAIL, implement, PASS. Commit: `"Add startup health check"`.

### Task 12: Man pages and CLI flags

**Files:** Create `vmmanager/man/vmmanager.8`, `vmmanager/man/vmmanager.toml.5`, `vmmanager/manpages.py`, `tests/test_manpages.py`; modify `vmmanager/cli.py`.

**Interfaces:**
- Produces:
  ```python
  MAN_DIR = Path("/usr/local/man")
  def bundled() -> list[Path]                # importlib.resources.files("vmmanager") / "man" / *
  def view(sys, page: str = "vmmanager.8") -> int   # interactive man -l <bundled path>
  def install(sys) -> None                    # install_file each → MAN_DIR/man<N>/<file>, mode 0o644; root makewhatis /usr/local/man
  def should_offer(layout, doas_state: str) -> bool
  def record(layout, installed: bool) -> None # state.toml: man_installed_version or man_declined
  ```
  - `should_offer`: False if doas_state is not in {"ok","password"}, or if `man_declined`. True if `man_installed_version` is missing or != `__version__`.
  - `install` and `view` take their files straight from the package, so pip wheels must include `man/*`. Task 1's package-data covers that.
- Man page content (mdoc, `Dd`/`Dt VMMANAGER 8`/`Os`). Sections:
  - NAME, SYNOPSIS (`vmmanager [--version | --man | --install-man]`)
  - DESCRIPTION
  - KEYS (the bindings from Task 13)
  - FILES (every path in spec Layout + Networking)
  - PRIVILEGES (doas; persist recommended; what needs root)
  - NETWORKING (exactly what is changed, why, how to undo; how to remove old hand-written NAT lines)
  - MIGRATING (automatic steps, refusal reasons, the manual procedure)
  - SECURITY (owner rule; why disks must be regular files)
  - HEALTH CHECK, UNINSTALL (`pipx uninstall vmmanager`, `doas rm /usr/local/man/man8/vmmanager.8 /usr/local/man/man5/vmmanager.toml.5 && doas makewhatis /usr/local/man`, then how to remove `/etc/vmmanager` and the include and anchor lines)
  - SEE ALSO (vmctl 8, vmd 8, vm.conf 5, pf.conf 5, doas.conf 5, unwind 8)
  `vmmanager.toml.5` documents the extras and state keys from Task 5.
- [ ] **Step 1: Failing tests:** `test_bundled_pages_present`, `test_should_offer_matrix` (declined / same version / older version / denied), and `test_install_targets_and_makewhatis` (FakeSystem). In test_cli: `test_man_flag_calls_view` (patched).
- [ ] **Step 2–4:** FAIL, implement, PASS. Wire up `--man` (works on any OS) and `--install-man` in `cli.main`.
- [ ] **Step 5:** Lint on the box: `scp vmmanager/man/* testbox:/tmp/ && ssh testbox 'mandoc -Tlint -Wwarning /tmp/vmmanager.8 /tmp/vmmanager.toml.5'`. Expected: no output.
- [ ] **Step 6:** Commit: `"Add man pages and --man/--install-man"`.

### Task 13: Textual UI

**Files:** Create `vmmanager/ui/__init__.py`, `vmmanager/ui/app.py`, `vmmanager/ui/forms.py`, `vmmanager/ui/dialogs.py`, `tests/test_ui.py`; modify `vmmanager/cli.py` (launch the app).

**Interfaces:**
- Consumes: everything above.
- Produces: `VmmApp(App)` with `__init__(self, sys: System, layout: Layout)`, plus `cli.main([])` → `VmmApp(System(), Layout()).run()`. In `on_mount` it sets `sys.suspend = self.suspend`.
- Behaviour:
  - **Main screen:** `DataTable` with columns Name, State, CPUs, Memory, Internet, Owner. Rows whose `actions` are empty or `{"migrate"}` render dim, and the selected row's `reason` appears in a one-line status bar above the footer. Refresh with `set_interval(3, ...)` via `load_inventory`.
  - **Key bindings** (shown in the Footer; an action not in `entry.actions` shows the reason with `notify` instead of running):
    `n` create, `s` start, `r` run, `o` connect, `x` stop (asks "Force stop?" if the VM is still running after 2 refreshes), `e` update, `d` delete, `m` migrate, `?` view man page, `q` quit.
  - **Root actions** (create/update/delete/migrate) are disabled with the notify text "needs doas — ask an admin" when `doas_state()` is "denied" or "missing".
  - **VmFormScreen(mode="create"|"update", entry=None):**
    - Inputs: Name (create only), Memory, CPUs, Disk size (create only), ISO (Input plus a "Browse…" button that opens `IsoPickerScreen`, a `DirectoryTree` filtered to `*.iso`/`*.img`, starting at home), Internet `Switch`, OS label, Notes.
    - Validate on submit with `validate_name` and `quote`, showing inline errors.
    - In update mode, block lines outside the editable set appear in a read-only `Static` (the "kept as written" lines).
  - **After create:** if internet is on and `net_status` isn't ok, show `ConfirmScreen` with `setup_explanation`. On yes, run `setup_network`. Then offer "Run now (attach console to install)?".
  - **Delete:** `ConfirmScreen("Delete <name>?")` → `delete_vm`, then `ConfirmScreen("Also delete ~/vmmanager/<name>/ and its disk?")` → `remove_vm_files`.
  - **Migrate:** `ConfirmScreen` explaining the 4 edits (spec, Unmanaged VMs and migration) → `migrate_vm`. On VmmError, show `MessageScreen(err.message, err.detail)`. On success, if the migrated VM has internet and `net_status` isn't ok, offer network setup, the same as after create.
  - **Update:** turning Internet on runs the same network offer.
  - **Startup:** run `health.check`. If `blocking`, a `HealthScreen` explains `socket owner` and offers only quit. Other problems get one `HealthScreen` listing each with a fix button (enable vmd, fetch firmware, remove broken config, set up networking). Then, if `manpages.should_offer`, a `ConfirmScreen` "Install man pages system-wide? (needs doas)" → `install`/`record`.
  - Every `VmmError` raised by an action becomes a `MessageScreen`. The UI never shows a traceback.
- [ ] **Step 1: Failing tests** with Textual's `App.run_test()` pilot, a FakeSystem and the box fixtures:
  - `test_lists_box_vms_dimmed_with_migrate_reason`
  - `test_create_flow_calls_create_vm`: fill the form, submit, and check that `create_vm` was called with the expected `VmSpec` (patched)
  - `test_start_disabled_for_unmanaged_notifies_reason`
  - `test_vmm_error_shows_message_screen`
  - `test_blocking_health_shows_quit_only`
- [ ] **Step 2–4:** FAIL, implement, PASS. Run the full suite: `venv/bin/python -m unittest discover -s tests -v`, all PASS.
- [ ] **Step 5:** Commit: `"Add Textual UI"`.

### Task 14: Live acceptance on the OpenBSD box

Needs the user: answer doas prompts through a pty. Restore anything this task creates for testing.

- [ ] **Step 1:** Build and install on the box:
  - local: `venv/bin/python -m build`
  - then: `scp dist/*.whl testbox:/tmp/`
  - on the box: `pipx install --force /tmp/vmmanager-0.1.0-py3-none-any.whl`
  Expected: `vmmanager --version` prints `vmmanager 0.1.0`.
- [ ] **Step 2:** Confirm the doas messages that `doas_state` relies on (`doas -n true` as kixz, and as a user who isn't in doas.conf). Fix `system.py` if they differ, and re-run Task 3's tests.
- [ ] **Step 3: Owner test (spec open item).**
  1. `doas useradd -m vmtest`.
  2. Create a VM owned by `vmtest` through the tool, logged in as kixz with owner overridden in a scratch conf. Alternatively, write `/etc/vmmanager/vmtest/t.conf` by hand, plus the aggregate, then reload.
  3. As `vmtest`: `vmctl start t`, `vmctl stop t`, `vmctl console t`. Before this, add vmtest to a `socket owner` group in a scratch copy of vm.conf, then restore.
  4. Record the result in the vault. If owner doesn't grant non-root control, apply the spec's fallback: those actions go through doas too.
  5. Clean up: `doas userdel -r vmtest`, remove the test files, reload.
- [ ] **Step 4: Create/run/delete through the TUI.**
  1. Create `vmmtest` (512M, 1 cpu, 2G disk, ISO `/tmp/install80.iso`; fetch it from the mirror in `/etc/installurl` if missing, and remove it afterwards). Accept the network setup.
  2. Check: `doas pfctl -a vmmanager -sr` shows 2 rules, `doas pfctl -nf /etc/pf.conf` passes, and the include line is in `/etc/vm.conf`.
  3. Run it, then in the installer shell (`S`) check that `ping -c1 1.1.1.1` works, and that `nslookup`-equivalent DNS through `100.64.<id>.2` resolves. In the OpenBSD installer, `ftp -o /dev/null http://cdn.openbsd.org/` tests DNS plus NAT.
  4. Leave with `~.`, stop, update memory to 1G, delete it including the folder.
  Expected: every step succeeds and `vmd -n` passes at the end.
- [ ] **Step 5: Migration of the author's 3 VMs.** Ask the user for explicit confirmation first, since it moves their disks from `~/VM`. Migrate `openbsd`, `debian` and `debian-12` one by one. Each must start afterwards, and `/etc/vm.conf` must show the commented blocks. If the user says no, test migration on a scratch VM block instead.
- [ ] **Step 6:** Run `mandoc -Tlint` and `vmmanager --install-man`, then check that `man vmmanager` and `apropos vmmanager` both work.
- [ ] **Step 7:** Record results and anything learned in the vault through the `scribe` agent (project note Threads and memory files). Commit any fixes, each with a regression test.

### Task 15: Release prep (no publish)

**Files:** Create `publish_pypi.sh` (copy wifzf's, s/wifzf/vmmanager/, and add an OpenBSD branch: on OpenBSD, take build/twine from `pkg_add py3-build py3-twine`, like the FreeBSD branch). Modify `README.md` to cover install, first run (what gets asked and why), the key table, and pointers to `man vmmanager`. No screenshot for now.

- [ ] **Step 1:** Run `venv/bin/python -m build && venv/bin/python -m twine check dist/*`. Expected: `PASSED` for both files.
- [ ] **Step 2:** Run `reviewer` agent on the whole branch. Fix findings.
- [ ] **Step 3:** Commit: `"Release prep: publish script and README"`. Publishing to PyPI is left to the user (manual, from their laptop, as with wifzf).
