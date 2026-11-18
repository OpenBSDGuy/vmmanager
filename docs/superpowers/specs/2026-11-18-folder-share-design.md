# vmmanager — share a host folder with a VM (key `f`)

Date: 2026-11-18. Status: approved by the user 2026-11-18.

## Intent

Let a user share one folder on the OpenBSD host with one VM, as easily as
VirtualBox's shared folders. vmd has no shared-folder device (no virtio-fs,
no 9p), so the share runs over the VM's network link. The approach comes from
the user's `dockerctl` project, which already shares the home folder with its
Docker VM over NFS and benchmarked NFS against sshfs on the same box.

The user wants it **seamless**: set it up once per VM, and the share is there
after every host reboot and every guest boot, with no password prompts on
start/stop.

## Goals

- Key `f` on a managed VM the user owns: pick a folder, share it, or stop
  sharing it.
- Only that VM can mount the folder (not every VM on the host).
- Start/stop of a VM with a share stays root-free (as today).
- The share survives host reboots, even though VM addresses shift then.
- A `Shared` column in the VM list shows the shared folder.
- The dialog shows the guest-side commands, ready to paste: mount now, and
  mount at every guest boot.

## Non-goals (v1)

- More than one folder per VM; read-only shares; per-share options.
- Setting up the guest automatically (cloud-init, ssh into the guest).
  vmmanager has no login into guests; the user pastes one line once per VM.
- sshfs. Considered and rejected (see Approaches).
- Shares for VMs not managed by vmmanager.

## Anti-goals

- Root running code the user can write. The boot hook must not run vmmanager
  from the user's pipx venv (in `~`, user-writable): that would let anything
  running as the user get root at boot without the doas password.
