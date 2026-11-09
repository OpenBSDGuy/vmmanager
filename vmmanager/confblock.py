import re
from dataclasses import dataclass
from pathlib import Path

from vmmanager.errors import VmmError
from vmmanager.layout import quote

# strings, comments, braces, and bare words
_TOKEN = re.compile(r'"[^"]*"|#[^\n]*|[{}]|[^\s{}"#]+')
_WORD = re.compile(r'"[^"]*"|#.*|\S+')
_MEMORY = re.compile(r"\d+(\.\d+)?[KMGTkmgt]?")
_DISK = re.compile(r'^(\s*disk\s+)("[^"]*"|\S+)(.*)$', re.S)


@dataclass(frozen=True)
class BlockFacts:
    memory: str | None
    cpus: int | None
    cdrom: str | None
    disks: list[str]
    owner: str | None
    local_interfaces: int
    other_interfaces: bool
    uses_macros: bool
    is_instance: bool


def _unq(tok: str) -> str:
    return tok[1:-1] if tok.startswith('"') else tok


def _words(line: str) -> list[str]:
    return [w for w in _WORD.findall(line) if not w.startswith("#")]


def find_block(text: str, name: str) -> tuple[int, int] | None:
    toks = [m for m in _TOKEN.finditer(text) if not m.group().startswith("#")]
    depth = 0
    for i, t in enumerate(toks):
        v = t.group()
        if v == "{":
            depth += 1
        elif v == "}":
            depth -= 1
        elif depth == 0 and v == "vm":
            head = [x.group() for x in toks[i + 1 : i + 5]]
            if head[:1] and head[1:2] == ["{"] and _unq(head[0]) == name:
                open_i = i + 2
            elif head[1:2] == ["instance"] and head[3:4] == ["{"] and _unq(head[2]) == name:
                open_i = i + 4
            else:
                continue
            if text[text.rfind("\n", 0, t.start()) + 1 : t.start()].strip():
                raise VmmError(f"Cannot edit vm {name}: another statement shares its first line")
            d = 0
            for j in range(open_i, len(toks)):
                if toks[j].group() == "{":
                    d += 1
                elif toks[j].group() == "}":
                    d -= 1
                    if d == 0:
                        start = text.rfind("\n", 0, t.start()) + 1
                        end = text.find("\n", toks[j].end())
                        if _words(text[toks[j].end() : len(text) if end < 0 else end]):
                            raise VmmError(f"Cannot edit vm {name}: another statement shares its last line")
                        return start, len(text) if end < 0 else end + 1
            return None
    return None


def _inner_lines(block: str) -> list[str]:
    return block[block.index("{") + 1 : block.rindex("}")].splitlines()


def facts(block: str) -> BlockFacts:
    vals: dict[str, str] = {}
    disks: list[str] = []
    local = 0
    other = macros = False
    for line in _inner_lines(block):
        w = _words(line)
        if not w:
            continue
        macros = macros or "$" in " ".join(w)
        if w[0] == "disk" and len(w) > 1:
            disks.append(_unq(w[1]))
        elif w[0] == "local" and w[1:2] == ["interface"]:
            local += 1
        elif w[0] == "interface":
            other = True
        elif w[0] in ("memory", "cpus", "cdrom", "owner") and len(w) > 1:
            vals.setdefault(w[0], _unq(w[1]))
    head = _words(block[: block.index("{")])
    return BlockFacts(
        memory=vals.get("memory"),
        cpus=int(vals["cpus"]) if vals.get("cpus", "").isdigit() else None,
        cdrom=vals.get("cdrom"),
        disks=disks,
        owner=vals.get("owner"),
        local_interfaces=local,
        other_interfaces=other,
        uses_macros=macros,
        is_instance="instance" in head,
    )


def _check(memory: object, cpus: object) -> None:
    if memory is not None and not (isinstance(memory, str) and _MEMORY.fullmatch(memory)):
        raise VmmError(f"Invalid memory {memory!r}: use a size like 512M or 2G")
    if cpus is not None and not (type(cpus) is int and 1 <= cpus <= 64):
        raise VmmError(f"Invalid cpus {cpus!r}: use a whole number from 1 to 64")


