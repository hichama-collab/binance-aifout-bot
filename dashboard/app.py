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
SERVICE_ENV = Path(os.environ.get("BOT_SERVICE_ENV", BASE_DIR / ".service.env"))
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


def read_service_env() -> dict:
    data = {}
    try:
        with open(SERVICE_ENV, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
    except Exception:
        pass
    return data


def write_service_env(updates: dict) -> None:
    data = read_service_env()
    data.update({k: str(v).strip() for k, v in updates.items() if v is not None})
    ordered_keys = ["PROFILE", "SYMBOL", "DRY_RUN"]
    keys = ordered_keys + sorted(k for k in data if k not in ordered_keys)
    SERVICE_ENV.parent.mkdir(parents=True, exist_ok=True)
    tmp = SERVICE_ENV.with_suffix(SERVICE_ENV.suffix + ".tmp")
    lines = [f"{k}={data.get(k, '')}" for k in keys if k in data]
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(SERVICE_ENV)


def normalize_symbol(raw: str) -> str:
    symbol = (raw or "").strip().upper().replace("/", "").replace("-", "")
    if symbol and not symbol.endswith("USDC"):
        symbol = f"{symbol}USDC"
    if not re.fullmatch(r"[A-Z0-9]{2,30}USDC", symbol or ""):
        abort(400, "Invalid symbol")
    return symbol


def _boolish(value: str) -> int:
    return 1 if str(value or "").strip().lower() in ("1", "true", "yes", "on") else 0


def detect_symbol_profile() -> tuple:
    env = read_service_env()
    symbol = env.get("SYMBOL") or "UNKNOWN"
    profile = env.get("PROFILE") or "major"
    dry_run = _boolish(env.get("DRY_RUN", ""))
    return symbol, profile, dry_run


def get_control_state() -> dict:
    selector_timer = read_unit_state("token-profile-selector.timer")
    selector_service = read_unit_state("token-profile-selector.service")
    bot_service = read_unit_state("binance-aifout-bot.service")
    symbol, profile, dry_run = detect_symbol_profile()
    auto_selector = selector_timer.get("state") == "active"
    return {
        "symbol": symbol,
        "profile": profile,
        "dry_run": dry_run,
        "mode": "auto" if auto_selector else "manual",
        "auto_selector": auto_selector,
        "service_env": str(SERVICE_ENV),
        "bot": bot_service,
        "selector_timer": selector_timer,
        "selector_service": selector_service,
    }


def stop_selector() -> list:
    results = []
    for unit in ("token-profile-selector.timer", "token-profile-selector.service"):
        ok, output = systemctl("stop", unit)
        results.append({"unit": unit, "action": "stop", "ok": ok, "output": output})
    return results


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
    if "entry_price" not in pos and "entry" in pos:
        pos["entry_price"] = pos.get("entry")
    return pos


def _parse_float(raw: str) -> Optional[float]:
    try:
        return float(raw)
    except Exception:
        return None


def _line_ts_epoch(line: str) -> Optional[float]:
    m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(Z|[+-]\d{4})\]", line)
    if not m:
        return None
    try:
        raw_dt, raw_tz = m.group(1), m.group(2)
        if raw_tz == "Z":
            dt = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.strptime(f"{raw_dt}{raw_tz}", "%Y-%m-%d %H:%M:%S%z")
        return dt.timestamp()
    except Exception:
        return None


def _parse_kv_metrics(line: str) -> dict:
    fields = {}
    for key in ("MOM", "RANGE", "UP", "SPREAD", "BID", "ASK", "MID"):
        m = re.search(rf"\b{key}:([-+]?\d+(?:\.\d+)?)%?", line)
        if m:
            fields[key.lower()] = _parse_float(m.group(1))
    state = re.search(r"\bSTATE:([A-Z_]+)", line)
    if state:
        fields["state"] = state.group(1)
    return fields


