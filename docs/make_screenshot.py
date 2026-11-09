"""Regenerate the README screenshot from the repo root:

    venv/bin/python -m docs.make_screenshot

Renders the real UI with sample VMs through the test fakes (nothing on the host is
touched) to docs/screenshot.svg. The README uses a PNG, since PyPI can't show SVGs:

    printf '<img src="file://%s/docs/screenshot.svg" style="width:1385px">' "$PWD" > /tmp/w.html
    chromium --headless=new --force-device-scale-factor=2 --window-size=1385,392 \
        --default-background-color=00000000 --allow-file-access-from-files \
        --screenshot=docs/screenshot.png file:///tmp/w.html
    magick docs/screenshot.png -trim +repage docs/screenshot.png
"""
import asyncio
import re
import tempfile
from pathlib import Path
from unittest import mock

from tests.fakes import FakeSystem
from vmmanager.health import Health
from vmmanager.layout import Layout
from vmmanager.system import Result
from vmmanager.ui.app import VmmApp

OUT = Path(__file__).parent / "screenshot.svg"
STATUS = """\
   ID   PID VCPUS  MAXMEM  CURMEM     TTY        OWNER    STATE NAME
    1 41552     4    8.0G    8.0G   ttyp2    kixz:kixz  running debian
    2     -     4    8.0G       -       -    kixz:kixz  stopped debian-12
    3     -     1    2.0G       -       -    kixz:kixz  stopped openbsd
    4     -     1    1.0G       -       -    kixz:kixz  stopped legacy
"""
BLOCK = 'vm "{n}" {{\n  memory {m}\n  cpus {c}\n  disable\n  disk "/home/kixz/vmmanager/{n}/{n}.qcow2"\n{i}  owner kixz\n}}\n'


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        layout = Layout(etc=root / "etc", home=root / "home/kixz", user="kixz")
        mine = layout.etc_dir / "kixz"
        mine.mkdir(parents=True)
        for n, m, c, net in [("debian", "8G", 4, True), ("debian-12", "8G", 4, True), ("openbsd", "2G", 1, False)]:
            (mine / f"{n}.conf").write_text(BLOCK.format(n=n, m=m, c=c, i="  local interface\n" if net else ""))
        layout.vm_conf.write_text('vm "legacy" {\n  memory 1G\n  disk /home/kixz/VM/legacy.img\n  owner kixz\n}\n')
        sys = FakeSystem(root)
        sys.responses[("vmctl", "status")] = Result(0, STATUS, "")
        healthy = Health(True, True, True, "ok", None, [], None)
        with mock.patch("vmmanager.health.check", return_value=healthy), \
             mock.patch("vmmanager.manpages.should_offer", return_value=False):
            app = VmmApp(sys, layout)
            async with app.run_test(size=(112, 14)) as pilot:
                for _ in range(50):
                    await pilot.pause()
                    if app.doas is not None and not app.workers:
                        break
                await pilot.press("down", "down")  # rows sort by name: select "legacy" so its hint shows
                await pilot.pause()
                OUT.write_text(xfce_frame(app.export_screenshot()))


def xfce_frame(svg: str) -> str:
    """Swap Textual's macOS-style traffic lights for XFCE-style buttons on the right."""
    width = float(re.search(r'viewBox="0 0 ([\d.]+)', svg).group(1))
    buttons = "".join(
        f'<text fill="#c5c8c6" font-size="18" text-anchor="middle" x="{width - 26 - 30 * i:.0f}" y="28">{glyph}</text>'
        for i, glyph in enumerate("×□–"))  # close is rightmost
    svg, n = re.subn(r'<g transform="translate\(26,22\)">.*?</g>', buttons, svg, count=1, flags=re.S)
    assert n == 1, "Textual's window chrome changed; update xfce_frame()"
    return svg.replace('rx="8"', 'rx="3"', 1)


if __name__ == "__main__":
    asyncio.run(main())
