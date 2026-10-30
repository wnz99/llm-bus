#!/bin/sh
set -eu
repo_root=$1
snapshot_root=$2
cd "$snapshot_root"
ruff="$repo_root/.venv/bin/ruff"
[ -x "$ruff" ] || ruff="$repo_root/.venv/Scripts/ruff.exe"
"$ruff" format --check --config "$snapshot_root/pyproject.toml" src tests plugins/herdr-history tools
