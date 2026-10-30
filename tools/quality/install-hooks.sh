#!/bin/sh
# SPDX-License-Identifier: CC0-1.0
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
if [ ! -x .githooks/pre-commit ] || [ ! -x .githooks/pre-push ]; then
  printf '%s\n' 'Executable pre-commit and pre-push stubs are required.' >&2
  exit 1
fi
if configured=$(git config --get core.hooksPath); then
  :
else
  status=$?
  [ "$status" -eq 1 ] || exit "$status"
fi
if [ "$configured" = '.githooks' ]; then
  exit 0
fi
if [ -n "$configured" ]; then
  printf '%s\n' 'Existing hooksPath preserved; integrate this baseline with its owner.' >&2
  exit 1
fi
hooks_directory=$(git rev-parse --git-path hooks)
for hook in "$hooks_directory"/*; do
  [ -e "$hook" ] || continue
  case "$hook" in *.sample) continue ;; esac
  printf '%s\n' 'Existing hooks preserved; integrate this baseline with their owner.' >&2
  exit 1
done
git config --local core.hooksPath .githooks
