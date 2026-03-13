#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
plugin_dir="$script_dir/plugin"
output_zip="$script_dir/AnkiConnect.zip"

rm -f "$output_zip"
(
    cd "$plugin_dir"
    zip -r "$output_zip" .
)
