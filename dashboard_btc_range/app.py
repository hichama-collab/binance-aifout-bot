#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "dashboard" / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))

def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None else str(v)

APP_TITLE = _env("APP_TITLE", "Hicham AIFOUT BTC Range")
BASE_DIR = Path(_env("BOT_BASE_DIR", str(REPO_ROOT))).resolve()
LOG_DIR = Path(_env("BOT_LOG_DIR", str(BASE_DIR / "data" / "logs"))).resolve()
RUNTIME_DIR = Path(_env("BOT_RUNTIME_DIR", str(BASE_DIR / "data" / "runtime"))).resolve()
RANGE_ENV = Path(_env("BTC_RANGE_ENV_FILE", str(BASE_DIR / ".btc_range.env"))).resolve()
BOT_UNIT = _env("BOT_UNIT", "btc-range-bot.service")
DASH_UNIT = _env("DASH_UNIT", "btc-range-botdash.service")
DASH_USER = _env("DASH_USER", "")
DASH_PASS = _env("DASH_PASS", "")
MAX_TAIL_LINES = 800
UNITS = [BOT_UNIT, DASH_UNIT]

@app.context_processor
def inject_branding():
    return {"app_title": APP_TITLE}

def _unauthorized():
    return Response("unauthorized", 401, {"WWW-Authenticate": f'Basic realm="{APP_TITLE}"'})

def require_basic_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not DASH_USER or not DASH_PASS:
            return Response("dashboard credentials not configured", 503)
        auth = request.authorization
        if not auth or auth.username != DASH_USER or auth.password != DASH_PASS:
            return _unauthorized()
        return fn(*args, **kwargs)
    return wrapper

def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        if not path.exists():
            return out
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip("'").strip('"')
    except Exception:
        return {}
    return out

def load_runtime_env() -> dict[str, str]:
    data = _read_env_file(RANGE_ENV)
    merged = dict(data)
    for key in ("BTC_RANGE_SYMBOL", "BTC_RANGE_PROFILE", "BTC_RANGE_DRY_RUN"):
        if os.getenv(key) is not None:
            merged[key] = str(os.getenv(key))
    return merged

def read_range_config() -> dict[str, str]:
    env = load_runtime_env()
    return {
        "symbol": (env.get("BTC_RANGE_SYMBOL") or "BTCUSDC").strip().upper(),
        "profile": (env.get("BTC_RANGE_PROFILE") or "default").strip().lower(),
        "dry_run": (env.get("BTC_RANGE_DRY_RUN") or "").strip(),
    }

def safe_read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def read_status_json() -> dict:
    data = safe_read_json(RUNTIME_DIR / "btc_range_v1_status.json")
    return data if isinstance(data, dict) else {}

