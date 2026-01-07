Operations

Logs and CSV
- data/logs/<SYMBOL>_trades.log: runtime decisions, state changes
- data/logs/<SYMBOL>_errors.log: exceptions and exchange errors
- data/logs/<SYMBOL>_trades.csv: single source of truth for iteration

Clean
- ./scripts/clean-logs.sh ALL
- ./scripts/clean-csv.sh ALL
- ./scripts/clean-all.sh ALL
- Per symbol: ./scripts/clean-all.sh PEPEUSDC

Legacy
- scripts/legacy/: old scripts preserved as-is
- docs/legacy/: old specs and notes preserved as-is
- legacy/backups/: old *.bak preserved
