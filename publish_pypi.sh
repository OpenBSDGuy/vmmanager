#!/bin/sh
set -e

# ─────────────────────────────────────────────
# publish_pypi.sh
# Builds and uploads vmmanager to PyPI.
#
# Usage:
#   chmod +x publish_pypi.sh
#   ./publish_pypi.sh
#
# Dependencies (build, twine):
#   - FreeBSD: installed from pkg (py3XX-build, py3XX-twine), because
#     twine depends on a Rust package (nh3) that pip can't build on FreeBSD.
#     The script offers to install them if they are missing.
#   - OpenBSD: build and nh3 (twine's Rust dependency) from pkg_add,
#     then twine itself with pip --user (OpenBSD has no twine package).
#   - Other systems: installed with pip.
#
# You need a PyPI API token:
#   1. Go to https://pypi.org and create an account
#   2. Account Settings → API tokens → Add API token
#   3. Scope: "Entire account" for the first upload
#      (after that, scope it to "openbsd-vmmanager" only)
#   4. Copy the token — it starts with "pypi-"
#   5. Paste it when prompted below (or set TWINE_PASSWORD beforehand)
# ─────────────────────────────────────────────

PYTHON="${PYTHON:-python3}"

have_tools() {
    "$PYTHON" -c 'import build, twine' >/dev/null 2>&1
}

echo "==> Checking dependencies..."
if ! have_tools; then
    if [ "$(uname -s)" = "FreeBSD" ]; then
        pyver=$("$PYTHON" -c 'import sys; print("py%d%d" % sys.version_info[:2])')
        pkgs="${pyver}-build ${pyver}-twine"
        echo "    'build' and/or 'twine' not found for $PYTHON."
        echo "    On FreeBSD they have to come from pkg."
        echo "    Will run: pkg install $pkgs"
        printf "    Install now? [y/N] "
        read -r answer
        case "$answer" in
            y|Y|yes|YES) ;;
            *) echo "Aborted."; exit 1 ;;
        esac

        if [ "$(id -u)" -eq 0 ]; then
            pkg install -y $pkgs
        elif command -v doas >/dev/null 2>&1; then
            doas pkg install -y $pkgs
        elif command -v sudo >/dev/null 2>&1; then
            sudo pkg install -y $pkgs
        else
            echo "Need root to install packages (no doas/sudo found)."
            exit 1
        fi

        if ! have_tools; then
            echo "Installed, but $PYTHON still can't import them."
            echo "If a virtualenv is active, run 'deactivate' (or recreate it with"
            echo "'python3 -m venv --system-site-packages venv') and try again."
            exit 1
        fi
    elif [ "$(uname -s)" = "OpenBSD" ]; then
        echo "    'build' and/or 'twine' not found for $PYTHON."
        echo "    Will run: pkg_add py3-build py3-nh3, then pip install --user twine"
        printf "    Install now? [y/N] "
        read -r answer
        case "$answer" in
            y|Y|yes|YES) ;;
            *) echo "Aborted."; exit 1 ;;
        esac

        if [ "$(id -u)" -eq 0 ]; then
            pkg_add py3-build py3-nh3
        else
            doas pkg_add py3-build py3-nh3
        fi
        "$PYTHON" -m pip install --quiet --user twine

        if ! have_tools; then
            echo "Installed, but $PYTHON still can't import them."
            exit 1
        fi
    else
        "$PYTHON" -m pip install --quiet --upgrade build twine
    fi
fi

echo "==> Cleaning previous builds..."
rm -rf dist/ build/ ./*.egg-info

echo "==> Building package..."
"$PYTHON" -m build

echo "==> Checking package..."
"$PYTHON" -m twine check dist/*

echo ""
echo "==> Ready to upload to PyPI."
if [ -z "$TWINE_PASSWORD" ]; then
    echo "    Paste your API token (starts with 'pypi-', input is hidden)."
    echo "    Get one at: https://pypi.org/manage/account/token/"
    printf "    Token: "
    trap 'stty echo 2>/dev/null' EXIT INT TERM
    stty -echo 2>/dev/null || true
    read -r TWINE_PASSWORD
    stty echo 2>/dev/null || true
    echo ""
    export TWINE_PASSWORD
fi
export TWINE_USERNAME="${TWINE_USERNAME:-__token__}"

"$PYTHON" -m twine upload dist/*

echo ""
echo "==> Done! Install with:"
echo "    pipx install openbsd-vmmanager"
echo "    vmmanager"
