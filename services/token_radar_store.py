"""SQLite store for the independent Token Radar module."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SNAPSHOT_COLUMNS = [
    "created_at", "symbol", "price", "bid", "ask", "spread_pct",
    "volume_24h", "quote_volume_24h",
    "change_5m_pct", "change_15m_pct", "change_30m_pct", "change_1h_pct",
    "change_2h_pct", "change_4h_pct", "change_24h_pct", "change_3d_pct", "change_7d_pct",
    "high_24h", "low_24h", "distance_high_24h_pct", "distance_low_24h_pct",
    "momentum_score", "liquidity_score", "spread_score", "trend_quality_score",
    "risk_score", "global_score", "signal", "reason",
]

PERIOD_COLUMNS = {
    "5m": "change_5m_pct",
    "15m": "change_15m_pct",
    "30m": "change_30m_pct",
    "1h": "change_1h_pct",
    "2h": "change_2h_pct",
    "4h": "change_4h_pct",
    "24h": "change_24h_pct",
    "3d": "change_3d_pct",
    "7d": "change_7d_pct",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_db_path(db_path: str | Path | None = None, base_dir: str | Path | None = None) -> Path:
    raw = str(db_path or os.getenv("TOKEN_RADAR_DB") or "data/token_radar.sqlite3")
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    root = Path(base_dir or os.getenv("BOT_BASE_DIR") or Path(__file__).resolve().parents[1]).expanduser()
    return (root / path).resolve()


def connect(db_path: str | Path | None = None, base_dir: str | Path | None = None) -> sqlite3.Connection:
    path = resolve_db_path(db_path, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS token_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL,
            bid REAL,
            ask REAL,
            spread_pct REAL,
            volume_24h REAL,
            quote_volume_24h REAL,
            change_5m_pct REAL,
            change_15m_pct REAL,
            change_30m_pct REAL,
            change_1h_pct REAL,
            change_2h_pct REAL,
            change_4h_pct REAL,
            change_24h_pct REAL,
            change_3d_pct REAL,
            change_7d_pct REAL,
            high_24h REAL,
            low_24h REAL,
            distance_high_24h_pct REAL,
            distance_low_24h_pct REAL,
            momentum_score REAL,
            liquidity_score REAL,
            spread_score REAL,
            trend_quality_score REAL,
            risk_score REAL,
            global_score REAL,
            signal TEXT,
            reason TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_token_snapshots_symbol_time
        ON token_snapshots(symbol, created_at);

        CREATE INDEX IF NOT EXISTS idx_token_snapshots_created_score
        ON token_snapshots(created_at, global_score);

        CREATE INDEX IF NOT EXISTS idx_token_snapshots_symbol_score
        ON token_snapshots(symbol, global_score);

        CREATE TABLE IF NOT EXISTS token_favorites (
            symbol TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            note TEXT,
            status TEXT DEFAULT 'active',
            added_price REAL,
            added_score REAL,
            last_seen_price REAL,
            last_seen_score REAL
        );

        CREATE TABLE IF NOT EXISTS token_scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            scanned_count INTEGER DEFAULT 0,
            inserted_count INTEGER DEFAULT 0,
            error TEXT
        );
        """
    )
    conn.commit()


def init_db(db_path: str | Path | None = None, base_dir: str | Path | None = None) -> Path:
    path = resolve_db_path(db_path, base_dir)
    with connect(path) as conn:
        ensure_schema(conn)
    return path


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def start_scan_run(db_path: str | Path | None = None, base_dir: str | Path | None = None) -> int:
    with connect(db_path, base_dir) as conn:
        ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO token_scan_runs(started_at, status) VALUES (?, ?)",
            (utc_now(), "running"),
        )
        conn.commit()
        return int(cur.lastrowid)


