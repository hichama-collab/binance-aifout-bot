from pathlib import Path
from datetime import datetime
import traceback
import csv

_RUN_STAMPS: dict[int, str] = {}


def local_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def local_day_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d")


def local_run_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


class LogDayContext:
    def __init__(self):
        self.anchor_day: str | None = None

    def current_day(self) -> str:
        return self.anchor_day or local_day_stamp()

    def ensure_anchor_today(self) -> str:
        if not self.anchor_day:
            self.anchor_day = local_day_stamp()
        return self.anchor_day

    def clear_anchor(self):
        self.anchor_day = None


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


def _resolve_day_stamp(day_ctx: LogDayContext | None = None) -> str:
    if day_ctx is not None:
        return day_ctx.current_day()
    return local_day_stamp()


def _day_dir(cfg, day_stamp: str | None = None) -> Path:
    day = day_stamp or local_day_stamp()
    d = _log_dir(cfg) / day
    ensureDataDir(d)
    return d


def _daily_artifact_name(symbol: str | None, day_stamp: str, suffix: str) -> str:
    if not symbol:
        return f"{day_stamp}_{suffix}"
    return f"{symbol}_{day_stamp}_{suffix}"


def tradeLogger(cfg, symbol: str | None = None, day_ctx: LogDayContext | None = None):
    def _log_file() -> Path:
        day_stamp = _resolve_day_stamp(day_ctx)
        logFile = _day_dir(cfg, day_stamp) / _daily_artifact_name(symbol, day_stamp, "trades.log")
        logFile.touch(exist_ok=True)
        return logFile

    def log(msg: str):
        ts = local_timestamp()
        logFile = _log_file()
        with open(logFile, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")

    return log


def errorLogger(cfg, symbol: str | None = None, day_ctx: LogDayContext | None = None):
    def _log_file() -> Path:
        day_stamp = _resolve_day_stamp(day_ctx)
        logFile = _day_dir(cfg, day_stamp) / _daily_artifact_name(symbol, day_stamp, "errors.log")
        logFile.touch(exist_ok=True)
        return logFile

    def log(msg: str, exc: Exception | None = None):
        ts = local_timestamp()
        logFile = _log_file()
        with open(logFile, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
            if exc is not None:
                f.write(traceback.format_exc())
                if not traceback.format_exc().endswith("\n"):
                    f.write("\n")

    return log


def tradeCsvLogger(cfg, symbol: str | None = None, day_ctx: LogDayContext | None = None):
    def log(row: dict):
        cols = [
            "ts_utc","symbol","event","side","qty","price","reason","pnl",
            "profile","dry_run","spread_pct","mom_pct","mom_range_pct","up_ratio","rsi",
            "ema1_ok","ema5_ok","vol_ok","bid","ask","mid","entry_price",
            "p1","p2","p3","p4","entry_vs_mid_pct","mid_vs_entry_pct"
        ]
        day_stamp = _resolve_day_stamp(day_ctx)
        csvFile = _day_dir(cfg, day_stamp) / _daily_artifact_name(symbol, day_stamp, "trades.csv")
        csvFile.parent.mkdir(parents=True, exist_ok=True)
        write_header = (not csvFile.exists()) or csvFile.stat().st_size == 0
        with open(csvFile, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            if write_header:
                w.writeheader()
            out = {k: row.get(k, "") for k in cols}
            w.writerow(out)

    return log


def ensureCsvHeader(cfg, symbol: str | None = None, day_ctx: LogDayContext | None = None):
    """Create the per-symbol daily CSV with header upfront."""
    cols = [
        "ts_utc","symbol","event","side","qty","price","reason","pnl",
        "profile","dry_run","spread_pct","mom_pct","mom_range_pct","up_ratio","rsi",
        "ema1_ok","ema5_ok","vol_ok","bid","ask","mid","entry_price",
        "p1","p2","p3","p4","entry_vs_mid_pct","mid_vs_entry_pct"
    ]
    day_stamp = _resolve_day_stamp(day_ctx)
    csvFile = _day_dir(cfg, day_stamp) / _daily_artifact_name(symbol, day_stamp, "trades.csv")
    if (not csvFile.exists()) or csvFile.stat().st_size == 0:
        with open(csvFile, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
