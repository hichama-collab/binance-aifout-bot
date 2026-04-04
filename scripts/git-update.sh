#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"

if ! command -v git >/dev/null 2>&1; then
  echo "ERR: git not found"
  exit 1
fi

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "ERR: not a git repo"; exit 1; }

branch="${1:-$(git branch --show-current)}"
if [ -z "${branch}" ]; then
  branch="main"
fi

git pull --rebase --autostash origin "${branch}"
git status -sb
