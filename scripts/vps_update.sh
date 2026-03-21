#!/usr/bin/env bash
set -euo pipefail

# VPS update script
# Update code from origin/main while preserving local runtime/state files
# Usage: ./scripts/vps-update.sh

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

backup_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$backup_dir"
}
trap cleanup EXIT

collect_preserved_files() {
  {
    git ls-files 'data/runtime/*' 2>/dev/null || true
    [ -f ".service.env" ] && printf '%s\n' ".service.env"
    [ -f "dashboard/botdash.env" ] && printf '%s\n' "dashboard/botdash.env"
    find "data/runtime" -maxdepth 1 -type f \( -name "*.sqlite3" -o -name "*.db" \) 2>/dev/null || true
  } | awk 'NF' | sort -u
}

mapfile -t preserved_files < <(collect_preserved_files)

backup_runtime_file() {
  local path="$1"
  [ -f "$path" ] || return 0
  mkdir -p "$backup_dir/$(dirname "$path")"
  cp -p "$path" "$backup_dir/$path"
}

restore_runtime_file() {
  local path="$1"
  [ -f "$backup_dir/$path" ] || return 0
  mkdir -p "$(dirname "$path")"
  cp -p "$backup_dir/$path" "$path"
}

for path in "${preserved_files[@]}"; do
  backup_runtime_file "$path"
done

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

for path in "${preserved_files[@]}"; do
  restore_runtime_file "$path"
done

echo "Updated to:"
git log -1 --oneline
if [ "${#preserved_files[@]}" -gt 0 ]; then
  echo
  echo "Preserved runtime files:"
  printf '  %s\n' "${preserved_files[@]}"
fi

echo
echo "Git status (runtime files may appear modified by design):"
git status -sb
