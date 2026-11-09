# vmmanager — design

Date: 2026-11-09. Status: draft for approval.

## Intent

`vmmanager` is an open-source TUI for managing OpenBSD vmm(4)/vmd(8) virtual
machines. Aimed at home users; multi-user support is a bonus that comes from
following OpenBSD's own permission model, not from new machinery. Installed
from PyPI with one command (`pipx install vmmanager`), like the author's other
project `wifzf`.

It should feel like VirtualBox's model — one folder per VM holding its disk and
extras — while keeping `vm.conf(5)` as the source of truth for everything vmd
understands. Principle: follow what OpenBSD already provides (vmctl, vmd,
vm.conf, doas, owner) instead of reinventing it.

## Goals

- TUI listing all VMs vmd knows about, with live state (from `vmctl status`).
- Actions on managed VMs: **create, start, run, connect, stop, update, delete**.
  - start = boot in background.
  - run = boot and attach the serial console (`vmctl start -c` equivalent).
  - connect = attach the console of an already running VM (`vmctl console`).
  - Leaving the console (`~.`) returns to the TUI.
- Opt-in, one-action **migration** of VMs not created by vmmanager.
- Comprehensive mdoc man pages, installable system-wide.
- Usable by many people: clear errors, never corrupts the host's vmd setup.

## Non-goals (v1)

- Advanced networking: switches/bridges/veb, port forwarding, host-only
  networks, custom DNS. v1 offers exactly two choices per VM: internet
  (NAT) or air-gapped (no interface). Everything else comes in later
  iterations.
- A restricted root helper for non-admin users (see Privilege model).
- Snapshots, cloning, templates/instances, disk resize.
- Remote hosts; any non-OpenBSD OS.
- Running/managing VMs whose config the user can't read.

## Anti-goals (failure even if it "works")

- Rewriting or reformatting the user's hand-written `/etc/vm.conf`,
  `/etc/pf.conf` or `/etc/sysctl.conf` beyond exactly the documented edits
  (an include/anchor line, the forwarding line, commenting out a migrated
  block).
- Leaving vmd's config in a state where `vmd -n` fails — that stops *every*
  VM on the host at next boot/reload.
- Letting a user-writable file be read by vmd as config (root escalation:
  a config line can point a disk at `/dev/sd0c` or set any owner).
- Asking for root without telling the user why.

## Facts verified on the target (OpenBSD 8.0, amd64)

- `vmd.sock` is `root:wheel 0660`; wheel members run `vmctl status` without
  root. Others need the admin to set `socket owner` in vm.conf.
- Owner of a VM (`owner user[:group]`) can start/stop/pause/console it without
  root (per vm.conf(5); **to be confirmed with a second test user** during build).
  *Builder's call:* if the test shows otherwise, start/stop/run/connect also go
  through doas, and the man page says so; nothing else in the design changes.
- Non-root can: `vmctl create` disks, `vmd -n -f <file>`. Non-root cannot:
  `vmctl reload`.
- `vmctl status` has no machine-readable output; columns are
  `ID PID VCPUS MAXMEM CURMEM TTY OWNER STATE NAME`.
- Nested `include` works (vm.conf → aggregate → per-VM file).
- `vmd -n` fails if any referenced disk or include file is missing.
- `vmctl reload` does not touch running VMs' configs.
- Python 3.14 + ncurses present; Textual installs via pip with 3 extra pure-Python
  wheels; `/usr/local/man` with `mandoc.db` exists.

## Layout

Per user:

```
~/vmmanager/
  <vm>/
    <vm>.qcow2        # disk (created with vmctl create)
    vmmanager.toml    # extras vm.conf can't hold (see below)
  state.toml          # tool state, e.g. installed man page version
```

System (root-owned, written only via doas):

```
/etc/vm.conf                       # one added line: include "/etc/vmmanager/vmmanager.conf"
/etc/vmmanager/vmmanager.conf      # regenerated: one include line per managed VM
/etc/vmmanager/<user>/<vm>.conf    # that VM's vm "<vm>" { ... } block
```

- All `/etc/vmmanager` files: `root:wheel`, `0644`, dirs `0755`.
- The VM's vm.conf block always sets `owner <user>` (the creating user) and
  `disable` (no autostart; user starts explicitly).
- Extras in `vmmanager.toml` (v1): install ISO path, OS label, free-text notes,
  created timestamp, vmmanager version. Nothing vmd needs lives here.
- VM names are global in vmd: the tool checks `vmctl status` + existing config
  before creating and says which user holds the name.

## Privilege model ("simple")

- Read-only (list/status): needs vmd socket access. If missing, show a screen
  explaining `socket owner` and quit gracefully.
