#!/usr/bin/env bash
set -euo pipefail

# VPS update script
# Simple safe update from the current branch (or the branch passed as arg)
# Usage: ./scripts/vps_update.sh [branch]

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if ! command -v git >/dev/null 2>&1; then
  echo "ERR: git not found"
  exit 1
fi

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "ERR: not a git repo"; exit 1; }

branch="${1:-$(git branch --show-current)}"
if [ -z "${branch}" ]; then
  branch="main"
fi

echo "Updating branch: ${branch}"
git fetch origin "${branch}"
git pull --rebase --autostash origin "${branch}"

if [ -x "./scripts/trade-memory-sync.sh" ]; then
  ./scripts/trade-memory-sync.sh || true
fi

echo
echo "Updated to:"
git log -1 --oneline

echo
echo "Git status:"
git status -sb
