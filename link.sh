#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
plugin_dir="$script_dir/plugin"
manifest_path="$plugin_dir/manifest.json"
plugin_name="$(sed -n 's/.*"package": "\(.*\)".*/\1/p' "$manifest_path")"
plugin_path_linux="$HOME/.local/share/Anki2/addons21"
plugin_path_mac="$HOME/Library/Application Support/Anki2/addons21"

link_addon() {
    local addons_dir="$1"
    local target="$addons_dir/$plugin_name"

    if [ ! -d "$addons_dir" ]; then
        return
    fi

    rm -rf "$target"
    ln -s "$plugin_dir" "$target"
}

link_addon "$plugin_path_linux"
link_addon "$plugin_path_mac"
