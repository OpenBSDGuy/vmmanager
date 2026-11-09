import argparse
import platform
import sys

from vmmanager import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vmmanager")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--man", action="store_true")
    parser.add_argument("--install-man", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(f"vmmanager {__version__}")
        return 0
    if args.man or args.install_man:
        return 0  # Task 12
    if platform.system() != "OpenBSD":
        print("vmmanager only runs on OpenBSD", file=sys.stderr)
        return 1
    return 0  # Task 13
