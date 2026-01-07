from pathlib import Path
from datetime import datetime
import traceback
import csv


def ensureDataDir(dataDir: Path):
    dataDir.mkdir(parents=True, exist_ok=True)


def _log_dir(cfg) -> Path:
    """Return directory where log artifacts are written.

    We standardize on <dataDir>/logs to match the on-disk layout.
    """
    base = Path(getattr(cfg, "dataDir", Path("data")))
    d = base / "logs"
    ensureDataDir(d)
    return d


def _prefixed(name: str, symbol: str | None) -> str:
    if not symbol:
        return name
    # Keep filenames simple and deterministic.
    return f"{symbol}_{name}"


def tradeLogger(cfg, symbol: str | None = None):
    logDir = _log_dir(cfg)
    logFile = logDir / _prefixed("trades.log", symbol)

    # Ensure the file exists even if no trade happens.
    logFile.touch(exist_ok=True)

    def log(msg: str):
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with open(logFile, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")

    return log


def errorLogger(cfg, symbol: str | None = None):
    logDir = _log_dir(cfg)
    logFile = logDir / _prefixed("errors.log", symbol)

    # Ensure the file exists even if no error happens.
    logFile.touch(exist_ok=True)

    def log(msg: str, exc: Exception | None = None):
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with open(logFile, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
            if exc is not None:
                f.write(traceback.format_exc())
                if not traceback.format_exc().endswith("\n"):
                    f.write("\n")

    return log


def tradeCsvLogger(cfg, symbol: str | None = None):
    logDir = _log_dir(cfg)
    csvFile = logDir / _prefixed("trades.csv", symbol)

    def log(row: dict):
        cols = [
            "ts_utc","symbol","event","side","qty","price","reason","pnl",
            "profile","dry_run","spread_pct","mom_pct","mom_range_pct","up_ratio","rsi",
            "ema1_ok","ema5_ok","vol_ok"
        ]
        csvFile.parent.mkdir(parents=True, exist_ok=True)
        write_header = (not csvFile.exists()) or csvFile.stat().st_size == 0
        with open(csvFile, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            if write_header:
                w.writeheader()
            out = {k: row.get(k, "") for k in cols}
            w.writerow(out)

    return log


def ensureCsvHeader(cfg, symbol: str | None = None):
    """Create the per-symbol CSV with header upfront."""
    log = tradeCsvLogger(cfg, symbol)
    # Write an empty header only (row with blanks is avoided by creating file and header).
    cols = [
        "ts_utc","symbol","event","side","qty","price","reason","pnl",
        "profile","dry_run","spread_pct","mom_pct","mom_range_pct","up_ratio","rsi",
        "ema1_ok","ema5_ok","vol_ok"
    ]
    logDir = _log_dir(cfg)
    csvFile = logDir / _prefixed("trades.csv", symbol)
    if (not csvFile.exists()) or csvFile.stat().st_size == 0:
        with open(csvFile, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