- start/stop/run/connect on owned VMs: plain vmctl, no doas.
- create/update/delete/migrate/man-page install: need doas. The tool checks
  whether the user may use doas (`doas -n true`, or presence in doas.conf
  rules if a password is needed). If not, those actions are greyed out with a
  one-line reason.
- When doas needs a password, the TUI suspends so doas can prompt on the real
  terminal, then resumes. `persist` keeps prompts rare.
- **Ownership rule** (user's decision: others may *see* VMs but not edit or
  start them): the TUI offers actions only on VMs whose `owner` is the current
  user. Other users' VMs, managed or not, are listed read-only with
  "owned by <user>". doas could bypass this, but the tool doesn't.
- **Pre-existing config errors:** if `vmd -n` already fails before the tool
  changes anything, every write action is blocked and the vmd error is shown.
  The tool never "fixes" hand-written config. (*Builder's call*: keeps the
  "vmd -n always passes after our write" guarantee.)
- Every root write follows one pattern: build the file in a user temp dir →
  `doas install -o root -g wheel -m 0644` into place → `doas vmd -n` on the
  full config → on failure roll back to the previous file(s) → `doas vmctl reload`.

## Actions

**Create** (form): name, memory (default 1G), cpus (1), disk size (20G),
install ISO (optional, file picker), Internet on/off (default on; see
Networking; first internet VM triggers the one-time host NAT setup).
Steps: validate name (vmctl rules: letter first, `[A-Za-z0-9._-]`), check
global uniqueness → mkdir `~/vmmanager/<vm>` → `vmctl create -s` → write
extras → write per-VM conf (with `cdrom` if ISO given) → regenerate aggregate
→ ensure include line in `/etc/vm.conf` (first time only, after a
timestamped copy) → `vmd -n` → reload. Any failure undoes earlier steps.
Offer "run now" at the end to start the installer on the console.

**Start / Run / Connect / Stop**: `vmctl start <name>`, `vmctl start -c`,
`vmctl console`, `vmctl stop` (graceful; a second action offers `-f`).
Console actions suspend the TUI and hand over the terminal.
Before start/run, the tool checks every disk and ISO path in the VM's block
with `lstat`: it must be a regular file (not a symlink or device) owned by the
VM's owner. Otherwise it refuses and explains. *Builder's call*: vmd opens
these paths as root, so a swapped-in symlink to `/dev/sd0c` would give the
guest root access to the host disk. In v1 every owner already has doas, so
this is defence in depth. The man page's SECURITY section documents it.

**Update**: same form as create, minus name and disk size. VM must be stopped.
Includes detaching the ISO after install. *Builder's call*: edits are
**line-level** on the stored block. The form changes only the lines it
models (`memory`, `cpus`, `cdrom`, the single `local interface` line), and
every other line (extra disks, `switch`, `lladdr`, `boot`, comments) is kept
as written and shown read-only in the form. The network field is disabled
when the block has anything other than zero or one plain `local interface`.
This is the same code path for tool-created and migrated VMs. Written
through the root-write pattern.

**Delete**: VM must be stopped. Confirm. Remove per-VM conf → regenerate
aggregate → `vmd -n` → reload. Then ask whether to delete `~/vmmanager/<vm>/`
(config removed *before* disk, never the other way, because a missing disk
breaks vmd for everyone).

**Health check on start-up**: if `vmd -n` currently fails (e.g. a user deleted
a VM folder by hand), show which managed VM is broken and offer to remove its
config. That offer is made only to the VM's owner; for others the tool only
reports it. If vmd isn't running/enabled, or vmm-bios firmware is missing, say so
and offer the fix (`rcctl enable/start vmd`, `fw_update`) via doas.

## Networking (user's requirement: VirtualBox-like, works out of the box)

Each VM has one setting, **Internet: on/off**, which defaults to on at
create and can be changed in Update.
- on → the block has `local interface` (vmd assigns 100.64.<id>.3, runs DHCP,
  gateway/DNS = host tap 100.64.<id>.2).
- off → no interface at all ("air-gapped").

Host-side NAT is set up **once per host**, automatically, the first time a
VM with internet is created (or migrated). It uses the doas root-write
pattern and follows the same "own file plus one include line" approach as
vm.conf:
- `/etc/vmmanager/pf.conf` (tool-owned anchor rules):
  ```
  match out on egress inet from 100.64.0.0/10 to any nat-to (egress)
  pass in quick inet proto { udp tcp } from 100.64.0.0/10 to any port domain rdr-to 127.0.0.1 port domain
  ```
- Appended once to `/etc/pf.conf`:
  `anchor "vmmanager"` + `load anchor "vmmanager" from "/etc/vmmanager/pf.conf"`.
  Validate with `pfctl -nf /etc/pf.conf`, then load with `pfctl -f`. Restore the
  previous pf.conf if either step fails.
