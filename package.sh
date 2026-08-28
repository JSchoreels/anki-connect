#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
plugin_dir="$script_dir/plugin"
output_zip="$script_dir/AnkiConnect.zip"
output_addon="$script_dir/AnkiConnect.ankiaddon"

rm -f "$output_zip" "$output_addon"
(
    cd "$plugin_dir"
    zip -r "$output_addon" . \
        -x '.DS_Store' \
        -x '*/.DS_Store' \
        -x '__pycache__/*' \
        -x '*/__pycache__/*' \
        -x '*.pyc' \
        -x 'meta.json' \
        -x 'user_files/*'
)
cp "$output_addon" "$output_zip"

echo "Created $output_addon"
echo "Created $output_zip"
