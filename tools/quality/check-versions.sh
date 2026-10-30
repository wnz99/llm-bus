#!/bin/sh
set -eu
case "$(uv tool run --from uv==0.13.1 uv --version)" in
  "uv 0.13.1"|"uv 0.13.1 "*) ;;
  *) printf "%s\n" "Wrong uv version" >&2; exit 1 ;;
esac
uv tool run --from uv==0.13.1 uv run --frozen --no-sync python -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'
