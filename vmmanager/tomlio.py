import json
import os
import tempfile
from pathlib import Path
import tomllib


def _value(v) -> str:
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, int):
        return str(v)
    return json.dumps(v, ensure_ascii=False).replace("\x7f", "\\u007f")  # a JSON string is a valid TOML basic string


def dump(data: dict) -> str:
    return "".join(f"{k} = {_value(data[k])}\n" for k in sorted(data))


def load(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def save(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dump(data))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
