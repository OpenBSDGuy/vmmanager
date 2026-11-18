# Folder share (key `f`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Share one host folder per VM over NFS, limited to that VM, kept right across host reboots, with no doas on start/stop.

**Architecture:** A stdlib-only script `vmmanager/sync_exports.py` computes vmmanager's `/etc/exports` lines from `vmctl status` and root-owned `/etc/vmmanager/<user>/<vm>.share` files, and rewrites only its own marked lines under a shared flock. The same file is installed as `/etc/vmmanager/sync-exports` and run as root from `/etc/rc.local` and via doas from the TUI; the TUI imports its pure functions to detect stale exports without root. `share.py` holds the one-time setup and share/unshare ops, like `network.py`.

**Tech Stack:** Python >=3.11 stdlib, Textual (existing), unittest, mandoc.

**Spec:** `docs/superpowers/specs/2026-11-18-folder-share-design.md`

## Global Constraints

- Python >=3.11; `sync_exports.py` imports only the stdlib (it runs under root's `/usr/local/bin/python3`, never from the user's venv).
- Lock: `fcntl.flock(LOCK_EX)` on `/var/run/exports.lock`; temp file `/etc/exports.vmmanager`; then `pkill -HUP -x mountd`. Never touch lines vmmanager did not write.
- Marker line: `# vmmanager: the next line is managed by vmmanager (<vm>)`; export line: `<folder> -mapall=<user> 100.64.<id>.3`.
- Share file: `/etc/vmmanager/<user>/<vm>.share`, root:wheel 0644, one line = absolute folder path. Sync script: `/etc/vmmanager/sync-exports`, root:wheel 0755. rc.local line: `/usr/local/bin/python3 /etc/vmmanager/sync-exports` under `# added by vmmanager`.
- Folder rule: absolute, existing directory, none of whitespace, `#`, `\`, `"`.
- nfsd flags `-t -n 4`; daemons `portmap mountd nfsd` enabled with rcctl.
- New anchor line: `block return in quick on ! lo0 proto { tcp udp } from ! 100.64.0.0/10 to port { sunrpc nfsd }`.
- Key `f`, footer "Share"; `Shared` column last, width 30, folder only, wraps.
- Every root change is announced by a confirm dialog naming the files it changes (existing UI rule).
- CI: `python -m unittest discover -s tests -v`, `python -m py_compile vmmanager/*.py vmmanager/ui/*.py`, `mandoc -Tlint -Wwarning vmmanager/man/*`. Run tests locally with `venv/bin/python -m unittest discover -s tests`.

## Review Focus

- A `.share` file whose folder was deleted or renamed later: sync skips it and reports it on stderr; other VMs' lines are still written (test in Task 1).
- `/etc/exports` with the admin's own lines, dockerctl's marked pair, and no trailing newline: all kept byte-for-byte (test in Task 1).
- A shared VM whose internet is turned off: no export line (its id has no link); turning it back on restores it (tests in Tasks 1 and 4).
- Share/unshare when the sync fails (e.g. Ctrl-C at doas): the `.share` change is rolled back (test in Task 2).
- An old install whose anchor lacks the new pf line is detected as "networking not set up" (test in Task 3).

---

### Task 0: Box checks before any code (manual, on the test box)

Spec section "To verify on the box". The box is the test box; ask the user for the password, never store it; helpers in `.superpowers/helpers/`. Check the box isn't in use first. Back up `/etc/exports` and `/etc/pf.conf` to `~/share-backup/` and restore after.

- [ ] **Step 1:** Add a marked vmmanager line for debian-12 (`/home/kixz/share -mapall=kixz 100.64.<id>.3`, id from `vmctl status`) next to dockerctl's `/home/kixz` line; `pkill -HUP -x mountd`. Expected: `showmount -e localhost` lists both; `/var/log/daemon` has no mountd parse error.
- [ ] **Step 2:** Load the new anchor line into the vmmanager anchor (`pfctl -a vmmanager -f` with the two old lines plus the new one). Expected from the laptop: `nc -z -w3 <box> 111` and `2049` fail; `rpcinfo -p localhost` on the box still lists mountd; dockerctl `docker run --rm -v /home/kixz:/x alpine ls /x` works.
- [ ] **Step 3:** The user starts debian-12, logs in on its console and runs the Linux "mount now" command from Task 2's `guest_commands`. Expected: the folder mounts; a file created in the guest is `kixz:kixz` on the host. Then the user adds the `@reboot` line, reboots the guest, and checks it is mounted again.
- [ ] **Step 4:** If any step fails, stop and report to the user (spec "If a box check fails"). Restore `/etc/exports` and the anchor (`pfctl -f /etc/pf.conf`). Record results in the vault note.