- IP forwarding: if `net.inet.ip.forwarding` is 0, set it with `sysctl` and
  append `net.inet.ip.forwarding=1` to `/etc/sysctl.conf` (once).
- DNS: guests ask the host tap, and pf redirects the queries to a resolver on
  the host's 127.0.0.1. This works on any network, unlike a hard-coded upstream.
  If no resolver listens on 127.0.0.1:53, offer `rcctl enable unwind` and
  `rcctl start unwind` (unwind ships with the base system and needs no
  configuration; resolvd then points the host at it too).
- The health check verifies that the anchor is loaded (`pfctl -a vmmanager -sr`),
  that forwarding is on, and that a resolver is listening. It offers a re-apply
  if anything is missing.
- Each step is explained before the doas prompt ("VMs need NAT to reach the
  internet; this adds 2 lines to /etc/pf.conf …").
- Existing hand-written NAT/rdr rules (like on the author's box) are left
  alone. The anchor sits at the end of pf.conf, so with last-match plus `quick`
  its rules take effect. The man page explains how to remove the user's old
  lines.
- Verified on OpenBSD 8.0: the anchor syntax passes `pfctl -nvf`, and unwind
  listens on 127.0.0.1:53. **Still to test in the build:** a running VM
  actually resolves and reaches the internet through `rdr-to 127.0.0.1`.
- The man page has a NETWORKING section covering what was changed, why, and
  how to undo it.

## Unmanaged VMs and migration

- Any VM in `vmctl status` without a `/etc/vmmanager/<owner>/<vm>.conf` is
  "not managed here": listed greyed out with a short explanation; **all
  actions disabled**; one action: **Migrate**. Never automatic.
- Migrate is offered only when the block's `owner` is the current user or
  there is no `owner` line, in which case it's set to the current user.
  Files go under that user's `~/vmmanager` and `/etc/vmmanager/<user>/`.
- Migration (VM must be stopped):
  1. Locate the `vm "<name>" { ... }` block in `/etc/vm.conf` (text range by
     brace matching; copied verbatim, not reinterpreted).
  2. Refuse (and show manual steps) if: block uses macros (`$`), is an
     `instance`, lives in another included file, VM is running, a disk is a
     qcow2 with a backing file, or a disk is on a different filesystem than
     `~/vmmanager`.
  3. Move disk(s) into `~/vmmanager/<vm>/` (rename; keeps owner/mode), rewrite
     the copied block's disk paths, ensure `owner` present, write per-VM conf,
     add to aggregate.
  4. Comment out the original block in `/etc/vm.conf` with a marker
     (`# moved to vmmanager <date>:`); nothing deleted.
  5. `vmd -n`; on failure undo every step (move disks back, restore files).
     Then reload.
- On refusal/failure: explain why, give the manual steps, and point to
  `vmmanager(8)`, section MIGRATING.
- Acceptance case: the author's three VMs (`debian`, `debian-12`, `openbsd`;
  root-owned 0600 qcow2 disks in `~/VM` with names differing from VM names).

## Man pages

- `vmmanager.8` (command, keys, actions, privilege model, layout, migration
  incl. manual steps, health check, UNINSTALL) and `vmmanager.toml.5`.
  Written in mdoc, lint clean with `mandoc -Tlint`.
- Shipped inside the package. On first run, users with doas are asked once:
  "Install man pages system-wide? (needs doas)". Yes → copy to
  `/usr/local/man/man8`, `man5` and run `doas makewhatis /usr/local/man`.
  No → not asked again; `vmmanager --install-man` does it later.
- Installed version recorded in `~/vmmanager/state.toml`; offer again after
  an upgrade. `vmmanager --man` opens the bundled page with `man -l` without
  installing.

## Tech

- Python ≥ 3.11 (stdlib `tomllib`), Textual (pip dep), no other runtime deps.
- PyPI name, command, package: `vmmanager`. License: GPL-3.0 (same as wifzf;
  confirm).
- All OS interaction goes through a thin module wrapping vmctl/vmd/doas
  subprocess calls, so the rest is testable on Linux with fakes.
- Tests: unittest, as in wifzf; parsing of `vmctl status`, block
  extraction/commenting, config generation, rollback paths. CI on GitHub
  Actions like wifzf (py 3.11 + 3.14). Live acceptance on the author's
  OpenBSD box.

## Left to the builder

- Screen layout, key bindings, colours (sensible Textual defaults; show key
  hints in the footer).
- Exact TOML keys in `vmmanager.toml` and `state.toml`.
- Internal module structure.
- How state refresh is scheduled (polling `vmctl status` every few seconds is fine).