def _parse_decision_line(line: str) -> dict:
    ts_epoch = _line_ts_epoch(line)
    reason = ""
    detail = ""
    m = re.search(r"DECIDE_HOLD reason=([A-Z0-9_]+)(.*)$", line)
    if m:
        reason = m.group(1)
        detail = m.group(2).strip()
    return {
        "ts_epoch": ts_epoch,
        "reason": reason,
        "detail": detail,
        **_parse_kv_metrics(line),
    }


def _parse_chk_line(line: str) -> dict:
    return {
        "ts_epoch": _line_ts_epoch(line),
        **_parse_kv_metrics(line),
    }


def _latest_symbol_log(symbol: str) -> Optional[Path]:
    if not symbol or symbol == "UNKNOWN":
        pattern = str(LOG_DIR / "**" / "*_trades.log")
    else:
        pattern = str(LOG_DIR / "**" / f"{symbol}_*_trades.log")
    return find_latest(pattern)


def _get_live_monitor(symbol: str) -> dict:
    runtime = safe_read_json(RUNTIME_DIR / "bot_status.json")
    latest_log = _latest_symbol_log(symbol)
    last_chk = {}
    last_hold = {}

    if latest_log:
        for line in reversed(tail_file(latest_log, 600)):
            if not last_hold and "DECIDE_HOLD reason=" in line:
                last_hold = _parse_decision_line(line)
            if not last_chk and "CHK " in line:
                last_chk = _parse_chk_line(line)
            if last_hold and last_chk:
                break

    ts = runtime.get("ts") or last_chk.get("ts_epoch")
    age_sec = max(0.0, time.time() - float(ts)) if ts else None
    metrics = {
        "symbol": runtime.get("symbol") or symbol,
        "state": runtime.get("state") or last_chk.get("state") or "unknown",
        "bid": runtime.get("bid") if runtime.get("bid") is not None else last_chk.get("bid"),
        "ask": runtime.get("ask") if runtime.get("ask") is not None else last_chk.get("ask"),
        "spread_pct": runtime.get("spread_pct") if runtime.get("spread_pct") is not None else last_chk.get("spread"),
        "mom_pct": runtime.get("mom_pct") if runtime.get("mom_pct") is not None else last_chk.get("mom"),
        "mom_range_pct": runtime.get("mom_range_pct") if runtime.get("mom_range_pct") is not None else last_chk.get("range"),
        "up_ratio": runtime.get("up_ratio") if runtime.get("up_ratio") is not None else last_chk.get("up"),
        "ts_epoch": ts,
        "age_sec": round(age_sec, 1) if age_sec is not None else None,
        "log_file": str(latest_log.relative_to(LOG_DIR)) if latest_log else "",
    }
    decision_age = None
    if last_hold.get("ts_epoch"):
        decision_age = max(0.0, time.time() - float(last_hold["ts_epoch"]))
    decision = {
        "reason": last_hold.get("reason") or ("NO_ENTRY_SIGNAL" if metrics["state"] == "IDLE" else ""),
        "detail": last_hold.get("detail", ""),
        "age_sec": round(decision_age, 1) if decision_age is not None else None,
        "ts_epoch": last_hold.get("ts_epoch"),
    }
    return {"metrics": metrics, "decision": decision, "runtime": runtime}


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


@app.route("/api/portfolio")
@require_basic_auth
def api_portfolio():
    portfolio = safe_read_json(RUNTIME_DIR / "portfolio.json")
    return jsonify(portfolio)


@app.route("/api/logs")
@require_basic_auth
def api_logs():
    files = []
    seen = set()
    # Collect all log/txt files, bot logs first (exclude dashboard.log from top)
    all_paths = list(LOG_DIR.rglob("*.log")) + list(LOG_DIR.rglob("*.csv"))
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
    try:
        updates = {}
        if "SYMBOL" in data:
            updates["SYMBOL"] = normalize_symbol(str(data["SYMBOL"]))
        for key in ("DRY_RUN", "PROFILE"):
            if key in data:
                updates[key] = data[key]
        write_service_env(updates)
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


