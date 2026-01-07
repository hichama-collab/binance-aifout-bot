README (short)

Run (real):
PROFILE=aggressive STRATEGY=momentum python3 main.py ATUSDC

Run (reversal entry):
PROFILE=aggressive STRATEGY=reversal python3 main.py ATUSDC

Dry-run:
DRY_RUN=1 PROFILE=strict STRATEGY=momentum python3 main.py ATUSDC

Logs:
- data/trades.log (live)
- data/errors.log (crash)
- data/trades.csv (analysis)

Analysis:
python3 tools/analyze_trades.py data/trades.csv

Tuning:
Edit config/risk.yaml (profiles + strategies). No code changes.