def render_block(
    name: str, *, memory: str, cpus: int, disk: Path, cdrom: str | None, internet: bool, owner: str
) -> str:
    _check(memory, cpus)
    lines = [f"vm {quote(name)} {{", f"  memory {memory}", f"  cpus {cpus}", "  disable"]
    lines.append(f"  disk {quote(str(disk))}")
    if cdrom is not None:
        lines.append(f"  cdrom {quote(cdrom)}")
    if internet:
        lines.append("  local interface")
    lines += [f"  owner {owner}", "}"]
    return "\n".join(lines) + "\n"


def _split(block: str) -> list[str]:
    """Lines of a multi-line block; refuses one-liners it cannot edit by line."""
    lines = block.splitlines(keepends=True)
    head = _words(lines[0].split("{", 1)[1]) if "{" in lines[0] else ["?"]
    name = _words(lines[0].split("{", 1)[0])[-1].strip('"')
    if head or len(lines) < 2 or lines[-1].strip() != "}":
        raise VmmError(f"Cannot edit vm {name}: its block is not one statement per line", detail=block)
    for line in lines[1:-1]:
        depth = 0
        for m in _TOKEN.finditer(line):
            depth += {"{": 1, "}": -1}.get(m.group(), 0)
            if depth < 0:
                break
        if depth != 0:
            raise VmmError(f"Cannot edit vm {name}: it has a statement spanning several lines", detail=block)
    return lines


def _indent(lines: list[str]) -> str:
    for line in lines[1:-1]:
        if _words(line):
            return line[: len(line) - len(line.lstrip())]
    return "  "


def _first(lines: list[str], *keys: str) -> int | None:
    for i, line in enumerate(lines[1:-1], 1):
        w = _words(line)
        if w and tuple(w[: len(keys)]) == keys:
            return i
    return None


def _set(lines: list[str], key: str, value: str | None, after: tuple[str, ...]) -> None:
    ind = _indent(lines)
    i = _first(lines, key)
    if i is not None:
        eol = "\n" if lines[i].endswith("\n") else ""
        if value is None:
            del lines[i]
        else:
            lines[i] = f"{lines[i][: len(lines[i]) - len(lines[i].lstrip())]}{key} {value}{eol}"
    elif value is not None:
        pos = 1
        for a in after:
            j = max((k for k in range(1, len(lines) - 1) if _words(lines[k])[:1] == [a]), default=None)
            if j is not None:
                pos = j + 1
                break
        lines.insert(pos, f"{ind}{key} {value}\n")


def edit_block(block: str, changes: dict[str, object]) -> str:
    lines = _split(block)
    _check(changes.get("memory"), changes.get("cpus"))
    for key, value in changes.items():
        if key == "memory":
            _set(lines, "memory", str(value), ())
        elif key == "cpus":
            _set(lines, "cpus", str(value), ("memory",))
        elif key == "cdrom":
            _set(lines, "cdrom", None if value is None else quote(str(value)), ("disk",))
        elif key == "internet":
            has = _first(lines, "local", "interface")
            if value and has is None:
                owner = _first(lines, "owner")
                lines.insert(len(lines) - 1 if owner is None else owner, f"{_indent(lines)}local interface\n")
            elif not value:
                while (i := _first(lines, "local", "interface")) is not None:
                    del lines[i]
        else:
            raise VmmError(f"Unknown vm.conf setting {key!r}")
    return "".join(lines)


def comment_out(text: str, span: tuple[int, int], marker: str) -> str:
    start, end = span
    body = "".join(marker + ln for ln in text[start:end].splitlines(keepends=True))
    return text[:start] + body + text[end:]


def replace_disks(block: str, mapping: dict[str, str]) -> str:
    out = []
    for line in block.splitlines(keepends=True):
        m = _DISK.match(line)
        if m and _unq(m.group(2)) in mapping:
            line = f"{m.group(1)}{quote(mapping[_unq(m.group(2))])}{m.group(3)}"
        out.append(line)
    return "".join(out)


def ensure_owner(block: str, user: str) -> str:
    lines = _split(block)
    if _first(lines, "owner") is None:
        lines.insert(len(lines) - 1, f"{_indent(lines)}owner {user}\n")
    return "".join(lines)
