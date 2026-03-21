import csv
import hashlib
import io
import json
import os
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path


HISTORY_WINDOW = max(1, int(os.getenv("TOKEN_SCORE_HISTORY_WINDOW", "5")))
TOXIC_MIN_TRADES = max(1, int(os.getenv("TOKEN_SCORE_TOXIC_MIN_TRADES", "3")))
TOXIC_LOSS_STREAK = max(1, int(os.getenv("TOKEN_SCORE_TOXIC_LOSS_STREAK", "3")))
TOXIC_PNL_USDC = float(os.getenv("TOKEN_SCORE_TOXIC_PNL_USDC", "-0.15"))
TOXIC_WINRATE = float(os.getenv("TOKEN_SCORE_TOXIC_WINRATE", "40.0"))
HISTORY_BONUS_STRONG = float(os.getenv("TOKEN_SCORE_HISTORY_BONUS_STRONG", "0.20"))
HISTORY_BONUS_LIGHT = float(os.getenv("TOKEN_SCORE_HISTORY_BONUS_LIGHT", "0.10"))
HISTORY_BONUS_PENALTY = float(os.getenv("TOKEN_SCORE_HISTORY_BONUS_PENALTY", "-0.10"))
DASHBOARD_CACHE_KEY = "stats_v3"


def resolve_bot_root() -> Path:
    raw = (os.getenv("BOT_ROOT_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    return Path(__file__).resolve().parents[1]


def resolve_logs_dir(root: Path | None = None) -> Path:
    raw = (os.getenv("BOT_LOG_DIR") or os.getenv("LOG_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    return (root or resolve_bot_root()).joinpath("data", "logs").resolve()


def resolve_memory_db_path(root: Path | None = None) -> Path:
    raw = (os.getenv("TRADE_MEMORY_DB_PATH") or os.getenv("TOKEN_SCORE_DB_PATH") or "").strip()
    if raw:
        return Path(raw).resolve()
    return (root or resolve_bot_root()).joinpath("data", "runtime", "trade_memory.sqlite3").resolve()


def resolve_archive_logs_dir(root: Path | None = None) -> Path:
    for env_name in (
        "TRADE_MEMORY_ARCHIVE_LOGS_DIR",
        "BOT_LOG_ARCHIVE_DIR",
        "BOT_ARCHIVE_LOG_DIR",
    ):
        raw = (os.getenv(env_name) or "").strip()
        if raw:
            return Path(raw).resolve()

    default = Path("/opt/archivelog/binance-aifout-bot/logs")
    if default.exists():
        return default.resolve()

    local_default = (root or resolve_bot_root()).joinpath("data", "logs-archive")
    if local_default.exists():
        return local_default.resolve()

    return default.resolve()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value):
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def _parse_trade_ts(value) -> tuple[str, float]:
    if value is None:
        return "", 0.0
    raw = str(value).strip()
    if not raw:
        return "", 0.0
    try:
        if raw.isdigit() and len(raw) == 10:
            dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)
            return dt.isoformat(), dt.timestamp()
        if raw.isdigit() and len(raw) == 13:
            dt = datetime.fromtimestamp(float(raw) / 1000.0, tz=timezone.utc)
            return dt.isoformat(), dt.timestamp()
    except Exception:
        return raw, 0.0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.isoformat(), dt.timestamp()
    except Exception:
        return raw, 0.0


def _format_num(value) -> str:
    if value is None:
        return ""
    return f"{float(value):.12g}"


def _symbol_from_filename(path: Path) -> str:
    name = path.name
    if name.endswith("_trades.csv"):
        stem = name[:-11]
        if "_" in stem:
            maybe_symbol = stem.split("_", 1)[0]
            if maybe_symbol:
                return maybe_symbol.strip().upper()
        return stem.strip().upper()
    return path.stem.strip().upper()


def _build_trade_key(symbol: str, ts_utc: str, qty, entry_price, exit_price, pnl_usdc, reason: str) -> str:
    payload = "|".join([
        symbol,
        ts_utc,
        _format_num(qty),
        _format_num(entry_price),
        _format_num(exit_price),
        _format_num(pnl_usdc),
        str(reason or "").strip().upper(),
    ])
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()


def _iter_trade_csv_paths(logs_dir: Path):
    if not logs_dir.exists():
        return
    for path in sorted(logs_dir.rglob("*_trades.csv")):
        if path.is_file():
            yield path


def _iter_trade_archive_paths(archive_logs_dir: Path):
    if not archive_logs_dir.exists():
        return
    for path in sorted(archive_logs_dir.rglob("*")):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.endswith(".tar.gz") or name.endswith(".tgz") or name.endswith(".tar"):
            yield path


def _ensure_schema(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closed_trades (
            trade_key TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            ts_epoch REAL NOT NULL DEFAULT 0,
            qty REAL,
            entry_price REAL,
            exit_price REAL,
            buy_usdc REAL,
            sell_usdc REAL,
            pnl_usdc REAL NOT NULL,
            pnl_pct REAL,
            reason TEXT NOT NULL DEFAULT '',
            src TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_closed_trades_symbol_ts
        ON closed_trades(symbol, ts_epoch DESC, ts_utc DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_scores (
            symbol TEXT PRIMARY KEY,
            closed_trades INTEGER NOT NULL DEFAULT 0,
            recent_trades INTEGER NOT NULL DEFAULT 0,
            winrate_5 REAL,
            pnl_usdc_5 REAL,
            avg_pnl_pct_5 REAL,
            loss_streak INTEGER NOT NULL DEFAULT 0,
            is_toxic INTEGER NOT NULL DEFAULT 0,
            toxic_reasons TEXT NOT NULL DEFAULT '',
            history_bonus REAL NOT NULL DEFAULT 0,
            last_closed_ts TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_files (
            src TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            mtime REAL NOT NULL DEFAULT 0,
            scanned_at TEXT NOT NULL,
            closed_rows INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dashboard_cache (
            cache_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _file_is_unchanged(conn: sqlite3.Connection, rel_src: str, size_bytes: int, mtime: float) -> bool:
    row = conn.execute(
        "SELECT size_bytes, mtime FROM source_files WHERE src = ?",
        (rel_src,),
    ).fetchone()
    if not row:
        return False
    return int(row[0] or 0) == int(size_bytes) and float(row[1] or 0.0) == float(mtime)


def _upsert_source_file(conn: sqlite3.Connection, rel_src: str, size_bytes: int, mtime: float, closed_rows: int):
    conn.execute(
        """
        INSERT INTO source_files(src, size_bytes, mtime, scanned_at, closed_rows)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(src) DO UPDATE SET
            size_bytes = excluded.size_bytes,
            mtime = excluded.mtime,
            scanned_at = excluded.scanned_at,
            closed_rows = excluded.closed_rows
        """,
        (rel_src, int(size_bytes), float(mtime), _now_iso(), int(closed_rows)),
    )


def _scan_trade_rows(reader, fallback_symbol: str, src_label: str):
    closed_rows = []
    for row in reader:
        event = str(row.get("event") or "").strip().upper()
        if event != "SELL_FILLED":
            continue
        pnl_usdc = _to_float(row.get("pnl"))
        if pnl_usdc is None:
            continue
        symbol = str(row.get("symbol") or fallback_symbol).strip().upper()
        if not symbol:
            continue
        qty = _to_float(row.get("qty"))
        entry_price = _to_float(row.get("entry_price"))
        exit_price = _to_float(row.get("price"))
        buy_usdc = qty * entry_price if qty is not None and entry_price is not None else None
        sell_usdc = qty * exit_price if qty is not None and exit_price is not None else None
        if buy_usdc is None and sell_usdc is not None:
            buy_usdc = sell_usdc - pnl_usdc
        if sell_usdc is None and buy_usdc is not None:
            sell_usdc = buy_usdc + pnl_usdc
        pnl_pct = None
        if buy_usdc is not None and abs(buy_usdc) > 1e-12:
            pnl_pct = pnl_usdc / buy_usdc * 100.0
        ts_utc, ts_epoch = _parse_trade_ts(row.get("ts_utc") or row.get("ts") or row.get("timestamp"))
        reason = str(row.get("reason") or "").strip()
        trade_key = _build_trade_key(symbol, ts_utc, qty, entry_price, exit_price, pnl_usdc, reason)
        closed_rows.append({
            "trade_key": trade_key,
            "symbol": symbol,
            "ts_utc": ts_utc,
            "ts_epoch": ts_epoch,
            "qty": qty,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "buy_usdc": buy_usdc,
            "sell_usdc": sell_usdc,
            "pnl_usdc": pnl_usdc,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "src": src_label,
        })
    return closed_rows


def _scan_trade_file(path: Path, logs_dir: Path):
    rel_src = path.resolve().relative_to(logs_dir.resolve()).as_posix()
    with path.open("r", newline="", encoding="utf-8", errors="ignore") as handle:
        reader = csv.DictReader(handle)
        closed_rows = _scan_trade_rows(reader, _symbol_from_filename(path), rel_src)
    return rel_src, closed_rows


def _archive_source_key(path: Path, archive_logs_dir: Path) -> str:
    rel = path.resolve().relative_to(archive_logs_dir.resolve()).as_posix()
    return f"archive_logs/{rel}"


def _scan_trade_archive(path: Path, archive_logs_dir: Path):
    rel_src = _archive_source_key(path, archive_logs_dir)
    closed_rows = []
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith("_trades.csv"):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            with io.TextIOWrapper(handle, encoding="utf-8", errors="ignore", newline="") as text_handle:
                reader = csv.DictReader(text_handle)
                member_src = f"{rel_src}::{member.name}"
                closed_rows.extend(_scan_trade_rows(reader, _symbol_from_filename(Path(member.name)), member_src))
    return rel_src, closed_rows


def _insert_closed_trade(conn: sqlite3.Connection, row: dict) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO closed_trades(
            trade_key, symbol, ts_utc, ts_epoch, qty, entry_price, exit_price,
            buy_usdc, sell_usdc, pnl_usdc, pnl_pct, reason, src
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["trade_key"],
            row["symbol"],
            row["ts_utc"],
            float(row["ts_epoch"] or 0.0),
            row["qty"],
            row["entry_price"],
            row["exit_price"],
            row["buy_usdc"],
            row["sell_usdc"],
            row["pnl_usdc"],
            row["pnl_pct"],
            row["reason"],
            row["src"],
        ),
    )
    return cur.rowcount > 0


def _compute_loss_streak(recent_rows: list[sqlite3.Row]) -> int:
    streak = 0
    for row in recent_rows:
        pnl = float(row["pnl_usdc"] or 0.0)
        if pnl < 0:
            streak += 1
            continue
        break
    return streak


def _compute_history_bonus(closed_count: int, pnl_usdc_window: float, winrate_window: float | None, is_toxic: bool) -> float:
    if is_toxic or closed_count < TOXIC_MIN_TRADES:
        return 0.0
    if pnl_usdc_window > 0 and winrate_window is not None and winrate_window >= 60.0:
        return HISTORY_BONUS_STRONG
    if pnl_usdc_window > 0:
        return HISTORY_BONUS_LIGHT
    if pnl_usdc_window < 0 and winrate_window is not None and winrate_window < 50.0:
        return HISTORY_BONUS_PENALTY
    return 0.0


def refresh_token_scores(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM token_scores")
    symbols = [row[0] for row in conn.execute("SELECT DISTINCT symbol FROM closed_trades ORDER BY symbol")]
    now = _now_iso()
    for symbol in symbols:
        rows = conn.execute(
            """
            SELECT symbol, ts_utc, pnl_usdc, pnl_pct
            FROM closed_trades
            WHERE symbol = ?
            ORDER BY ts_epoch DESC, ts_utc DESC
            """,
            (symbol,),
        ).fetchall()
        if not rows:
            continue
        recent = rows[:HISTORY_WINDOW]
        recent_count = len(recent)
        win_count = sum(1 for row in recent if float(row["pnl_usdc"] or 0.0) > 0)
        winrate_window = (win_count / recent_count * 100.0) if recent_count else None
        pnl_usdc_window = sum(float(row["pnl_usdc"] or 0.0) for row in recent)
        pnl_pct_vals = [float(row["pnl_pct"]) for row in recent if row["pnl_pct"] is not None]
        avg_pnl_pct_window = (sum(pnl_pct_vals) / len(pnl_pct_vals)) if pnl_pct_vals else None
        loss_streak = _compute_loss_streak(recent)
        toxic_checks = {
            "loss_streak": loss_streak >= TOXIC_LOSS_STREAK,
            "pnl_usdc_window": pnl_usdc_window <= TOXIC_PNL_USDC,
            "winrate_window": winrate_window is not None and winrate_window < TOXIC_WINRATE,
        }
        toxic_hits = sum(1 for ok in toxic_checks.values() if ok)
        is_toxic = len(rows) >= TOXIC_MIN_TRADES and toxic_hits >= 2
        toxic_reasons = ",".join(name for name, ok in toxic_checks.items() if ok) if is_toxic else ""
        history_bonus = _compute_history_bonus(len(rows), pnl_usdc_window, winrate_window, is_toxic)
        conn.execute(
            """
            INSERT INTO token_scores(
                symbol, closed_trades, recent_trades, winrate_5, pnl_usdc_5, avg_pnl_pct_5,
                loss_streak, is_toxic, toxic_reasons, history_bonus, last_closed_ts, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                len(rows),
                recent_count,
                winrate_window,
                pnl_usdc_window,
                avg_pnl_pct_window,
                loss_streak,
                int(bool(is_toxic)),
                toxic_reasons,
                history_bonus,
                str(rows[0]["ts_utc"] or ""),
                now,
            ),
        )
    conn.commit()
    return len(symbols)


def _round_value(value, digits: int = 6):
    if value is None:
        return None
    return round(float(value), digits)


def _bucket_payload(rows: list[dict]) -> dict:
    pnl_vals = [float(row.get("pnl_usdc") or 0.0) for row in rows]
    net = sum(pnl_vals)
    wins = [v for v in pnl_vals if v > 0]
    losses = [v for v in pnl_vals if v < 0]
    return {
        "usdc": _round_value(net, 6) or 0.0,
        "trades": len(pnl_vals),
        "winrate": _round_value(len(wins) / len(pnl_vals) * 100.0, 2) if pnl_vals else None,
        "profit_factor": (
            _round_value(sum(wins) / abs(sum(losses)), 3)
            if losses
            else (_round_value(sum(wins), 3) if wins else None)
        ),
    }


def _trade_cache_item(row: dict) -> dict:
    return {
        "ts": str(row.get("ts_utc") or ""),
        "ts_utc": str(row.get("ts_utc") or ""),
        "symbol": str(row.get("symbol") or "").strip().upper(),
        "event": "SELL_FILLED",
        "price": _round_value(row.get("exit_price"), 12),
        "qty": _round_value(row.get("qty"), 12),
        "entry_price": _round_value(row.get("entry_price"), 12),
        "pnl_usdc": _round_value(row.get("pnl_usdc"), 6) or 0.0,
        "reason": str(row.get("reason") or ""),
        "src": str(row.get("src") or ""),
    }


def _compute_streaks(rows: list[dict]) -> tuple[int, int]:
    best_win = 0
    best_loss = 0
    cur_win = 0
    cur_loss = 0
    for row in rows:
        pnl = float(row.get("pnl_usdc") or 0.0)
        if pnl > 0:
            cur_win += 1
            cur_loss = 0
        elif pnl < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = 0
            cur_loss = 0
        best_win = max(best_win, cur_win)
        best_loss = max(best_loss, cur_loss)
    return best_win, best_loss


def _compute_max_drawdown(rows: list[dict]) -> float:
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for row in rows:
        cum += float(row.get("pnl_usdc") or 0.0)
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return _round_value(max_dd, 6) or 0.0


def _dashboard_cache_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM dashboard_cache WHERE cache_key = ?",
        (DASHBOARD_CACHE_KEY,),
    ).fetchone()
    return row is not None


def _build_dashboard_cache(conn: sqlite3.Connection) -> dict:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                symbol,
                ts_utc,
                ts_epoch,
                qty,
                entry_price,
                exit_price,
                buy_usdc,
                sell_usdc,
                pnl_usdc,
                pnl_pct,
                reason,
                src
            FROM closed_trades
            ORDER BY ts_epoch ASC, ts_utc ASC, symbol ASC
            """
        )
    ]

    pnl_vals = [float(row.get("pnl_usdc") or 0.0) for row in rows]
    wins = [v for v in pnl_vals if v > 0]
    losses = [v for v in pnl_vals if v < 0]
    total_realized = _round_value(sum(pnl_vals), 6) or 0.0
    winrate = _round_value(len(wins) / len(pnl_vals) * 100.0, 2) if pnl_vals else 0.0
    profit_factor = (
        _round_value(sum(wins) / abs(sum(losses)), 3)
        if losses
        else (_round_value(sum(wins), 3) if wins else 0.0)
    )

    cum_pnl = []
    pnl_samples = []
    equity_points = []
    cum = 0.0
    for row in rows:
        pnl = float(row.get("pnl_usdc") or 0.0)
        cum += pnl
        cum_pnl.append(_round_value(cum, 6) or 0.0)
        pnl_samples.append(_round_value(pnl, 6) or 0.0)
        equity_points.append({
            "ts": str(row.get("ts_utc") or ""),
            "usdc": _round_value(cum, 6) or 0.0,
            "symbol": str(row.get("symbol") or "").strip().upper(),
        })

    best_win, best_loss = _compute_streaks(rows)
    best_trade = _round_value(max(pnl_vals), 6) if pnl_vals else None
    worst_trade = _round_value(min(pnl_vals), 6) if pnl_vals else None
    avg_win = _round_value(sum(wins) / len(wins), 6) if wins else None
    avg_loss = _round_value(sum(losses) / len(losses), 6) if losses else None

    daily = {}
    token_agg = {}
    for row in rows:
        ts_utc = str(row.get("ts_utc") or "")
        day = ts_utc[:10] if len(ts_utc) >= 10 else ""
        pnl = float(row.get("pnl_usdc") or 0.0)
        if day:
            item = daily.setdefault(day, {
                "date": day,
                "usdc": 0.0,
                "trades": 0,
                "wins_count": 0,
                "losses_count": 0,
                "wins_sum": 0.0,
                "losses_sum": 0.0,
            })
            item["usdc"] += pnl
            item["trades"] += 1
            if pnl > 0:
                item["wins_count"] += 1
                item["wins_sum"] += pnl
            elif pnl < 0:
                item["losses_count"] += 1
                item["losses_sum"] += pnl

        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            agg = token_agg.setdefault(symbol, {
                "symbol": symbol,
                "pnl_usdc": 0.0,
                "trades": 0,
                "last_ts": "",
                "buy_usdc": 0.0,
                "sell_usdc": 0.0,
                "max_open_usdc": 0.0,
            })
            agg["pnl_usdc"] += pnl
            agg["trades"] += 1
            agg["last_ts"] = max(agg["last_ts"], ts_utc)
            if row.get("buy_usdc") is not None:
                buy_usdc = float(row.get("buy_usdc") or 0.0)
                agg["buy_usdc"] += buy_usdc
                agg["max_open_usdc"] = max(agg["max_open_usdc"], buy_usdc)
            if row.get("sell_usdc") is not None:
                agg["sell_usdc"] += float(row.get("sell_usdc") or 0.0)

    daily_buckets = []
    for item in sorted(daily.values(), key=lambda x: x["date"]):
        wins_sum = float(item["wins_sum"] or 0.0)
        losses_sum = float(item["losses_sum"] or 0.0)
        trades = int(item["trades"] or 0)
        wins_count = int(item["wins_count"] or 0)
        losses_count = int(item["losses_count"] or 0)
        daily_buckets.append({
            "date": item["date"],
            "usdc": _round_value(item["usdc"], 6) or 0.0,
            "trades": trades,
            "wins_count": wins_count,
            "losses_count": losses_count,
            "wins_sum": _round_value(wins_sum, 6) or 0.0,
            "losses_sum": _round_value(losses_sum, 6) or 0.0,
            "winrate": _round_value(wins_count / trades * 100.0, 2) if trades else None,
            "profit_factor": (
                _round_value(wins_sum / abs(losses_sum), 3)
                if losses_sum < 0
                else (_round_value(wins_sum, 3) if wins_sum > 0 else None)
            ),
        })

    token_rows = []
    for item in token_agg.values():
        pnl_usdc = float(item["pnl_usdc"] or 0.0)
        buy_usdc = float(item["buy_usdc"] or 0.0)
        sell_usdc = float(item["sell_usdc"] or 0.0)
        pnl_pct = (pnl_usdc / buy_usdc * 100.0) if abs(buy_usdc) > 1e-12 else None
        token_rows.append({
            "symbol": item["symbol"],
            "pnl_usdc": _round_value(pnl_usdc, 6) or 0.0,
            "buy_usdc": _round_value(buy_usdc, 6) or 0.0,
            "sell_usdc": _round_value(sell_usdc, 6) or 0.0,
            "max_open_usdc": _round_value(item["max_open_usdc"], 6) or 0.0,
            "pnl_pct": _round_value(pnl_pct, 3),
            "trades": int(item["trades"] or 0),
            "last_ts": item["last_ts"],
        })

    token_rows.sort(key=lambda row: (row.get("last_ts") or "", abs(float(row.get("pnl_usdc") or 0.0))), reverse=True)
    rows_by_best = sorted(token_rows, key=lambda row: (row["pnl_usdc"], row["trades"], row["symbol"]), reverse=True)
    top = rows_by_best[:5]
    top_symbols = {row["symbol"] for row in top}
    bottom = [
        row for row in sorted(token_rows, key=lambda row: (row["pnl_usdc"], -row["trades"], row["symbol"]))
        if row["symbol"] not in top_symbols
    ][:5]

    recent_closed = [_trade_cache_item(row) for row in reversed(rows[-120:])]
    last_trade = recent_closed[0] if recent_closed else None
    session_source = str(last_trade.get("src") or "") if last_trade else ""
    session_rows = [row for row in rows if str(row.get("src") or "") == session_source] if session_source else []
    session = _bucket_payload(session_rows)
    session["source"] = session_source

    return {
        "generated_at": _now_iso(),
        "overall": {
            "total_realized": total_realized,
            "winrate": winrate,
            "profit_factor": profit_factor,
            "trades": len(pnl_vals),
            "cum_pnl": cum_pnl[-400:],
            "pnl_samples": pnl_samples[-400:],
        },
        "quality": {
            "avg_win_usdc": avg_win,
            "avg_loss_usdc": avg_loss,
            "best_trade_usdc": best_trade,
            "worst_trade_usdc": worst_trade,
            "max_drawdown_usdc": _compute_max_drawdown(rows),
            "longest_win_streak": best_win,
            "longest_loss_streak": best_loss,
        },
        "last_trade": last_trade,
        "recent_closed": recent_closed[:12],
        "last_trades": recent_closed[:30],
        "equity_points": equity_points[-400:],
        "session": session,
        "daily_buckets": daily_buckets,
        "tokens": token_rows[:200],
        "token_rankings": {"top": top, "bottom": bottom},
        "total_usdc": total_realized,
    }


def refresh_dashboard_cache(conn: sqlite3.Connection) -> dict:
    payload = _build_dashboard_cache(conn)
    conn.execute(
        """
        INSERT INTO dashboard_cache(cache_key, payload_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (DASHBOARD_CACHE_KEY, json.dumps(payload, ensure_ascii=True), _now_iso()),
    )
    conn.commit()
    return payload


def sync_trade_memory(
    logs_dir: Path | str | None = None,
    db_path: Path | str | None = None,
    archive_logs_dir: Path | str | None = None,
) -> dict:
    root = resolve_bot_root()
    logs_dir = Path(logs_dir).resolve() if logs_dir is not None else resolve_logs_dir(root)
    db_path = Path(db_path).resolve() if db_path is not None else resolve_memory_db_path(root)
    archive_logs_dir = (
        Path(archive_logs_dir).resolve()
        if archive_logs_dir is not None
        else resolve_archive_logs_dir(root)
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        imported_closed = 0
        scanned_files = 0
        skipped_files = 0
        scanned_archives = 0
        skipped_archives = 0
        for path in _iter_trade_csv_paths(logs_dir):
            stat = path.stat()
            rel_src = path.resolve().relative_to(logs_dir.resolve()).as_posix()
            if _file_is_unchanged(conn, rel_src, stat.st_size, stat.st_mtime):
                skipped_files += 1
                continue
            scanned_files += 1
            rel_src, closed_rows = _scan_trade_file(path, logs_dir)
            for row in closed_rows:
                if _insert_closed_trade(conn, row):
                    imported_closed += 1
            _upsert_source_file(conn, rel_src, stat.st_size, stat.st_mtime, len(closed_rows))
        for path in _iter_trade_archive_paths(archive_logs_dir):
            stat = path.stat()
            rel_src = _archive_source_key(path, archive_logs_dir)
            if _file_is_unchanged(conn, rel_src, stat.st_size, stat.st_mtime):
                skipped_archives += 1
                continue
            scanned_archives += 1
            rel_src, closed_rows = _scan_trade_archive(path, archive_logs_dir)
            for row in closed_rows:
                if _insert_closed_trade(conn, row):
                    imported_closed += 1
            _upsert_source_file(conn, rel_src, stat.st_size, stat.st_mtime, len(closed_rows))
        conn.commit()
        current_scores = int(conn.execute("SELECT COUNT(*) FROM token_scores").fetchone()[0] or 0)
        needs_refresh = scanned_files > 0 or scanned_archives > 0
        if needs_refresh or current_scores == 0:
            scored_tokens = refresh_token_scores(conn)
        else:
            scored_tokens = current_scores
        dashboard_cache_refreshed = False
        if needs_refresh or not _dashboard_cache_exists(conn):
            refresh_dashboard_cache(conn)
            dashboard_cache_refreshed = True
        return {
            "ok": True,
            "logs_dir": str(logs_dir),
            "db_path": str(db_path),
            "archive_logs_dir": str(archive_logs_dir),
            "scanned_files": scanned_files,
            "skipped_files": skipped_files,
            "scanned_archives": scanned_archives,
            "skipped_archives": skipped_archives,
            "imported_closed_trades": imported_closed,
            "scored_tokens": scored_tokens,
            "dashboard_cache_refreshed": dashboard_cache_refreshed,
        }
    finally:
        conn.close()


def load_token_scores(db_path: Path | str | None = None) -> dict[str, dict]:
    root = resolve_bot_root()
    db_path = Path(db_path).resolve() if db_path is not None else resolve_memory_db_path(root)
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        out = {}
        for row in conn.execute("SELECT * FROM token_scores ORDER BY symbol"):
            item = dict(row)
            item["is_toxic"] = bool(item.get("is_toxic"))
            out[str(item.get("symbol") or "").upper()] = item
        return out
    finally:
        conn.close()


def load_closed_trades(db_path: Path | str | None = None) -> list[dict]:
    root = resolve_bot_root()
    db_path = Path(db_path).resolve() if db_path is not None else resolve_memory_db_path(root)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = []
        for row in conn.execute(
            """
            SELECT
                symbol,
                ts_utc,
                ts_epoch,
                qty,
                entry_price,
                exit_price,
                buy_usdc,
                sell_usdc,
                pnl_usdc,
                pnl_pct,
                reason,
                src
            FROM closed_trades
            ORDER BY ts_epoch ASC, ts_utc ASC, symbol ASC
            """
        ):
            rows.append(dict(row))
        return rows
    finally:
        conn.close()


def load_dashboard_cache(db_path: Path | str | None = None) -> dict:
    root = resolve_bot_root()
    db_path = Path(db_path).resolve() if db_path is not None else resolve_memory_db_path(root)
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        try:
            row = conn.execute(
                "SELECT payload_json FROM dashboard_cache WHERE cache_key = ?",
                (DASHBOARD_CACHE_KEY,),
            ).fetchone()
        except sqlite3.OperationalError:
            return {}
        if not row:
            return {}
        try:
            return json.loads(str(row["payload_json"] or "{}"))
        except Exception:
            return {}
    finally:
        conn.close()
