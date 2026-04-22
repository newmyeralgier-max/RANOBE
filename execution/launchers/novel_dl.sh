#!/usr/bin/env bash
# Double-click launcher for the novel downloader GUI on Linux/macOS.
# Requires Python 3.10+ (tkinter typically installed by default).

set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."
exec python3 -m novel_dl.gui