def run_cmd(args: list[str]):
    proc = subprocess.run(args, capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output.strip()

def systemctl(action: str, unit: str):
    if unit not in UNITS:
        return False, f"unit not allowed: {unit}"
    if action not in ("start", "stop", "restart", "status"):
        return False, f"action not allowed: {action}"
    return run_cmd(["systemctl", action, unit])

def read_unit_state(unit: str) -> dict:
    if unit not in UNITS:
        return {"ok": False, "state": "unknown", "sub": "", "since": ""}

    ok, out = run_cmd([
        "systemctl",
        "show",
        unit,
        "--property=ActiveState",
        "--property=SubState",
        "--property=ActiveEnterTimestamp",
    ])
    if ok:
        data: dict[str, str] = {}
        for line in out.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
        since = data.get("ActiveEnterTimestamp", "")
        if since == "n/a":
            since = ""
        return {
            "ok": True,
            "state": data.get("ActiveState", "unknown") or "unknown",
            "sub": data.get("SubState", ""),
            "since": since,
        }

    return {"ok": False, "state": "unknown", "sub": "", "since": "", "error": out}

def list_symbol_logs(symbol: str, limit: int = 30) -> list[dict]:
    rows: list[dict] = []
    if not LOG_DIR.exists():
        return rows
    pattern = re.compile(rf"^{re.escape(symbol)}(?:_\d{{8}}(?:-\d{{4,6}})?)?_(trades|errors)\.(log|csv)$")
    files = []
    for path in LOG_DIR.rglob("*"):
        if not path.is_file():
            continue
        if pattern.match(path.name):
            files.append(path)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:limit]:
        stat = path.stat()
        rows.append(
            {
                "name": path.name,
                "path": str(path),
                "size": int(stat.st_size),
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return rows

def tail_file(path: Path, n_lines: int = 200) -> list[str]:
    n_lines = max(1, min(MAX_TAIL_LINES, int(n_lines)))
    try:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return [line.rstrip("\n") for line in lines[-n_lines:]]
    except Exception:
        return []

def _parse_float(value):
    try:
        if value is None:
            return None
        text = str(value).strip()
        if text == "":
            return None
        return float(text)
    except Exception:
        return None

def _parse_ts(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def load_trade_rows(symbol: str) -> list[dict]:
    rows: list[dict] = []
    if not LOG_DIR.exists():
        return rows

    for csv_path in sorted(LOG_DIR.rglob(f"{symbol}*_trades.csv")):
        try:
            with csv_path.open("r", newline="", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    event = (row.get("event") or "").strip()
                    ts = (row.get("ts_utc") or row.get("utc") or "").strip()
                    price = _parse_float(row.get("price"))
                    pnl = _parse_float(row.get("pnl"))
                    qty = _parse_float(row.get("qty"))
                    rows.append(
                        {
                            "ts_utc": ts,
                            "event": event,
                            "reason": (row.get("reason") or "").strip(),
                            "side": (row.get("side") or "").strip().upper(),
                            "price": price,
                            "pnl": pnl,
                            "qty": qty,
                            "src": csv_path.name,
                        }
                    )
        except Exception:
            continue

    rows.sort(key=lambda item: item.get("ts_utc") or "")
    return rows

def compute_stats(rows: list[dict]) -> dict:
    closed = [row for row in rows if str(row.get("event") or "").upper() == "SELL_FILLED"]
    pnl_values = [float(row.get("pnl") or 0.0) for row in closed if row.get("pnl") is not None]
    wins = [v for v in pnl_values if v > 0]
    losses = [v for v in pnl_values if v < 0]
    total = sum(pnl_values)
    last_sell = closed[-1] if closed else None
    avg = (total / len(pnl_values)) if pnl_values else None
    return {
        "closed_trades": len(closed),
        "total_pnl_usdc": round(total, 6),
        "winrate": round((len(wins) / len(pnl_values) * 100.0), 2) if pnl_values else None,
        "avg_pnl_usdc": round(avg, 6) if avg is not None else None,
        "best_trade_usdc": round(max(pnl_values), 6) if pnl_values else None,
        "worst_trade_usdc": round(min(pnl_values), 6) if pnl_values else None,
        "last_sell": last_sell,
        "recent_closed": list(reversed(closed[-12:])),
    }

# NEW: Compute additional metrics for BTC Range

def compute_range_metrics(status: dict) -> dict:
    """Extract and format range metrics from status json"""
    snapshot = status.get("snapshot") or {}
    position = status.get("position") or {}

    return {
        "bid": status.get("bid"),
        "ask": status.get("ask"),
        "spread_pct": status.get("spread_pct"),
        "low": snapshot.get("low"),
        "high": snapshot.get("high"),
        "mid": snapshot.get("mid"),
        "range_pct": snapshot.get("rangePct"),
        "drift_pct": snapshot.get("driftPct"),
        "trend_ok": snapshot.get("trendOk"),
        "atr": snapshot.get("atr"),
        "last_hold_reason": status.get("last_hold_reason"),
        "state": status.get("state"),
        "qty": position.get("qty"),
        "entry": position.get("entry"),
        "stop": position.get("stop"),
        "target": position.get("target"),
        "protectArmed": position.get("protectArmed"),
    }

@app.route("/")
@require_basic_auth
def page_dashboard():
    return render_template("dashboard.html", host=os.uname().nodename, utc=utc_now_str(), base=str(BASE_DIR), logs=str(LOG_DIR))

@app.route("/services")
@require_basic_auth
def page_services():
    return render_template("services.html", host=os.uname().nodename, utc=utc_now_str(), base=str(BASE_DIR), logs=str(LOG_DIR))

@app.route("/statistics")
@require_basic_auth
def page_statistics():
    return render_template("statistics.html", host=os.uname().nodename, utc=utc_now_str(), base=str(BASE_DIR), logs=str(LOG_DIR))

@app.route("/logs")
@require_basic_auth
def page_logs():
    return render_template("logs.html", host=os.uname().nodename, utc=utc_now_str(), base=str(BASE_DIR), logs=str(LOG_DIR))

@app.route("/api/status")
@require_basic_auth
def api_status():
    cfg = read_range_config()
    status = read_status_json()
    units = []
    for unit in UNITS:
        info = read_unit_state(unit)
        units.append(
            {
                "unit": unit,
                "state": info.get("state", "unknown"),
                "details": info.get("sub", ""),
                "since": info.get("since", ""),
            }
        )

    recent = load_trade_rows(cfg["symbol"])[-12:]
    recent.reverse()

    # NEW: Add range metrics
    range_metrics = compute_range_metrics(status)

    return jsonify(
        {
            "ok": True,
            "host": os.uname().nodename,
            "utc": utc_now_str(),
            "base": str(BASE_DIR),
            "logs": str(LOG_DIR),
            "config": cfg,
            "status": status,
            "range_metrics": range_metrics,
            "units": units,
            "recent": recent,
        }
    )

@app.route("/api/services")
@require_basic_auth
def api_services():
    payload = api_status().get_json()
    return jsonify({"ok": True, "units": payload.get("units", [])})

@app.route("/api/stats")
@require_basic_auth
def api_stats():
    cfg = read_range_config()
    rows = load_trade_rows(cfg["symbol"])
    stats = compute_stats(rows)
    return jsonify({"ok": True, "symbol": cfg["symbol"], "stats": stats})

@app.route("/api/logs")
@require_basic_auth
def api_logs():
    cfg = read_range_config()
    return jsonify({"ok": True, "files": list_symbol_logs(cfg["symbol"]), "logs": str(LOG_DIR), "base": str(BASE_DIR)})

@app.route("/api/log_tail")
@require_basic_auth
def api_log_tail():
    name = (request.args.get("name") or request.args.get("file") or "").strip()
    try:
        n_lines = int(request.args.get("n") or 200)
    except Exception:
        n_lines = 200
    if not name:
        return jsonify({"ok": False, "error": "missing file"}), 400

    path = None
    for candidate in LOG_DIR.rglob(name):
        if candidate.is_file() and candidate.name == name:
            path = candidate
            break
    if path is None:
        return jsonify({"ok": False, "error": "file not found"}), 404

    return jsonify({"ok": True, "file": name, "text": "\n".join(tail_file(path, n_lines))})

@app.route("/api/control", methods=["POST"])
@require_basic_auth
def api_control():
    payload = request.get_json(silent=True) or {}
    unit = str(payload.get("unit") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    if not unit or action not in ("start", "stop", "restart"):
        return jsonify({"ok": False, "error": "bad request"}), 400
    ok, out = systemctl(action, unit)
    return jsonify({"ok": ok, "unit": unit, "action": action, "output": out, "error": "" if ok else out})

if __name__ == "__main__":
    port = int(_env("DASH_PORT", "8100"))
    app.run(host="127.0.0.1", port=port)
