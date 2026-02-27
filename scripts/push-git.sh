#!/usr/bin/env bash
set -euo pipefail

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "ERR: not a git repo"; exit 1; }

git push
git status -sb
