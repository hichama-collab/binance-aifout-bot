Configuration

risk.yaml
- Central place for risk tuning (profiles, thresholds)
- No thresholds should be hardcoded in code

Runtime env
- PROFILE: strict | aggressive
- STRATEGY: momentum | reversal
- DRY_RUN: 1 (if supported)
- Any exchange credentials should stay in .env (gitignored)

IP guard
- ip.txt should contain the allowed public IP
- Ensure Binance API key has the same IP whitelisted
