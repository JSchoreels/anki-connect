#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
source_dir="$script_dir/plugin"
target_dir="$HOME/Library/Application Support/Anki2/addons21/1635024181"
stage_dir="$(mktemp -d "${TMPDIR:-/tmp}/anki-connect-sync.XXXXXX")"

cleanup() {
    rm -rf "$stage_dir"
}
trap cleanup EXIT

rsync -a \
    --exclude '.DS_Store' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'meta.json' \
    --exclude 'user_files/' \
    "$source_dir/" "$stage_dir/"

mkdir -p "$target_dir"
rsync -a --delete \
    --exclude 'meta.json' \
    --exclude 'user_files/' \
    "$stage_dir/" "$target_dir/"

echo "Synchronized AnkiConnect Extended to $target_dir"
echo "Fully quit and reopen Anki to load the updated Python modules."
