#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

REMOTE_HOST="${REMOTE_HOST:-root@46.224.209.99}"
REMOTE_LOGS_DIR="${REMOTE_LOGS_DIR:-/opt/binance-aifout-bot/data/logs}"
LOCAL_LOGS_DIR="${LOCAL_LOGS_DIR:-/mnt/data/Trade/binance-aifout-bot/data/logs}"

if ! command -v scp >/dev/null 2>&1; then
  echo "ERR: scp not found" >&2
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "ERR: ssh not found" >&2
  exit 1
fi

mkdir -p "${LOCAL_LOGS_DIR}"

next_num=1
shopt -s nullglob
for dir in "${LOCAL_LOGS_DIR}"/logs[0-9][0-9][0-9]; do
  if [ -d "${dir}" ]; then
    base="$(basename "${dir}")"
    num="${base#logs}"
    if [[ "${num}" =~ ^[0-9]{3}$ ]]; then
      value=$((10#${num}))
      if [ "${value}" -ge "${next_num}" ]; then
        next_num=$((value + 1))
      fi
    fi
  fi
done
shopt -u nullglob

batch_dir="$(printf "%s/logs%03d" "${LOCAL_LOGS_DIR}" "${next_num}")"
mkdir -p "${batch_dir}"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

remote_list_cmd=$'find . -maxdepth 1 -type f \\( -name "*_trades.log" -o -name "*_trades.csv" -o -name "*_errors.log" -o -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" -o -name "*.webp" \\) -printf "%P\\n" | sort'

mapfile -t remote_files < <(ssh "${REMOTE_HOST}" "cd '${REMOTE_LOGS_DIR}' && ${remote_list_cmd}")

if [ "${#remote_files[@]}" -eq 0 ]; then
  rmdir "${batch_dir}" 2>/dev/null || true
  echo "OK: no root-level log files found on ${REMOTE_HOST}:${REMOTE_LOGS_DIR}"
  exit 0
fi

for name in "${remote_files[@]}"; do
  scp -q "${REMOTE_HOST}:${REMOTE_LOGS_DIR}/${name}" "${tmp_dir}/${name}"
done

mv "${tmp_dir}"/* "${batch_dir}/"

echo "OK: fetched ${#remote_files[@]} files into ${batch_dir}"
