#!/usr/bin/env bash
set -euo pipefail

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "ERR: not a git repo"; exit 1; }

MSG="${1:-}"
if [ -z "$MSG" ]; then
  echo "Usage: ./scripts/git-commit.sh "message""
  exit 1
fi

git add -A
git commit -m "$MSG"
git status -sb
