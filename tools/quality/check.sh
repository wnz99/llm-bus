#!/bin/sh
set -eu
cd "$(git rev-parse --show-toplevel)"
uv tool run --from uv==0.13.1 uv run --frozen --no-sync --project . poe check
