#!/usr/bin/env bash
set -euo pipefail

# VPS update script
# Force the working tree to match origin/main exactly
# Usage: ./scripts/vps-update.sh

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

# Disable sparse checkout if enabled (can hide tracked files)
if git config --bool core.sparseCheckout >/dev/null 2>&1; then
  if [ "$(git config --bool core.sparseCheckout)" = "true" ]; then
    git sparse-checkout disable >/dev/null 2>&1 || true
  fi
fi

git fetch --all --prune
git checkout -f main >/dev/null 2>&1 || git checkout -b main origin/main
git reset --hard origin/main
git clean -fd
git checkout -- .

git status -sb
git log -1 --oneline

