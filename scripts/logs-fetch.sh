#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

repo_dir="$(pwd)"
trade_dir="$(cd "${repo_dir}/.." && pwd)"

REMOTE_HOST="${REMOTE_HOST:-root@46.224.209.99}"
REMOTE_LOGS_DIR="${REMOTE_LOGS_DIR:-/opt/binance-aifout-bot/data/logs}"
LOCAL_LOGS_DIR="${LOCAL_LOGS_DIR:-${repo_dir}/data/logs}"
SSH_KEY="${SSH_KEY:-${trade_dir}/.ssh/id_ed25519}"

if ! command -v scp >/dev/null 2>&1; then
  echo "ERR: scp not found" >&2
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "ERR: ssh not found" >&2
  exit 1
fi

mkdir -p "${LOCAL_LOGS_DIR}"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

remote_list_cmd=$'find . -type f \\( -name "*_trades.log" -o -name "*_trades.csv" -o -name "*_errors.log" -o -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" -o -name "*.webp" \\) -printf "%P\\n" | sort'

mapfile -t remote_files < <(ssh -i "${SSH_KEY}" -o IdentitiesOnly=yes "${REMOTE_HOST}" "cd '${REMOTE_LOGS_DIR}' && ${remote_list_cmd}")

if [ "${#remote_files[@]}" -eq 0 ]; then
  echo "OK: no log files found on ${REMOTE_HOST}:${REMOTE_LOGS_DIR}"
  exit 0
fi

for rel_path in "${remote_files[@]}"; do
  mkdir -p "${tmp_dir}/$(dirname "${rel_path}")"
  scp -q -i "${SSH_KEY}" -o IdentitiesOnly=yes "${REMOTE_HOST}:${REMOTE_LOGS_DIR}/${rel_path}" "${tmp_dir}/${rel_path}"
done

find "${tmp_dir}" -type f | while read -r local_file; do
  rel_path="${local_file#${tmp_dir}/}"
  mkdir -p "${LOCAL_LOGS_DIR}/$(dirname "${rel_path}")"
  mv "${local_file}" "${LOCAL_LOGS_DIR}/${rel_path}"
done

echo "OK: fetched ${#remote_files[@]} files into ${LOCAL_LOGS_DIR}"