### Task 1: `sync_exports.py` — the root sync script

**Files:**
- Create: `vmmanager/sync_exports.py`
- Test: `tests/test_sync_exports.py`

**Interfaces:**
- Produces:
  - `MARK = "# vmmanager: the next line is managed by vmmanager"`
  - `valid_folder(path: str) -> bool` (syntax only: absolute, no whitespace/`#`/`\`/`"`)
  - `parse_ids(status_text: str) -> dict[str, int]` (name → id from `vmctl status` output)
  - `wanted_lines(etc_dir: Path, ids: dict[str, int]) -> tuple[list[str], list[str]]` → (export text lines incl. marker lines, problems)
  - `exports_with(text: str, lines: list[str]) -> str` (drop every vmmanager marker + following line, keep the rest as is, append `lines`)
  - `sync(etc_dir: Path, exports: Path, lock: Path, status_text: str) -> list[str]` (locks, rewrites if changed, HUPs mountd, returns problems)
  - `main() -> int` (runs `vmctl status`, calls `sync(Path("/etc/vmmanager"), Path("/etc/exports"), Path("/var/run/exports.lock"), …)`, prints problems to stderr, returns 0)
  - The file starts with `#!/usr/local/bin/python3` and ends with `if __name__ == "__main__": sys.exit(main())`.

- [ ] **Step 1: Write the failing tests** (tmp dirs as `etc_dir`; `status_text` from a fixture string like `docs/make_screenshot.STATUS`)

```python
def test_line_per_shared_vm_with_internet(self):
    # etc/kixz/debian-12.conf has "local interface"; etc/kixz/debian-12.share = "/home/kixz/share\n" (dir exists)
    lines, problems = wanted_lines(etc, {"debian-12": 2})
    self.assertEqual(lines, [MARK + " (debian-12)", "/home/kixz/share -mapall=kixz 100.64.2.3"])
    self.assertEqual(problems, [])

def test_no_line_without_local_interface(self): ...       # conf without "local interface" → []
def test_no_line_without_id(self): ...                     # VM missing from ids → []
def test_missing_or_bad_folder_is_a_problem(self):         # deleted dir, and "/a b" → no line, 1 problem each, other VM still gets its line
def test_valid_folder(self):
    for ok in ["/home/kixz/share", "/x"]: self.assertTrue(valid_folder(ok))
    for bad in ["rel/path", "/a b", "/a#b", '/a"b', "/a\\b", "/a\tb", ""]: self.assertFalse(valid_folder(bad))

def test_exports_with_keeps_other_lines(self):
    theirs = ("/data -ro 10.0.0.5\n# dockerctl: the next line is managed by dockerctl\n"
              "/home/kixz -mapall=kixz 100.64.6.3")          # no trailing newline
    old = theirs + "\n" + MARK + " (gone)\n/old -mapall=kixz 100.64.9.3\n"
    new = exports_with(old, [MARK + " (debian-12)", "/home/kixz/share -mapall=kixz 100.64.2.3"])
    self.assertEqual(new, theirs + "\n" + MARK + " (debian-12)\n/home/kixz/share -mapall=kixz 100.64.2.3\n")
    self.assertEqual(exports_with(new, []), theirs + "\n")

def test_sync_unchanged_does_not_rewrite_or_hup(self): ...  # patch subprocess.run; file mtime and calls unchanged
def test_sync_writes_0644_and_hups_mountd(self): ...        # asserts mode 0o644, run(["pkill","-HUP","-x","mountd"])
def test_parse_ids(self): self.assertEqual(parse_ids(STATUS)["debian-12"], 2)
```

- [ ] **Step 2:** Run `venv/bin/python -m unittest tests.test_sync_exports -v`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement the interfaces above. Owner = the share file's parent folder name. "Has internet" = a line matching `^\s*local\s+interface\b` in the sibling `<vm>.conf`. `parse_ids` reads 9-column rows with a digit first column (same rule as `vmctl.parse_status`; duplicated on purpose, the script can't import vmmanager). Temp file = `exports.with_name(exports.name + ".vmmanager")`, `os.replace`.
- [ ] **Step 4:** Run the tests. Expected: PASS. Also `python -m py_compile vmmanager/sync_exports.py`.
- [ ] **Step 5:** Commit: `git commit -m "sync_exports: compute and write vmmanager's /etc/exports lines"`

### Task 2: `share.py` — setup, share/unshare, stale check, guest commands

