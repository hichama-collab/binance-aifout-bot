#!/usr/bin/env bash
set -euo pipefail

# VPS update script
# Simple safe update from the current branch (or the branch passed as arg)
# Usage: ./scripts/vps_update.sh [branch]

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

trade_dir="$(cd "${repo_dir}/.." && pwd)"
trade_ssh_dir="${trade_dir}/.ssh"
if [ -f "${trade_ssh_dir}/id_ed25519" ]; then
  export GIT_SSH_COMMAND="ssh -i ${trade_ssh_dir}/id_ed25519 -o IdentitiesOnly=yes"
fi

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

# If there are local commits not in origin (e.g. accidental VPS commits), reset hard
LOCAL_AHEAD=$(git rev-list --count "origin/${branch}..HEAD" 2>/dev/null || echo 0)
if [ "${LOCAL_AHEAD}" -gt 0 ]; then
  echo "WARN: ${LOCAL_AHEAD} local commit(s) not in origin — resetting to origin/${branch}"
  git reset --hard "origin/${branch}"
fi

# Discard any local modifications to tracked files
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "WARN: local modifications detected — stashing"
  git stash push -m "vps_update auto-stash $(date +%Y%m%dT%H%M%S)" || git checkout -- .
fi

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

# Restart services if running on VPS (systemd available)
if command -v systemctl >/dev/null 2>&1 && systemctl is-system-running >/dev/null 2>&1; then
  for svc in binance-aifout-bot botdash; do
    if systemctl is-active --quiet "${svc}.service"; then
      echo "Restarting ${svc}.service ..."
      systemctl restart "${svc}.service"
    fi
  done
fi
