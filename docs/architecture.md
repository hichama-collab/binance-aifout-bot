Architecture (reference)

Root
- main.py: orchestration runtime loop, entry/exit, wallet sync

Core modules
- core/: runtime config, yaml loading, logging helpers
- config/: central tuning (risk.yaml)

Trading
- strategy/: entry logic only (momentum, reversal)
- state/: position state (tp, trailing, breakeven, time stop)
- execution/: LIMIT orders, fill/cancel
- exchange/: REST + WebSocket bookTicker
- indicators/: shared indicators
- services/: infra guards (ipguard)

Data and observability
- data/logs/: *_trades.log, *_errors.log, *_trades.csv
- tools/: analysis utilities (offline)

Rules
- strategies do entry only
- exits are common and centralized
- no hardcoded thresholds: tune via config/risk.yaml