def finish_scan_run(
    run_id: int,
    *,
    status: str,
    scanned_count: int = 0,
    inserted_count: int = 0,
    error: str = "",
    db_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> None:
    with connect(db_path, base_dir) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE token_scan_runs
               SET finished_at = ?, status = ?, scanned_count = ?, inserted_count = ?, error = ?
             WHERE id = ?
            """,
            (utc_now(), status, int(scanned_count), int(inserted_count), error[:1000], int(run_id)),
        )
        conn.commit()


def insert_snapshots(
    snapshots: Iterable[dict],
    db_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> int:
    rows = []
    now = utc_now()
    for item in snapshots:
        row = {column: item.get(column) for column in SNAPSHOT_COLUMNS}
        row["created_at"] = row.get("created_at") or now
        row["symbol"] = str(row.get("symbol") or "").upper()
        if row["symbol"]:
            rows.append(row)
    if not rows:
        return 0
    placeholders = ", ".join(f":{column}" for column in SNAPSHOT_COLUMNS)
    columns = ", ".join(SNAPSHOT_COLUMNS)
    with connect(db_path, base_dir) as conn:
        ensure_schema(conn)
        conn.executemany(
            f"INSERT INTO token_snapshots({columns}) VALUES ({placeholders})",
            rows,
        )
        conn.commit()
    return len(rows)


def _latest_rows_sql(where: str = "") -> str:
    return f"""
        WITH latest AS (
            SELECT symbol, MAX(created_at) AS created_at
              FROM token_snapshots
             GROUP BY symbol
        )
        SELECT s.*
          FROM token_snapshots s
          JOIN latest l ON l.symbol = s.symbol AND l.created_at = s.created_at
        {where}
    """


def get_latest_snapshot(
    symbol: str,
    db_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> dict | None:
    with connect(db_path, base_dir) as conn:
        ensure_schema(conn)
        row = conn.execute(
            """
            SELECT * FROM token_snapshots
             WHERE symbol = ?
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()
        return _row_to_dict(row)


def get_top_tokens(
    *,
    period: str = "1h",
    min_score: float = 0.0,
    min_volume: float = 0.0,
    max_spread: float | None = None,
    limit: int = 50,
    favorites_only: bool = False,
    db_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> list[dict]:
    period_col = PERIOD_COLUMNS.get(str(period or "1h"), "change_1h_pct")
    filters = ["s.global_score >= ?", "COALESCE(s.quote_volume_24h, 0) >= ?"]
    params: list = [float(min_score), float(min_volume)]
    if max_spread is not None:
        filters.append("COALESCE(s.spread_pct, 999) <= ?")
        params.append(float(max_spread))
    join_fav = ""
    if favorites_only:
        join_fav = "JOIN token_favorites f ON f.symbol = s.symbol AND COALESCE(f.status, 'active') = 'active'"
    where = f"{join_fav} WHERE " + " AND ".join(filters)
    sql = _latest_rows_sql(where) + f" ORDER BY s.global_score DESC, COALESCE(s.{period_col}, 0) DESC LIMIT ?"
    params.append(max(1, min(int(limit), 250)))
    with connect(db_path, base_dir) as conn:
        ensure_schema(conn)
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def add_favorite(
    symbol: str,
    note: str = "",
    db_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> dict:
    symbol = str(symbol or "").upper().replace("/", "").replace("-", "")
    if symbol and not symbol.endswith("USDC"):
        symbol = f"{symbol}USDC"
    if not symbol:
        raise ValueError("symbol_required")
    snapshot = get_latest_snapshot(symbol, db_path, base_dir) or {}
    now = utc_now()
    with connect(db_path, base_dir) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO token_favorites(
                symbol, created_at, note, status, added_price, added_score,
                last_seen_price, last_seen_score
            )
            VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                note = excluded.note,
                status = 'active',
                last_seen_price = excluded.last_seen_price,
                last_seen_score = excluded.last_seen_score
            """,
            (
                symbol,
                now,
                note[:500],
                snapshot.get("price"),
                snapshot.get("global_score"),
                snapshot.get("price"),
                snapshot.get("global_score"),
            ),
        )
        conn.commit()
    return get_favorite(symbol, db_path, base_dir) or {"symbol": symbol, "note": note, "status": "active"}


def remove_favorite(
    symbol: str,
    db_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> bool:
    with connect(db_path, base_dir) as conn:
        ensure_schema(conn)
        cur = conn.execute(
            "UPDATE token_favorites SET status = 'inactive' WHERE symbol = ?",
            (str(symbol or "").upper(),),
        )
        conn.commit()
        return cur.rowcount > 0


def get_favorite(
    symbol: str,
    db_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> dict | None:
    with connect(db_path, base_dir) as conn:
        ensure_schema(conn)
        row = conn.execute(
            """
            SELECT * FROM token_favorites
             WHERE symbol = ? AND COALESCE(status, 'active') = 'active'
            """,
            (str(symbol or "").upper(),),
        ).fetchone()
        return _row_to_dict(row)


def list_favorites(db_path: str | Path | None = None, base_dir: str | Path | None = None) -> list[dict]:
    with connect(db_path, base_dir) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT symbol, MAX(created_at) AS created_at
                  FROM token_snapshots
                 GROUP BY symbol
            )
            SELECT f.*,
                   s.price AS current_price,
                   s.global_score AS current_score,
                   s.signal AS last_signal,
                   s.reason AS reason,
                   s.created_at AS last_seen_at
              FROM token_favorites f
              LEFT JOIN latest l ON l.symbol = f.symbol
              LEFT JOIN token_snapshots s ON s.symbol = l.symbol AND s.created_at = l.created_at
             WHERE COALESCE(f.status, 'active') = 'active'
             ORDER BY f.created_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_token_detail(
    symbol: str,
    *,
    limit: int = 100,
    db_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> dict:
    symbol = str(symbol or "").upper().replace("/", "").replace("-", "")
    with connect(db_path, base_dir) as conn:
        ensure_schema(conn)
        latest = conn.execute(
            "SELECT * FROM token_snapshots WHERE symbol = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        history = conn.execute(
            """
            SELECT * FROM token_snapshots
             WHERE symbol = ?
             ORDER BY created_at DESC, id DESC
             LIMIT ?
            """,
            (symbol, max(1, min(int(limit), 500))),
        ).fetchall()
    return {
        "symbol": symbol,
        "latest": _row_to_dict(latest),
        "history": [dict(row) for row in history],
    }
