#!/usr/bin/env bash
# install.sh — expose the `cosuil` command on your PATH via a symlink.
#
# Usage:
#   ./install.sh [TARGET_DIR]
#
# Defaults to "$HOME/bin". Idempotent: re-running is safe and reports
# whether the link is already correct. Never touches your photos.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LINK_NAME="cosuil"
DEFAULT_TARGET="${HOME}/bin"

usage() {
    cat << USAGE
Usage: ./install.sh [TARGET_DIR]

Symlink the cosuil CLI into TARGET_DIR (default: \$HOME/bin) so it can be
run from any terminal without activating the virtualenv.

Arguments:
  TARGET_DIR    Directory to install the symlink into (default: \$HOME/bin).
                It is created if missing.

Examples:
  ./install.sh                      # -> \$HOME/bin/cosuil
  ./install.sh /usr/local/bin       # system-wide (may need sudo)
USAGE
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
esac

if [ "$#" -gt 1 ]; then
    echo "Error: too many arguments." >&2
    usage >&2
    exit 2
fi

TARGET_DIR="${1:-$DEFAULT_TARGET}"
# Expand a leading ~ (also after a /)
TARGET_DIR="${TARGET_DIR/#\~/$HOME}"
TARGET_DIR="${TARGET_DIR//\/~\//$HOME\/}"

# ── locate the installed entry point ─────────────────────────────────────
VENV_EXEC="$SCRIPT_DIR/.venv/bin/$LINK_NAME"

if [ -x "$VENV_EXEC" ]; then
    COSUIL_EXEC="$VENV_EXEC"
elif command -v "$LINK_NAME" >/dev/null 2>&1; then
    COSUIL_EXEC="$(command -v "$LINK_NAME")"
else
    cat >&2 << ERROR
Error: cannot find the '$LINK_NAME' executable.

Set up the virtualenv first, from the repository root:

  python -m venv .venv
  .venv/bin/pip install -e .

then run ./install.sh again.
ERROR
    exit 1
fi

# Resolve to an absolute path so the symlink works from anywhere.
COSUIL_EXEC="$(cd "$(dirname "$COSUIL_EXEC")" && pwd)/$(basename "$COSUIL_EXEC")"

# ── prepare the target directory ─────────────────────────────────────────
if [ ! -d "$TARGET_DIR" ]; then
    mkdir -p "$TARGET_DIR"
    echo "Created $TARGET_DIR"
fi
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"
LINK_PATH="$TARGET_DIR/$LINK_NAME"

# ── create or refresh the symlink (idempotent) ───────────────────────────
if [ -L "$LINK_PATH" ]; then
    CURRENT="$(readlink "$LINK_PATH")"
    case "$CURRENT" in
        /*) ;;
        *) CURRENT="$(cd "$(dirname "$LINK_PATH")" && pwd)/$CURRENT" ;;
    esac
    if [ "$CURRENT" = "$COSUIL_EXEC" ]; then
        echo "Already installed: $LINK_PATH -> $COSUIL_EXEC"
    else
        ln -sfn "$COSUIL_EXEC" "$LINK_PATH"
        echo "Updated: $LINK_PATH -> $COSUIL_EXEC"
        echo "  (was: $CURRENT)"
    fi
elif [ -e "$LINK_PATH" ]; then
    echo "Error: '$LINK_PATH' already exists and is not a symlink." >&2
    echo "Remove it manually if you want cosuil installed there." >&2
    exit 1
else
    ln -s "$COSUIL_EXEC" "$LINK_PATH"
    echo "Installed: $LINK_PATH -> $COSUIL_EXEC"
fi

# ── PATH check ───────────────────────────────────────────────────────────
case ":${PATH}:" in
    *":$TARGET_DIR:"*)
        echo "OK: $TARGET_DIR is on your PATH."
        ;;
    *)
        cat >&2 << WARN
Warning: $TARGET_DIR is NOT on your PATH, so '$LINK_NAME' will not be found.

Add this to ~/.zshrc (then open a new terminal or run: source ~/.zshrc):

  export PATH="$TARGET_DIR:\$PATH"
WARN
        exit 0
        ;;
esac