@app.route("/api/ping")
@require_basic_auth
def api_ping():
    return jsonify({"ok": True})


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
        portfolio = safe_read_json(RUNTIME_DIR / "portfolio.json")
        monitor = _get_live_monitor(symbol)
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
            "portfolio": portfolio,
            "monitor": monitor,
            "pnl": _build_pnl_buckets(trades, fx),
            "stats": {
                **stats_data,
                "win_streak_max": streaks["win_streak_max"],
                "loss_streak_max": streaks["loss_streak_max"],
                "drawdown_max": drawdown,
            },
            "services": services_list,
            "control": get_control_state(),
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
                        entry = float(pos.get("entry_price", pos.get("entry", pos.get("price", 0))))
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
                                float(pos.get("ts_entry", pos.get("ts", 0))), tz=timezone.utc
                            ).isoformat() if (pos.get("ts_entry") or pos.get("ts")) else None,
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


@app.route("/api/bot/control-state")
@require_basic_auth
def api_bot_control_state():
    return jsonify(get_control_state())


@app.route("/api/bot/manual-token", methods=["POST"])
@require_token_auth
def api_bot_manual_token():
    data = request.get_json(force=True) or {}
    symbol = normalize_symbol(str(data.get("symbol", "")))
    profile = str(data.get("profile") or "").strip().lower()
    restart = bool(data.get("restart", True))
    pos = _get_position()
    if pos and not bool(data.get("force", False)):
        return jsonify({
            "ok": False,
            "error": (
                "Position ouverte detectee. Changement de token refuse pour eviter "
                "de relancer le bot sur un autre symbole."
            ),
            "position": pos,
        }), 409

    updates = {"SYMBOL": symbol}
    if profile:
        if not re.fullmatch(r"[a-z0-9_-]{2,32}", profile):
            abort(400, "Invalid profile")
        updates["PROFILE"] = profile

    selector_results = stop_selector()
    write_service_env(updates)
    restart_result = None
    if restart:
        ok, output = systemctl("restart", "binance-aifout-bot.service")
        restart_result = {"unit": "binance-aifout-bot.service", "action": "restart", "ok": ok, "output": output}
    log.warning(f"manual token selected: symbol={symbol} profile={profile or '-'} restart={restart}")
    return jsonify({
        "ok": True,
        "mode": "manual",
        "symbol": symbol,
        "selector": selector_results,
        "restart": restart_result,
        "control": get_control_state(),
    })


@app.route("/api/bot/selector/<action>", methods=["POST"])
@require_token_auth
def api_bot_selector_action(action: str):
    if action not in ("start", "stop", "restart"):
        abort(400, "Invalid action")
    results = []
    if action == "stop":
        results = stop_selector()
    else:
        timer_action = "restart" if action == "restart" else "start"
        ok, output = systemctl(timer_action, "token-profile-selector.timer")
        results.append({"unit": "token-profile-selector.timer", "action": timer_action, "ok": ok, "output": output})
        if action == "restart":
            ok, output = systemctl("restart", "token-profile-selector.service")
            results.append({"unit": "token-profile-selector.service", "action": "restart", "ok": ok, "output": output})
    log.warning(f"selector {action} requested")
    return jsonify({"ok": all(r["ok"] for r in results), "results": results, "control": get_control_state()})


@app.route("/api/bot/restart", methods=["POST"])
@require_token_auth
def api_bot_restart():
    ok, output = systemctl("restart", "binance-aifout-bot.service")
    log.warning(f"bot restart requested: ok={ok}")
    return jsonify({"ok": ok, "output": output, "control": get_control_state()})


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
    app.run(host="0.0.0.0", port=DASH_PORT, debug=False)
