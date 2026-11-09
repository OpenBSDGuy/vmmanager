import argparse
import platform
import sys

from vmmanager import __version__, manpages
from vmmanager.errors import VmmError
from vmmanager.layout import Layout
from vmmanager.system import System


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vmmanager")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--man", action="store_true")
    parser.add_argument("--install-man", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(f"vmmanager {__version__}")
        return 0
    if args.man:
        return manpages.view(System())
    if platform.system() != "OpenBSD":
        print("vmmanager only runs on OpenBSD", file=sys.stderr)
        return 1
    if args.install_man:
        try:
            manpages.install(System())
            manpages.record(Layout(), True)
        except VmmError as e:
            print(e.message, file=sys.stderr)
            if e.detail:
                print(e.detail, file=sys.stderr)
            return 1
        print(f"Installed man pages in {manpages.MAN_DIR}")
        return 0
    return 0  # Task 13
