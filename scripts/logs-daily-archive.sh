#!/usr/bin/env bash
set -euo pipefail

BOT_ROOT="/opt/binance-aifout-bot"
DATA_DIR="${BOT_ROOT}/data"
LOGS_DIR="${DATA_DIR}/logs"

ARCHIVE_ROOT="/opt/archivelog/binance-aifout-bot"
DAY="$(date +%Y%m%d)"
TS="$(date +%Y%m%d-%H%M%S)"

RUNTIME_DIR="${ARCHIVE_ROOT}/runtime/${DAY}"
LOGS_ARCHIVE_DIR="${ARCHIVE_ROOT}/logs/${DAY}"

mkdir -p "${RUNTIME_DIR}"
mkdir -p "${LOGS_ARCHIVE_DIR}"

journalctl -u binance-aifout-bot.service --since "24 hours ago" --no-pager \
  | gzip > "${RUNTIME_DIR}/binance-aifout-bot-${TS}.log.gz"

journalctl -u token-profile-selector.service --since "24 hours ago" --no-pager \
  | gzip > "${RUNTIME_DIR}/token-profile-selector-${TS}.log.gz"

if [ -d "${LOGS_DIR}" ] && [ "$(ls -A "${LOGS_DIR}" 2>/dev/null || true)" ]; then
  tar -czf "${LOGS_ARCHIVE_DIR}/logs-${TS}.tar.gz" -C "${DATA_DIR}" logs
  rm -rf "${LOGS_DIR}"
  mkdir -p "${LOGS_DIR}"
  chmod 755 "${LOGS_DIR}"
fi

journalctl --vacuum-size=300M >/dev/null 2>&1 || true
journalctl --vacuum-time=14d >/dev/null 2>&1 || true

exit 0

