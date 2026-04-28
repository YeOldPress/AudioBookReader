#!/usr/bin/env sh
# Heaven's River Reader — Linux / macOS launcher
# Double-click in a file manager or run: sh launch.sh

cd "$(dirname "$0")"

if command -v node >/dev/null 2>&1; then
    node launch.js
else
    echo "Node.js is not installed."
    echo "Install it via your package manager, e.g.:"
    echo "  sudo apt install nodejs   # Debian/Ubuntu"
    echo "  sudo dnf install nodejs   # Fedora"
    echo "  brew install node         # macOS"
    exit 1
fi
