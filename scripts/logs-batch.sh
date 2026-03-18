#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LOGS_DIR="data/logs"

if [ ! -d "${LOGS_DIR}" ]; then
  echo "ERR: missing ${LOGS_DIR}" >&2
  exit 1
fi

shopt -s nullglob
root_files=("${LOGS_DIR}"/*)
shopt -u nullglob

movable=()
for path in "${root_files[@]}"; do
  if [ -f "${path}" ]; then
    movable+=("${path}")
  fi
done

if [ "${#movable[@]}" -eq 0 ]; then
  echo "OK: no root-level files to batch in ${LOGS_DIR}"
  exit 0
fi

next_num=1
shopt -s nullglob
for dir in "${LOGS_DIR}"/logs[0-9][0-9][0-9]; do
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

batch_dir="$(printf "%s/logs%03d" "${LOGS_DIR}" "${next_num}")"
mkdir -p "${batch_dir}"

for path in "${movable[@]}"; do
  mv "${path}" "${batch_dir}/"
done

echo "OK: moved ${#movable[@]} files to ${batch_dir}"
