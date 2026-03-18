from pathlib import Path
from datetime import datetime
import traceback
import csv

_RUN_STAMPS: dict[int, str] = {}


def local_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def local_run_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


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
    return f"{symbol}_{name}"


def _run_stamp(cfg) -> str:
    stamp = _RUN_STAMPS.get(id(cfg), "")
    if stamp:
        return stamp
    stamp = local_run_stamp()
    _RUN_STAMPS[id(cfg)] = stamp
    return stamp


def _artifact_name(cfg, symbol: str | None, suffix: str) -> str:
    if not symbol:
        return suffix
    return f"{symbol}_{_run_stamp(cfg)}_{suffix}"


def tradeLogger(cfg, symbol: str | None = None):
    logDir = _log_dir(cfg)
    logFile = logDir / _artifact_name(cfg, symbol, "trades.log")

    # Ensure the file exists even if no trade happens.
    logFile.touch(exist_ok=True)

    def log(msg: str):
        ts = local_timestamp()
        with open(logFile, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")

    return log


def errorLogger(cfg, symbol: str | None = None):
    logDir = _log_dir(cfg)
    logFile = logDir / _artifact_name(cfg, symbol, "errors.log")

    # Ensure the file exists even if no error happens.
    logFile.touch(exist_ok=True)

    def log(msg: str, exc: Exception | None = None):
        ts = local_timestamp()
        with open(logFile, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
            if exc is not None:
                f.write(traceback.format_exc())
                if not traceback.format_exc().endswith("\n"):
                    f.write("\n")

    return log


def tradeCsvLogger(cfg, symbol: str | None = None):
    logDir = _log_dir(cfg)
    csvFile = logDir / _artifact_name(cfg, symbol, "trades.csv")

    def log(row: dict):
        cols = [
            "ts_utc","symbol","event","side","qty","price","reason","pnl",
            "profile","dry_run","spread_pct","mom_pct","mom_range_pct","up_ratio","rsi",
            "ema1_ok","ema5_ok","vol_ok","bid","ask","mid","entry_price",
            "p1","p2","p3","p4","entry_vs_mid_pct","mid_vs_entry_pct"
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
        "ema1_ok","ema5_ok","vol_ok","bid","ask","mid","entry_price",
        "p1","p2","p3","p4","entry_vs_mid_pct","mid_vs_entry_pct"
    ]
    logDir = _log_dir(cfg)
    csvFile = logDir / _artifact_name(cfg, symbol, "trades.csv")
    if (not csvFile.exists()) or csvFile.stat().st_size == 0:
        with open(csvFile, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