- Touching `/etc/exports` lines vmmanager did not write (dockerctl's line, the
  admin's own lines).
- Losing another tool's `/etc/exports` line by a concurrent write.
- A stale export giving one VM another VM's folder.

## Facts verified on the box (OpenBSD 8.0, 2026-11-18)

- vmd gives every configured VM, even stopped and `disable`d ones, an id when
  it loads the config. The guest address is `100.64.<id>.3`, the host side
  `100.64.<id>.2`.
- The id stays the same across start/stop and across `vmctl reload` with no
  config change. A VM added while vmd runs gets the next free id; the others
  keep theirs. Deleting a VM + reload leaves the others' ids alone.
- **After a reboot, ids are given again in config order** (the aggregate file
  is sorted by name), so after a create or delete they shift. Example from the
  test: a new `aaa-idtest` took id 1 and every other VM moved up by one.
- Boot order in `/etc/rc`: portmap, mountd/nfsd, vmd, `pkg_scripts`
  (dockerctl), then `/etc/rc.local`. So at `rc.local` time vmd has assigned
  the ids.
- dockerctl (from commit 0862161) edits only its own marked line in
  `/etc/exports`, under an exclusive `flock` on `/var/run/exports.lock`,
  writes through `/etc/exports.dockerctl` + rename, then `pkill -HUP -x
  mountd`. It starts portmap/mountd/nfsd if they are not running and never
  stops them. It runs nfsd as `-t -n 4` (TCP only).
- NFS v3 from dockerctl's guest works with `-mapall=<user>`: files written in
  the guest belong to the user on the host. OpenBSD has no rpc.lockd, so Linux
  guests mount with `nolock`.

## Approaches

1. **NFS, one export line per VM, kept in sync by a root boot hook** (chosen).
   Fast (about 3x sshfs on small files in dockerctl's benchmark), no login in
   the guest, the mount line works on every guest boot. Costs: one-time root
   setup (NFS daemons, boot hook) and a sync whenever ids can change.
2. **NFS, one export to the whole `100.64.0.0/10`.** Simplest and never stale,
   but every VM on the host can mount every shared folder. Rejected by the
   user: they want per-VM limits.
3. **sshfs from the guest to the host's sshd.** No root on the host at all, and
   access is checked by the ssh login, not by address. Costs: slower, and the
   guest needs a password at each mount or a host login key stored in the
   guest. Rejected: less seamless.

Also considered for approach 1: writing the export at VM start/stop. Rejected:
start/stop would then need doas, and a VM stopped outside vmmanager leaves a
stale line.

## Design

### Where the share is stored

- `/etc/vmmanager/<user>/<vm>.share`, root:wheel 0644: one line, the absolute
  folder path. Root-owned so the boot hook trusts only root-owned input. Not
  named `*.conf`: `inventory.load_inventory` treats every
  `/etc/vmmanager/*/*.conf` as a VM.
- The folder path is validated where it is written and again by the sync:
  absolute, an existing directory, and no whitespace, `#`, `\` or `"` (they
  would break `exports(5)` syntax). The path is stored as typed, not resolved
  through symlinks.

### The export lines

One line per shared VM, for all managed VMs with a `.share` file, running or
not (the id belongs to the VM while vmd runs):

```
# vmmanager: the next line is managed by vmmanager (debian-12)
/home/kixz/share -mapall=kixz 100.64.2.3
```

- `-mapall=<owner>`: the owner is the `<user>` folder the `.share` file sits
  in (vmmanager always writes `owner <user>` into that VM's block). The guest
  gets exactly the owner's rights on that folder, as with dockerctl.
- The address comes from `vmctl status` (the id) at sync time.
- A VM with a `.share` file but without an id (vmd not running, or VM not
  loaded) gets no line.

### The sync (one root code path)

A standalone, stdlib-only Python script shipped in the package and installed
by the one-time setup as `/etc/vmmanager/sync-exports`, root:wheel 0755. Root
runs it with the system Python (`/usr/local/bin/python3`, root-owned package).
It never imports vmmanager from the user's venv.

Steps, all under `fcntl.flock(LOCK_EX)` on `/var/run/exports.lock` (the same
lock as dockerctl; don't lock `/etc/exports` itself, rename swaps the file):

1. Read `vmctl status` → name → id.
2. Read every `/etc/vmmanager/*/*.share`; validate the path (skip and report
   invalid ones).
3. Rebuild `/etc/exports`: drop every vmmanager-marked pair of lines, keep all
   other lines as they are, append the new vmmanager lines.
4. If the text changed: write `/etc/exports.vmmanager`, chmod 0644, rename over
   `/etc/exports`, `pkill -HUP -x mountd`.

It is run:

- at boot, from one line in `/etc/rc.local` (added by the setup, with the
  `# added by vmmanager` marker): `/usr/local/bin/python3 /etc/vmmanager/sync-exports`.
- by the TUI through doas after any change that can change an export: share,
  stop sharing, create, delete (the delete also removes the `.share` file),
  and update (*Claude's call*: an update that turns internet off keeps the
  `.share` file but the sync writes no line for a VM without a local
  interface, so the address can't be handed to another VM).
  These already ask for doas, except share/stop sharing, which ask for it
  themselves.

### One-time setup (doas, confirm dialog naming every change)

Offered the first time the user presses Share and the setup is missing; same
pattern as the networking setup (`network.py`):

- install `/etc/vmmanager/sync-exports`, root:wheel 0755 (re-installed when the
  bundled copy differs, e.g. after an upgrade);
- add the marked line to `/etc/rc.local` (create the file if missing);
- `rcctl set nfsd flags -t -n 4` (TCP only, matches dockerctl);
- `rcctl enable portmap mountd nfsd`, then start them.

### Detecting stale exports without root

On startup and refresh the TUI computes the expected vmmanager lines (from
`vmctl status` and the `.share` files, both readable without root) and
compares them with `/etc/exports` (0644). If they differ (vmd was restarted by
hand, a sync failed), the health check shows "Shared folders are out of date"
with a fix button that runs the sync through doas.

### UI

- Key `f`, footer label "Share". Available on managed VMs the user owns, any
  state, with internet on. Unavailable keys explain why, as today; with
  internet off the reason is "needs internet on: the share runs over the VM's
  network link" (*Claude's call*: internet off means no `local interface`, so
  there is no link to mount over).
- Two VMs may share the same folder: each gets its own line (a host appears
  once per filesystem, as `exports(5)` requires).
- Dialog "Share a folder with <vm>": folder input (prefilled with the current
  share), Browse (the existing directory tree, for folders), and the guest
  commands for the folder in the input:
  - mount now, Linux:
    `mount -t nfs -o vers=3,proto=tcp,nolock "$(ip route | awk '/default/{print $3}'):<folder>" /mnt/share`
  - mount now, OpenBSD:
    `mount_nfs -T "$(route -n get default | awk '/gateway/{print $2}'):<folder>" /mnt/share`
  - mount at every boot: one root-crontab `@reboot` line that looks up the
    gateway and retries for about a minute (the network may come up late),
    for Linux and for OpenBSD.
  - a note that Debian guests need `nfs-common`, and that only this VM can
    mount the folder, with files owned by the user.
  Buttons: Share, Stop sharing (only when shared), Cancel.
- `Shared` column, last, fixed width 30, showing only the folder; a longer
  path wraps onto more lines in the same row (row height grows).

### Man page

vmmanager(8) gets a SHARED FOLDERS section: what is changed on the host
(`/etc/exports` lines, `rc.local` line, NFS daemons, the sync script); how to
mount the share in the guest, both one-off (mount now) and permanent (the
`@reboot` root-crontab line, added with `crontab -e`), for Linux and OpenBSD
guests, including the packages a guest needs (Debian: `nfs-common`); the
reboot behaviour and the boot window, that a VM's share is reachable only from
that VM, and how to undo it (Stop sharing; then remove the rc.local line and
disable the daemons by hand). UNINSTALL lists the sync script and the
`rc.local` line.

## To verify on the box during the build

- Two export lines for different folders on the same filesystem, to different
  hosts (dockerctl's `/home/kixz` and a vmmanager subfolder), are accepted by
  mountd together (`exports(5)`: a host only once per filesystem).
- pf on the box (and vmmanager's anchor) does not block NFS from a guest.
- The `@reboot` crontab line mounts on a Debian and an OpenBSD guest.
- After a reboot with shifted ids, `rc.local`'s sync fixes the lines before a
  guest mounts.

### Boot window (*Claude's call*, documented in the man page)

At boot, mountd reads the old `/etc/exports` before vmd assigns the new ids,
and the `rc.local` sync fixes the lines a few seconds later. In that window a
stale line can point at the wrong VM. Accepted and documented, because:
vmmanager's VMs are always `disable` (vmd never starts them at boot) and no
user is logged in yet to start one; only hand-written `enable`d VMs and
dockerctl's VM run in that window. A shutdown hook (`rc.shutdown`) would not
cover a crash or power loss, so it is not worth the extra root edit.

## If a box check fails

The checks under "To verify" run first, before any code. If mountd rejects a
line, pf blocks NFS from guests, or the guest mount fails, the build stops
and the user decides (for pf, a pass rule in vmmanager's own anchor is the
likely fix, but it changes the network setup for everyone).

## Left to the builder

- Module layout (a `share.py` next to `network.py` is the natural place).
- Exact retry loop and wording of the guest commands.
- How the TUI finds the bundled sync script (package data, like the man pages).