**Files:**
- Modify: `vmmanager/layout.py` (add `exports`, `rc_local`, `sync_script` properties and `share_file(name, user=None)`)
- Modify: `vmmanager/rootwrite.py` (`write(path, content, mode: int | None = None)`; `None` keeps today's behaviour)
- Create: `vmmanager/share.py`
- Test: `tests/test_share.py`, `tests/test_rootwrite.py`, `tests/test_layout.py`

**Interfaces:**
- Consumes: Task 1 (`valid_folder`, `parse_ids`, `wanted_lines`, `exports_with`, `MARK`).
- Produces (`vmmanager/share.py`):
  - `PYTHON = "/usr/local/bin/python3"`; `RC_LINE = f"{PYTHON} /etc/vmmanager/sync-exports"` (built from `layout.sync_script`)
  - `bundled_script() -> str` (text of `vmmanager/sync_exports.py` via `importlib.resources`)
  - `setup_needed(sys: System, layout: Layout) -> list[str]` (human bullets; empty = ready: script equals bundled, rc.local has `RC_LINE`, `rcctl check` ok for portmap mountd nfsd, network anchor current per Task 3)
  - `setup_sharing(sys: System, layout: Layout) -> None`
  - `folder(layout: Layout, name: str) -> str | None`
  - `set_share(sys: System, layout: Layout, name: str, folder: str | None) -> None` (None = stop sharing)
  - `sync(sys: System, layout: Layout) -> None` (no-op when `layout.sync_script` is missing)
  - `stale(sys: System, layout: Layout, status_text: str) -> bool`
  - `guest_commands(folder: str) -> str`

- [ ] **Step 1: Write the failing tests** (FakeSystem; layout under a tmp root)

```python
def test_set_share_writes_file_and_runs_sync_as_root(self):
    share.set_share(sys, L, "debian-12", str(folder))
    self.assertEqual(L.share_file("debian-12").read_text(), f"{folder}\n")
    self.assertIn(([share.PYTHON, str(L.sync_script)], True), sys.calls)

def test_set_share_rejects_bad_folder(self): ...      # "/a b" and a missing dir → VmmError, nothing written, no root call
def test_set_share_rolls_back_when_sync_fails(self):  # responses[(PYTHON,)] = Result(1, "", "boom") → VmmError; old share file content restored
def test_stop_sharing_deletes_file(self): ...
def test_setup_sharing_order(self):
    # root calls in this order: install script 0755, write rc.local (existing text + "\n# added by vmmanager\n" + RC_LINE + "\n"),
    # rcctl set nfsd flags -t -n 4, rcctl enable portmap mountd nfsd, rcctl start portmap mountd nfsd
def test_setup_creates_rc_local_if_missing(self): ...
def test_setup_needed_lists_each_missing_piece(self): ...
def test_stale_true_when_exports_differ(self): ...    # uses wanted_lines vs marked pairs in L.exports
def test_guest_commands_text(self):
    t = share.guest_commands("/home/kixz/share")
    self.assertIn('mount -t nfs -o vers=3,proto=tcp,nolock "$(/sbin/ip route | awk \'/default/{print $3}\'):/home/kixz/share" /mnt/share', t)
    self.assertIn('mount_nfs -T "$(/sbin/route -n get default | awk \'/gateway:/{print $2}\'):/home/kixz/share" /mnt/share', t)
    self.assertIn("@reboot for i in 1 2 3 4 5 6; do", t)
def test_write_with_mode(self): ...                   # rootwrite: tx.write(p, "x", 0o755) installs with 0o755
```

- [ ] **Step 2:** Run `venv/bin/python -m unittest tests.test_share tests.test_rootwrite tests.test_layout -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. `set_share` validates with `valid_folder` + `os.path.isdir`, then `RootTransaction(sys, check)` writes/deletes the share file where `check` runs the sync via `sys.run([PYTHON, str(layout.sync_script)], root=True, check=False)` and returns its stderr on rc≠0. `setup_sharing` uses one `RootTransaction` for the script (mode 0o755) and rc.local, then the rcctl calls. `guest_commands` returns exactly this text (also used verbatim in the man page, Task 6):

```
Run inside the guest, as root (Debian/Ubuntu need: apt install nfs-common).
The host is the guest's default gateway.

Mount now:
  mkdir -p /mnt/share
  Linux:   mount -t nfs -o vers=3,proto=tcp,nolock "$(/sbin/ip route | awk '/default/{print $3}'):FOLDER" /mnt/share
  OpenBSD: mount_nfs -T "$(/sbin/route -n get default | awk '/gateway:/{print $2}'):FOLDER" /mnt/share

Mount at every boot (add with crontab -e):
  Linux:   @reboot mkdir -p /mnt/share; for i in 1 2 3 4 5 6; do mount -t nfs -o vers=3,proto=tcp,nolock "$(/sbin/ip route | awk '/default/{print $3}'):FOLDER" /mnt/share && break; sleep 10; done
  OpenBSD: @reboot mkdir -p /mnt/share; for i in 1 2 3 4 5 6; do /sbin/mount_nfs -T "$(/sbin/route -n get default | awk '/gateway:/{print $2}'):FOLDER" /mnt/share && break; sleep 10; done
```

  (`FOLDER` replaced by the folder.) Add `vmmanager = ["man/*"]` stays; `sync_exports.py` is already in the package.
- [ ] **Step 4:** Run the tests. Expected: PASS; full suite still passes.
- [ ] **Step 5:** Commit: `git commit -m "share: one-time NFS setup, share/unshare via the root sync"`

### Task 3: pf anchor keeps NFS off the LAN

**Files:**
- Modify: `vmmanager/network.py` (`ANCHOR_RULES` gets the new third line; `net_status` sets `anchor_file` only when the file's text equals `ANCHOR_RULES`; `setup_explanation` wording for an outdated anchor: "Update the vmmanager pf rules (keeps NFS off the local network)")
- Test: `tests/test_network.py`

**Interfaces:**
- Produces: `network.anchor_current(sys: System, layout: Layout) -> bool` (used by `share.setup_needed`).

- [ ] **Step 1: Write the failing tests**

```python
def test_anchor_blocks_nfs_from_outside_vm_network(self):
    self.assertIn("block return in quick on ! lo0 proto { tcp udp } from ! 100.64.0.0/10 to port { sunrpc nfsd }\n", network.ANCHOR_RULES)
def test_old_anchor_counts_as_not_set_up(self): ...   # anchor file = the old two lines → net_status(...).anchor_file is False
def test_setup_rewrites_old_anchor(self): ...         # existing pf.conf line present, old anchor → anchor rewritten, pf.conf not appended twice
```

- [ ] **Step 2:** Run `venv/bin/python -m unittest tests.test_network -v`. Expected: FAIL.
- [ ] **Step 3:** Implement (`setup_network` already rewrites the anchor when its text differs).
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "network: pf anchor blocks portmap and nfsd from outside the VM network"`

### Task 4: Inventory, ops and health

**Files:**
- Modify: `vmmanager/inventory.py` (`VmEntry.shared: str | None = None`, filled for managed VMs from `share.folder`; `actions` adds `"share"` for managed, mine VMs with `internet`, in every state; `reason` for share without internet handled in the UI)
- Modify: `vmmanager/ops.py` (`delete_vm`: also `tx.delete(layout.share_file(name))` when it exists, then `share.sync` after reload; `update_vm`: `share.sync` after reload when `"internet"` in changes)
- Modify: `vmmanager/health.py` (`Health.shares_stale: bool = False`, set when the sync script exists and `share.stale(...)`)
- Test: `tests/test_inventory.py`, `tests/test_ops.py`, `tests/test_health.py`

**Interfaces:**
- Consumes: Task 2 (`share.folder`, `share.sync`, `share.stale`).
- Produces: `VmEntry.shared`, action name `"share"`, `Health.shares_stale`.

- [ ] **Step 1: Write the failing tests**

```python
def test_entry_shared_folder(self): ...               # share file → entry.shared == "/home/kixz/share"
def test_share_action_needs_internet(self): ...       # managed+mine+internet → "share" in actions (stopped AND running); no interface → not
def test_delete_removes_share_and_syncs(self): ...    # share file gone; sync call after the vmctl reload call
def test_update_internet_change_syncs(self): ...      # changes={"internet": False} → sync called; {"memory": "2G"} → not
def test_health_reports_stale_shares(self): ...       # patched share.stale True + script exists → shares_stale True
```

- [ ] **Step 2:** Run `venv/bin/python -m unittest tests.test_inventory tests.test_ops tests.test_health -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. (Create needs no sync: verified on the box that a new VM gets the next id and the others keep theirs; this deviates from the spec's list on purpose.)
- [ ] **Step 4:** Run the full suite. Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "Shares follow delete and internet changes; health flags stale exports"`

### Task 5: UI — `f` key, share dialog, folder picker, Shared column, health fix

**Files:**
- Modify: `vmmanager/ui/app.py` (binding `("f", "share", "Share")` after `m`; `"share"` in `ROOT_ACTIONS`; `_cells` adds `e.shared or ""`; column `Shared` added with `width=30`; rows added with `height=None` so long folders wrap; `action_share`; `_problems`/`_fix` handle `shares_stale` → "Shared folders are out of date" / button "Update shares" → confirm → `share.sync`)
- Modify: `vmmanager/ui/dialogs.py` (`FolderPickerScreen(start: Path)`: directory-only tree, Enter expands, button "Use this folder" and key `u` dismiss with the highlighted directory's path)
- Create: `vmmanager/ui/share.py` (`ShareScreen(name: str, folder: str | None, home: Path)`: dismisses with `("share", folder)`, `("stop", None)` or `None`)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: Tasks 2 and 4 (`share.setup_needed`, `share.setup_sharing`, `share.set_share`, `share.guest_commands`, `VmEntry.shared`, `"share"` action, `Health.shares_stale`).

`action_share` flow: `_ready_for("share")` (without internet the notice is "needs internet on: the share runs over the VM's network link") → `ShareScreen` (input prefilled with `e.shared` or `~/`; the guest commands update as the input changes; "Stop sharing" only when `e.shared`) → if `setup_needed` is non-empty, confirm "Set up folder sharing?" listing its bullets and the files (`/etc/vmmanager/sync-exports`, `/etc/rc.local`, rcctl changes, pf anchor) → `setup_sharing` (and `network.setup_network` when the anchor is outdated) → confirm "Share <folder> with <vm>?" naming `/etc/vmmanager/<user>/<vm>.share` and `/etc/exports` → `set_share` → refresh.

- [ ] **Step 1: Write the failing tests**

```python
async def test_shared_column_shows_folder(self): ...          # cell text "/home/kixz/share" in column "Shared"
async def test_share_flow_calls_setup_then_set_share(self): ... # setup_needed → ["x"]; confirms answered yes; set_share called with (sys, L, "web", folder)
async def test_share_without_internet_explains(self): ...      # notify text contains "needs internet on"
async def test_stop_sharing_calls_set_share_none(self): ...
async def test_stale_shares_offer_update_fix(self): ...        # health shares_stale → HealthScreen text "Shared folders are out of date", fix calls share.sync
async def test_folder_picker_returns_directory(self): ...      # press "u" on a highlighted dir → dismissed with its path
```

- [ ] **Step 2:** Run `venv/bin/python -m unittest tests.test_ui -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full suite and `python -m py_compile vmmanager/*.py vmmanager/ui/*.py`. Expected: PASS. Render a check image with `docs/make_screenshot.py`'s method and look at it (dialog and column).
- [ ] **Step 5:** Commit: `git commit -m "UI: f shares a folder with a VM; Shared column"`

### Task 6: Man page

**Files:**
- Modify: `vmmanager/man/vmmanager.8`

- [ ] **Step 1:** KEYS: add `f` (share a folder; needs internet on). New section `.Sh SHARED FOLDERS` after NETWORKING: what changes on the host (`.share` file, `/etc/exports` marked lines, `/etc/vmmanager/sync-exports`, the `/etc/rc.local` line, portmap/mountd/nfsd with nfsd `-t -n 4`, the pf line); that only that VM can mount it and files are owned by the user; the guest commands exactly as `share.guest_commands` (one-off and permanent, Linux and OpenBSD, `nfs-common`); the reboot behaviour and the boot window; mountd's random port (export list readable by a port scan, never mountable); "Shared folders are out of date" fix. FILES: the new files. UNINSTALL: stop sharing every VM, remove the rc.local line and `/etc/vmmanager/sync-exports`, `rcctl disable portmap mountd nfsd` unless dockerctl or the admin uses NFS.
- [ ] **Step 2:** Run `mandoc -Tlint -Wwarning vmmanager/man/*`. Expected: no output.
- [ ] **Step 3:** Commit: `git commit -m "man: SHARED FOLDERS"`

### Task 7: Live acceptance on the box

- [ ] **Step 1:** Build a wheel, `pipx install --force` it on the box (check the box is not in use first). Run the `f` flow on debian-12 with the user: setup dialog, share, `Shared` column, guest mount now and at boot (user on the guest console).
- [ ] **Step 2:** Reboot the box (user's OK first). Expected: `/etc/exports` lines match the new ids before the user logs in; dockerctl's line intact; guest remounts.
- [ ] **Step 3:** From the laptop: ports 111 and 2049 closed; `showmount -e <box>` fails.
- [ ] **Step 4:** Stop sharing; `/etc/exports` has no vmmanager lines. Record results in the vault note.
