#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

plugin_name=1635024181
plugin_path_linux=~/.local/share/Anki2/addons21
plugin_path_mac=~/Library/Application\ Support/Anki2/addons21

if [ -d "$plugin_path_linux" ]; then
    ln -s -f -n "$ROOT_DIR/plugin" "$plugin_path_linux/$plugin_name"
fi

if [ -d "$plugin_path_mac" ]; then
    ln -s -f -n "$ROOT_DIR/plugin" "$plugin_path_mac/$plugin_name"
fi
