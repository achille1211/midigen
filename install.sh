#!/bin/sh
# midigen installer for macOS and Linux: ./install.sh  (options are passed to install.py, see ./install.sh --help)
cd "$(dirname "$0")" || exit 1
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    exec "$py" install.py "$@"
  fi
done
echo "midigen install: PROBLEM" >&2
echo "  Python 3.10 or newer was not found." >&2
echo "  macOS: install it from https://www.python.org/downloads/ (or: brew install python)" >&2
echo "  Linux: sudo apt install python3 python3-venv   (Fedora: sudo dnf install python3)" >&2
echo "  then run ./install.sh again." >&2
exit 1
