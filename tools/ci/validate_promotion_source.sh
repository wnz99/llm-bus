#!/usr/bin/env bash
set -euo pipefail

if (($# != 4)); then
  echo "usage: $0 <base-branch> <head-branch> <source-repository> <target-repository>" >&2
  exit 2
fi

base_branch=$1
head_branch=$2
source_repository=$3
target_repository=$4

case "$base_branch" in
  develop)
    if [[ "$head_branch" == "staging" || "$head_branch" == "main" ]]; then
      echo "pull requests into develop cannot use reserved promotion head $head_branch" >&2
      exit 1
    fi
    ;;
  staging)
    required_head=develop
    ;;
  main)
    required_head=staging
    ;;
  *)
    echo "unsupported pull-request target: $base_branch" >&2
    exit 1
    ;;
esac

if [[ "$base_branch" != "develop" ]]; then
  if [[ -z "$source_repository" || "$source_repository" != "$target_repository" ]]; then
    echo "pull requests into $base_branch must come from repository $target_repository" >&2
    exit 1
  fi
  if [[ "$head_branch" != "$required_head" ]]; then
    echo "pull requests into $base_branch must come from $required_head, not $head_branch" >&2
    exit 1
  fi
fi
