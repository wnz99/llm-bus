#!/bin/sh
set -eu
cd "$(git rev-parse --show-toplevel)"
uv tool run --from uv==0.13.1 uv sync --frozen --all-groups
sh tools/quality/check-versions.sh
sh tools/quality/install-hooks.sh
