Run

Prerequisites
- Python 3.x
- Binance API key with Spot trading enabled
- Fixed public IP + Binance IP whitelist

Setup (Linux/Mac)
1) ./scripts/setup-venv.sh
2) Put your Binance keys in .env (or export env vars if your code uses that)
3) Ensure ip.txt contains your current public IP if ipguard is enabled

Start
- PROFILE=strict STRATEGY=momentum ./scripts/start.sh PEPEUSDC
- PROFILE=aggressive STRATEGY=reversal ./scripts/start.sh ZBTUSDC

Stop
- ./scripts/stop.sh

Dry run (if supported by your code)
- DRY_RUN=1 PROFILE=strict STRATEGY=momentum ./scripts/start.sh PEPEUSDC
