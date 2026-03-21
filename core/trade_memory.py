import csv
import hashlib
import os
import sqlite3
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


def _scan_trade_file(path: Path, logs_dir: Path):
    rel_src = path.resolve().relative_to(logs_dir.resolve()).as_posix()
    closed_rows = []
    with path.open("r", newline="", encoding="utf-8", errors="ignore") as handle:
        reader = csv.DictReader(handle)
        fallback_symbol = _symbol_from_filename(path)
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
                "src": rel_src,
            })
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


def sync_trade_memory(logs_dir: Path | str | None = None, db_path: Path | str | None = None) -> dict:
    root = resolve_bot_root()
    logs_dir = Path(logs_dir).resolve() if logs_dir is not None else resolve_logs_dir(root)
    db_path = Path(db_path).resolve() if db_path is not None else resolve_memory_db_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        imported_closed = 0
        scanned_files = 0
        skipped_files = 0
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
        conn.commit()
        scored_tokens = refresh_token_scores(conn)
        return {
            "ok": True,
            "logs_dir": str(logs_dir),
            "db_path": str(db_path),
            "scanned_files": scanned_files,
            "skipped_files": skipped_files,
            "imported_closed_trades": imported_closed,
            "scored_tokens": scored_tokens,
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
