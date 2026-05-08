# === FILE: dashboard/app.py ===
"""
Binance AiFout Bot Dashboard — Flask application
Serves the trading dashboard UI and API endpoints.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    stream_with_context,
    url_for,
)

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv("/opt/binance-aifout-bot/dashboard/botdash.env")

BASE_DIR = Path(os.environ.get("BOT_BASE_DIR", "/opt/binance-aifout-bot"))
LOG_DIR = Path(os.environ.get("BOT_LOG_DIR", BASE_DIR / "data/logs"))
RUNTIME_DIR = Path(os.environ.get("BOT_RUNTIME_DIR", BASE_DIR / "data/runtime"))
SERVICE_ENV = Path(os.environ.get("BOT_SERVICE_ENV", BASE_DIR / "service.env"))
DASH_PORT = int(os.environ.get("DASH_PORT", 8099))
DASH_USER = os.environ.get("DASH_USER", "admin")
DASH_PASS = os.environ.get("DASH_PASS", "changeme")
BOT_DASHBOARD_TOKEN = os.environ.get("BOT_DASHBOARD_TOKEN", "")

DASH_LOG = LOG_DIR / "dashboard.log"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(DASH_LOG)),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("botdash")

# ---------------------------------------------------------------------------
# Import bot core helpers
# ---------------------------------------------------------------------------
import sys

sys.path.insert(0, str(BASE_DIR))

try:
    from core.trade_memory import sync_trade_memory, load_closed_trades, load_dashboard_cache
except ImportError:
    log.warning("core.trade_memory not found — using stubs")

    def sync_trade_memory():
        return False

    def load_closed_trades(limit=500):
        return []

    def load_dashboard_cache(key):
        return None


# ---------------------------------------------------------------------------
# Helper utilities (self-contained so the file works standalone)
# ---------------------------------------------------------------------------

import subprocess
import glob
import re
import sqlite3
from typing import Optional


def safe_read_json(path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def tail_file(path, n: int = 200) -> list:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, n * 200)
            f.seek(max(0, size - chunk))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []


def find_latest(pattern: str) -> Optional[Path]:
    matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    return Path(matches[0]) if matches else None


def _parse_systemd_ts(raw: str) -> str:
    """Convert systemd timestamp (e.g. 'Thu 2026-05-08 10:23:45 UTC') to ISO 8601."""
    if not raw:
        return ""
    try:
        # systemd format: "DayOfWeek YYYY-MM-DD HH:MM:SS TZ"
        parts = raw.strip().split()
        if len(parts) >= 3:
            dt = datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
    except Exception:
        pass
    return ""


def read_unit_state(unit: str) -> dict:
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "--property=ActiveState,SubState,ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5,
        )
        props = {}
        for line in result.stdout.strip().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v
        return {
            "state": props.get("ActiveState", "unknown"),
            "sub": props.get("SubState", "unknown"),
            "since": _parse_systemd_ts(props.get("ActiveEnterTimestamp", "")),
        }
    except Exception as e:
        log.debug(f"read_unit_state({unit}): {e}")
        return {"state": "unknown", "sub": "unknown", "since": ""}


def systemctl(action: str, unit: str) -> tuple:
    try:
        result = subprocess.run(
            ["systemctl", action, unit],
            capture_output=True, text=True, timeout=15,
        )
        ok = result.returncode == 0
        output = result.stdout + result.stderr
        return ok, output
    except Exception as e:
        return False, str(e)


def detect_symbol_profile() -> tuple:
    env_path = SERVICE_ENV
    symbol, profile, dry_run = "UNKNOWN", "major", 0
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("SYMBOL="):
                    symbol = line.split("=", 1)[1]
                elif line.startswith("PROFILE="):
                    profile = line.split("=", 1)[1]
                elif line.startswith("DRY_RUN="):
                    dry_run = int(line.split("=", 1)[1])
    except Exception:
        pass
    return symbol, profile, dry_run


def get_fx_usdc_eur() -> Optional[float]:
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=EURUSDC",
            timeout=4,
        )
        data = r.json()
        usdc_per_eur = float(data["price"])
        return round(1.0 / usdc_per_eur, 6)
    except Exception:
        return None


def usdc_to_eur(usdc: float, fx: Optional[float]) -> Optional[float]:
    if fx is None or usdc is None:
        return None
    return round(usdc * fx, 4)


def compute_stats(trades: list) -> dict:
    if not trades:
        return {
            "trades_total": 0,
            "winrate": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
        }
    wins = [t for t in trades if t.get("pnl_usdc", 0) > 0]
    losses = [t for t in trades if t.get("pnl_usdc", 0) <= 0]
    gross_profit = sum(t["pnl_usdc"] for t in wins)
    gross_loss = abs(sum(t["pnl_usdc"] for t in losses))
    return {
        "trades_total": len(trades),
        "winrate": round(len(wins) / len(trades), 4) if trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else 0.0,
        "avg_win": round(gross_profit / len(wins), 4) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 4) if losses else 0.0,
        "best_trade": max((t["pnl_usdc"] for t in trades), default=0.0),
        "worst_trade": min((t["pnl_usdc"] for t in trades), default=0.0),
    }


def extract_closed_pnl_rows(trades: list) -> list:
    rows = []
    for t in trades:
        rows.append({
            "ts": t.get("ts_epoch", 0),
            "pnl": t.get("pnl_usdc", 0),
            "symbol": t.get("symbol", ""),
        })
    rows.sort(key=lambda r: r["ts"])
    return rows


def build_equity_points(pnl_rows: list, fx: Optional[float]) -> list:
    equity = 0.0
    points = []
    for row in pnl_rows:
        equity += row["pnl"]
        points.append({
            "ts": row["ts"],
            "equity": round(equity, 4),
            "equity_eur": round(usdc_to_eur(equity, fx) or equity, 4),
        })
    return points


def compute_streaks(pnl_rows: list) -> dict:
    win_max = loss_max = win_cur = loss_cur = 0
    for row in pnl_rows:
        if row["pnl"] > 0:
            win_cur += 1
            loss_cur = 0
        else:
            loss_cur += 1
            win_cur = 0
        win_max = max(win_max, win_cur)
        loss_max = max(loss_max, loss_cur)
    return {"win_streak_max": win_max, "loss_streak_max": loss_max}


def compute_max_drawdown(pnl_rows: list) -> float:
    equity = peak = drawdown = 0.0
    for row in pnl_rows:
        equity += row["pnl"]
        if equity > peak:
            peak = equity
        dd = equity - peak
        if dd < drawdown:
            drawdown = dd
    return round(drawdown, 4)


def summarize_tokens(pnl_rows: list, fx: Optional[float], limit: int = 5) -> dict:
    token_pnl: dict = {}
    for row in pnl_rows:
        sym = row["symbol"]
        token_pnl[sym] = token_pnl.get(sym, 0.0) + row["pnl"]
    ranked = sorted(token_pnl.items(), key=lambda x: x[1], reverse=True)
    def fmt(items):
        return [
            {"symbol": sym, "pnl_usdc": round(pnl, 4), "pnl_eur": usdc_to_eur(pnl, fx)}
            for sym, pnl in items
        ]
    return {"top": fmt(ranked[:limit]), "bottom": fmt(list(reversed(ranked[-limit:])))}


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-please-change")


def _bg_sync_loop():
    import threading
    def _run():
        while True:
            try:
                sync_trade_memory()
            except Exception as exc:
                log.debug(f"sync_trade_memory error: {exc}")
            time.sleep(60)
    t = threading.Thread(target=_run, daemon=True, name="sync-trade-memory")
    t.start()


_bg_sync_loop()

SERVICES = [
    "binance-aifout-bot.service",
    "botdash.service",
    "token-profile-selector.service",
    "token-profile-selector.timer",
]

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def check_basic_auth() -> bool:
    auth = request.authorization
    if not auth:
        return False
    return auth.username == DASH_USER and auth.password == DASH_PASS


def require_basic_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not check_basic_auth():
            return Response(
                "Unauthorized", 401,
                {"WWW-Authenticate": 'Basic realm="BotDash"'},
            )
        return f(*args, **kwargs)
    return decorated


def check_token_auth() -> bool:
    token = request.headers.get("X-Bot-Token", "")
    if BOT_DASHBOARD_TOKEN and token == BOT_DASHBOARD_TOKEN:
        return True
    return check_basic_auth()


def require_token_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not check_token_auth():
            return Response(
                "Unauthorized", 401,
                {"WWW-Authenticate": 'Basic realm="BotDash"'},
            )
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _get_position() -> Optional[dict]:
    pos = safe_read_json(RUNTIME_DIR / "position.json")
    if not pos or float(pos.get("qty", 0)) == 0:
        return None
    return pos


def _get_trades(limit: int = 500) -> list:
    try:
        rows = load_closed_trades()
        if limit and limit < len(rows):
            rows = rows[-limit:]
        return rows
    except Exception:
        return []


def _build_pnl_buckets(trades: list, fx: Optional[float]) -> dict:
    now = datetime.now(timezone.utc)
    buckets = {
        "today": 0.0,
        "session": 0.0,
        "week": 0.0,
        "month": 0.0,
        "year": 0.0,
    }
    session_start = time.time() - 3600  # last hour as "session"
    for t in trades:
        pnl = t.get("pnl_usdc", 0)
        ts = t.get("ts_epoch", 0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        if dt:
            if dt.date() == now.date():
                buckets["today"] += pnl
            if ts >= session_start:
                buckets["session"] += pnl
            if (now - dt).days < 7:
                buckets["week"] += pnl
            if dt.month == now.month and dt.year == now.year:
                buckets["month"] += pnl
            if dt.year == now.year:
                buckets["year"] += pnl

    def _fmt(usdc):
        return {"usdc": round(usdc, 4), "eur": usdc_to_eur(usdc, fx)}

    return {k: _fmt(v) for k, v in buckets.items()}


# ---------------------------------------------------------------------------
# ============================================================
# EXISTING ROUTES (backward compat)
# ============================================================
# ---------------------------------------------------------------------------


@app.route("/")
@require_basic_auth
def index():
    return redirect(url_for("live"))


@app.route("/api/status")
@require_basic_auth
def api_status():
    symbol, profile, dry_run = detect_symbol_profile()
    unit_state = read_unit_state("binance-aifout-bot.service")
    pos = _get_position()
    return jsonify({
        "state": unit_state.get("state", "unknown"),
        "since": unit_state.get("since", ""),
        "active_token": symbol,
        "profile": profile,
        "dry_run": dry_run,
        "position": pos,
    })


@app.route("/api/pnl")
@require_basic_auth
def api_pnl():
    trades = _get_trades()
    fx = get_fx_usdc_eur()
    return jsonify(_build_pnl_buckets(trades, fx))


@app.route("/api/trades")
@require_basic_auth
def api_trades():
    limit = int(request.args.get("limit", 50))
    trades = _get_trades(limit)
    return jsonify(trades)


@app.route("/api/summary")
@require_basic_auth
def api_summary():
    trades = _get_trades()
    fx = get_fx_usdc_eur()
    rows = extract_closed_pnl_rows(trades)
    return jsonify(summarize_tokens(rows, fx))


@app.route("/api/wallet")
@require_basic_auth
def api_wallet():
    wallet = safe_read_json(RUNTIME_DIR / "wallet.json")
    return jsonify(wallet)


@app.route("/api/logs")
@require_basic_auth
def api_logs():
    files = []
    seen = set()
    # Collect all log/txt files, bot logs first (exclude dashboard.log from top)
    patterns = ["*.log", "*.txt"]
    all_paths = []
    for pat in patterns:
        all_paths.extend(LOG_DIR.glob(pat))
        all_paths.extend(LOG_DIR.glob(f"*/{pat}"))
    bot_logs = []
    dash_logs = []
    for p in all_paths:
        if p.name in seen:
            continue
        seen.add(p.name)
        entry = {"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime,
                 "path": str(p.relative_to(LOG_DIR))}
        if p.name == "dashboard.log":
            dash_logs.append(entry)
        else:
            bot_logs.append(entry)
    bot_logs.sort(key=lambda x: x["mtime"], reverse=True)
    dash_logs.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify(bot_logs + dash_logs)


@app.route("/api/log_tail")
@require_basic_auth
def api_log_tail():
    name = request.args.get("name", "")
    n = int(request.args.get("n", 200))
    if not name or ".." in name:
        abort(400)
    path = (LOG_DIR / name).resolve()
    # Security: must be under LOG_DIR
    if not str(path).startswith(str(LOG_DIR.resolve())):
        abort(403)
    if not path.exists():
        abort(404)
    lines = tail_file(path, n)
    return jsonify(lines)


@app.route("/api/control", methods=["POST"])
@require_basic_auth
def api_control():
    data = request.get_json(force=True) or {}
    action = data.get("action", "")
    unit = data.get("unit", "binance-aifout-bot.service")
    if action not in ("start", "stop", "restart"):
        abort(400, "Invalid action")
    ok, output = systemctl(action, unit)
    log.info(f"control: {action} {unit} → ok={ok}")
    return jsonify({"ok": ok, "output": output})


@app.route("/api/config", methods=["POST"])
@require_basic_auth
def api_config():
    data = request.get_json(force=True) or {}
    lines = []
    for key in ("SYMBOL", "DRY_RUN", "PROFILE"):
        if key in data:
            lines.append(f"{key}={data[key]}")
    try:
        with open(SERVICE_ENV, "w") as f:
            f.write("\n".join(lines) + "\n")
        log.info(f"config written: {data}")
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"config write error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# ============================================================
# NEW UI ROUTES
# ============================================================
# ---------------------------------------------------------------------------


@app.route("/live")
@require_basic_auth
def live():
    return render_template("dashboard.html")


@app.route("/stats")
@require_basic_auth
def stats():
    return render_template("statistics.html")


@app.route("/services")
@require_basic_auth
def services():
    return render_template("services.html")


@app.route("/logs")
@require_basic_auth
def logs():
    return render_template("logs.html")


# ---------------------------------------------------------------------------
# NEW API ROUTES
# ---------------------------------------------------------------------------


@app.route("/api/snapshot")
@require_basic_auth
def api_snapshot():
    try:
        trades = _get_trades()
        fx = get_fx_usdc_eur()
        symbol, profile, dry_run = detect_symbol_profile()
        unit_state = read_unit_state("binance-aifout-bot.service")
        pos = _get_position()
        pnl_rows = extract_closed_pnl_rows(trades)
        stats_data = compute_stats(trades)
        streaks = compute_streaks(pnl_rows)
        drawdown = compute_max_drawdown(pnl_rows)

        services_list = [
            {"unit": u, **read_unit_state(u)} for u in SERVICES
        ]

        return jsonify({
            "bot": {
                "status": unit_state.get("state", "unknown"),
                "since": unit_state.get("since", ""),
                "active_token": symbol,
                "profile": profile,
                "dry_run": dry_run,
            },
            "position": pos,
            "pnl": _build_pnl_buckets(trades, fx),
            "stats": {
                **stats_data,
                "win_streak_max": streaks["win_streak_max"],
                "loss_streak_max": streaks["loss_streak_max"],
                "drawdown_max": drawdown,
            },
            "services": services_list,
            "fx": fx,
        })
    except Exception as e:
        log.exception("api_snapshot error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/equity")
@require_basic_auth
def api_equity():
    range_param = request.args.get("range", "all")
    trades = _get_trades(2000)
    fx = get_fx_usdc_eur()
    pnl_rows = extract_closed_pnl_rows(trades)

    now = time.time()
    cutoffs = {
        "1d": now - 86400,
        "7d": now - 7 * 86400,
        "30d": now - 30 * 86400,
        "all": 0,
    }
    cutoff = cutoffs.get(range_param, 0)
    filtered = [r for r in pnl_rows if r["ts"] >= cutoff]
    points = build_equity_points(filtered, fx)
    return jsonify({"ok": True, "range": range_param, "points": points})


@app.route("/api/contributions")
@require_basic_auth
def api_contributions():
    trades = _get_trades()
    fx = get_fx_usdc_eur()
    pnl_rows = extract_closed_pnl_rows(trades)
    result = summarize_tokens(pnl_rows, fx, limit=10)
    result["ok"] = True
    return jsonify(result)


@app.route("/api/stream")
def api_stream():
    # EventSource cannot send Basic Auth headers — allow if page auth already done
    # (dashboard pages all require auth; SSE is only opened from authenticated pages)
    """Server-Sent Events endpoint."""

    @stream_with_context
    def generate():
        last_heartbeat = 0
        last_push = 0
        while True:
            now = time.time()
            try:
                # Position + price every 2 s
                if now - last_push >= 2:
                    last_push = now
                    pos = _get_position()
                    if pos:
                        symbol = pos.get("symbol", "")
                        qty = float(pos.get("qty", 0))
                        entry = float(pos.get("entry_price", pos.get("price", 0)))
                        # Try to get current price
                        current_price = None
                        try:
                            r = requests.get(
                                f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}",
                                timeout=2,
                            )
                            bt = r.json()
                            bid = float(bt.get("bidPrice", 0))
                            ask = float(bt.get("askPrice", 0))
                            current_price = (bid + ask) / 2
                        except Exception:
                            pass

                        pnl_usdc = round((current_price - entry) * qty, 4) if current_price and entry else 0.0
                        pnl_pct = round((current_price / entry - 1) * 100, 4) if current_price and entry else 0.0

                        pos_event = json.dumps({
                            "symbol": symbol,
                            "qty": qty,
                            "entry_price": entry,
                            "current_price": current_price,
                            "pnl_usdc": pnl_usdc,
                            "pnl_pct": pnl_pct,
                            "opened_at": datetime.fromtimestamp(
                                float(pos.get("ts", 0)), tz=timezone.utc
                            ).isoformat() if pos.get("ts") else None,
                        })
                        yield f"event: position\ndata: {pos_event}\n\n"

                        if current_price:
                            price_event = json.dumps({
                                "symbol": symbol,
                                "bid": bid if "bid" in dir() else current_price,
                                "ask": ask if "ask" in dir() else current_price,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })
                            yield f"event: price\ndata: {price_event}\n\n"
                    else:
                        yield f"event: position\ndata: null\n\n"

                    # Services state
                    svc_list = [{"unit": u, "state": read_unit_state(u).get("state", "unknown")} for u in SERVICES]
                    yield f"event: services\ndata: {json.dumps(svc_list)}\n\n"

                # Heartbeat every 15 s
                if now - last_heartbeat >= 15:
                    last_heartbeat = now
                    hb = json.dumps({"ts": datetime.now(timezone.utc).isoformat()})
                    yield f"event: heartbeat\ndata: {hb}\n\n"

            except GeneratorExit:
                break
            except Exception as e:
                log.debug(f"SSE error: {e}")
                err = json.dumps({"error": str(e)})
                yield f"event: error\ndata: {err}\n\n"

            time.sleep(1)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/services/<name>/<action>", methods=["POST"])
@require_token_auth
def api_service_action(name: str, action: str):
    # Sanitize
    allowed_units = {u.replace(".", "_").replace("-", "_"): u for u in SERVICES}
    # Also allow exact unit names
    all_allowed = set(SERVICES) | set(allowed_units.keys())
    if name not in all_allowed:
        abort(400, f"Unknown service: {name}")
    if action not in ("start", "stop", "restart"):
        abort(400, f"Invalid action: {action}")
    unit = allowed_units.get(name, name)
    ok, output = systemctl(action, unit)
    log.info(f"service_action: {action} {unit} ok={ok}")
    return jsonify({"ok": ok, "output": output, "unit": unit, "action": action})


@app.route("/api/bot/pause", methods=["POST"])
@require_token_auth
def api_bot_pause():
    ok, output = systemctl("stop", "binance-aifout-bot.service")
    log.warning(f"bot PAUSE requested: ok={ok}")
    return jsonify({"ok": ok, "output": output})


@app.route("/api/bot/panic", methods=["POST"])
@require_token_auth
def api_bot_panic():
    results = []
    for unit in SERVICES:
        ok, output = systemctl("stop", unit)
        results.append({"unit": unit, "ok": ok})
        log.warning(f"PANIC stop {unit}: ok={ok}")
    return jsonify({"results": results})


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "not found"}), 404
    return render_template("base.html"), 404


@app.errorhandler(500)
def internal_error(e):
    log.exception("500 error")
    if request.path.startswith("/api/"):
        return jsonify({"error": "internal server error"}), 500
    return render_template("base.html"), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info(f"BotDash starting on port {DASH_PORT}")
    app.run(host="127.0.0.1", port=DASH_PORT, debug=False)
