#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
botdash - lightweight Flask dashboard for binance-aifout-bot

Goals of this file:
- Never crash (avoid 500) even if runtime/log files are missing/corrupted.
- Keep API response JSON-serializable (no Path objects).
- Keep signatures consistent (load_trades_csv() takes no args).
"""

from __future__ import annotations

import base64
import csv
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from flask import Flask, jsonify, render_template, request, Response

# -----------------------------
# App / Config
# -----------------------------

app = Flask(__name__, template_folder="templates", static_folder="static")

def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None else str(v)

DASH_USER = _env("DASH_USER", "admin")
DASH_PASS = _env("DASH_PASS", "pwd123")

BASE_DIR = Path(_env("BOT_BASE_DIR", Path(__file__).resolve().parents[1]))
LOG_DIR = Path(_env("BOT_LOG_DIR", str(BASE_DIR / "data" / "logs")))
RUNTIME_DIR = Path(_env("BOT_RUNTIME_DIR", str(BASE_DIR / "data" / "runtime")))
SERVICE_ENV = Path(_env("BOT_SERVICE_ENV", str(BASE_DIR / ".service.env")))
DASH_PORT = int(_env("DASH_PORT", "8099") or "8099")

# Wallet filter: hide rows with computed USDC value < threshold
WALLET_HIDE_LT_USDC = float(_env("WALLET_HIDE_LT_USDC", "1") or "1")

# -----------------------------
# Helpers: auth / time / json
# -----------------------------

def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def _json_safe(x: Any) -> Any:
    if isinstance(x, Path):
        return str(x)
    if isinstance(x, (datetime,)):
        return x.isoformat()
    if isinstance(x, dict):
        return {str(k): _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    return x

def _unauthorized() -> Response:
    r = Response("unauthorized", 401)
    r.headers["WWW-Authenticate"] = 'Basic realm="botdash"'
    return r

def require_basic_auth(fn):
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return _unauthorized()
        try:
            userpass = base64.b64decode(auth.split(" ", 1)[1].strip()).decode("utf-8", errors="ignore")
            user, pw = userpass.split(":", 1)
        except Exception:
            return _unauthorized()
        if user != DASH_USER or pw != DASH_PASS:
            return _unauthorized()
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper

def safe_read_text(p: Path, max_bytes: int = 2_000_000) -> str:
    try:
        if not p.exists():
            return ""
        data = p.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""

def safe_read_json(p: Path) -> Optional[dict]:
    try:
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None

def read_runtime_json(filename: str) -> dict:
    """
    Read JSON file from BOT_RUNTIME_DIR. Never raises.
    """
    try:
        p = (RUNTIME_DIR / filename)
        j = safe_read_json(p)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}

# -----------------------------
# Helpers: service env (token now)
# -----------------------------

def read_service_env() -> Dict[str, str]:
    if not SERVICE_ENV.exists():
        return {}
    out: Dict[str, str] = {}
    for line in SERVICE_ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out

def detect_symbol_profile() -> Tuple[str, str, str]:
    env = read_service_env()
    symbol = env.get("SYMBOL") or env.get("TOKEN") or env.get("SYMBOLUSDC") or ""
    profile = env.get("PROFILE") or ""
    dry_run = env.get("DRY_RUN") or ""
    return symbol, profile, dry_run

# -----------------------------
# FX: USDC -> EUR (cached)
# -----------------------------

FX_CACHE_TTL_SEC = int(_env("FX_CACHE_TTL_SEC", "120") or "120")
_FX_CACHE: Dict[str, Any] = {"ts": 0.0, "usdc_eur": None}

def _fetch_fx_usdc_eur_binance() -> Optional[float]:
    # EURUSDC price = USDC per 1 EUR => USDC->EUR = 1/price
    url = "https://api.binance.com/api/v3/ticker/price?symbol=EURUSDC"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "botdash"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        price = float(data.get("price"))
        if price <= 0:
            return None
        return 1.0 / price
    except Exception:
        return None

def get_fx_usdc_eur() -> Optional[float]:
    # 1) env override
    env_v = _env("FX_USDC_EUR", "")
    if env_v:
        try:
            return float(env_v)
        except Exception:
            pass

    # 2) runtime fx.json
    fxj = safe_read_json(RUNTIME_DIR / "fx.json") or {}
    for k in ("usdc_eur", "FX_USDC_EUR"):
        if k in fxj:
            try:
                return float(fxj[k])
            except Exception:
                pass

    # 3) cached fetch
    now = time.time()
    if (now - float(_FX_CACHE.get("ts", 0.0))) < FX_CACHE_TTL_SEC:
        return _FX_CACHE.get("usdc_eur")

    fx = _fetch_fx_usdc_eur_binance()
    _FX_CACHE["ts"] = now
    _FX_CACHE["usdc_eur"] = fx

    # best-effort persist
    if fx is not None:
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            (RUNTIME_DIR / "fx.json").write_text(json.dumps({"usdc_eur": fx, "ts": utc_now_str()}, indent=2), encoding="utf-8")
        except Exception:
            pass

    return fx

def usdc_to_eur(usdc: Optional[float], fx: Optional[float]) -> Optional[float]:
    try:
        if usdc is None or fx is None:
            return None
        return float(usdc) * float(fx)
    except Exception:
        return None

# -----------------------------
# Trades CSV
# -----------------------------

def _parse_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        s = str(x).strip()
        if s == "":
            return None
        return float(s)
    except Exception:
        return None

def load_trades_csv() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Scan BOT_LOG_DIR for "*_trades.csv" and load all rows.
    Returns: (trades_list, meta_dict)
    trades_list items include: ts_utc, symbol, side, qty, price, pnl
    """
    meta: Dict[str, Any] = {"files": [], "rows": 0, "errors": 0}
    trades: List[Dict[str, Any]] = []

    try:
        paths = sorted(LOG_DIR.glob("*_trades.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        paths = []

    meta["files"] = [str(p) for p in paths[:200]]

    for p in paths:
        try:
            with p.open("r", encoding="utf-8", errors="ignore", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    if not isinstance(row, dict):
                        continue
                    sym = (row.get("symbol") or "").strip().upper()
                    if not sym:
                        continue
                    t = {
                        "ts_utc": (row.get("ts_utc") or row.get("ts") or row.get("time") or "").strip(),
                        "symbol": sym,
                        "side": (row.get("side") or "").strip().upper(),
                        "qty": _parse_float(row.get("qty")),
                        "price": _parse_float(row.get("price")),
                        "pnl": _parse_float(row.get("pnl")),
                        "_file": str(p),
                    }
                    trades.append(t)
                    meta["rows"] += 1
        except Exception:
            meta["errors"] += 1

    return trades, meta

def summarize_trades_by_symbol(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Aggregate trade rows into per-symbol summaries:
    {symbol, trades, net_usdc, first_ts, last_ts, net_eur}
    """
    fx = get_fx_usdc_eur()
    agg: Dict[str, Dict[str, Any]] = {}

    for t in trades:
        sym = (t.get("symbol") or "").strip().upper()
        if not sym:
            continue
        pnl = _parse_float(t.get("pnl"))
        ts = (t.get("ts_utc") or "").strip()
        a = agg.get(sym)
        if a is None:
            a = {"symbol": sym, "trades": 0, "net_usdc": 0.0, "first_ts": ts, "last_ts": ts}
            agg[sym] = a
        a["trades"] += 1
        if pnl is not None:
            a["net_usdc"] += float(pnl)
        if ts:
            if not a.get("first_ts"):
                a["first_ts"] = ts
            a["last_ts"] = ts

    rows: List[Dict[str, Any]] = []
    for sym, a in agg.items():
        net_usdc = float(a.get("net_usdc", 0.0))
        rows.append({
            "symbol": sym,
            "trades": int(a.get("trades", 0)),
            "net_usdc": round(net_usdc, 6),
            "net_eur": round(usdc_to_eur(net_usdc, fx), 6) if fx is not None else None,
            "first_ts": a.get("first_ts") or "",
            "last_ts": a.get("last_ts") or "",
        })

    # newest first (by last_ts lexicographic ISO), fallback by trades
    rows.sort(key=lambda r: (r.get("last_ts") or "", r.get("trades") or 0), reverse=True)
    return rows

# -----------------------------
# Wallet
# -----------------------------

def load_wallet_snapshot() -> Tuple[str, Any]:
    for name in ("wallet.json", "balances.json", "spot_wallet.json", "account.json"):
        p = RUNTIME_DIR / name
        j = safe_read_json(p)
        if j is not None:
            return str(p), j
    return "", None

def normalize_wallet(wallet_data: Any) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    fx = get_fx_usdc_eur()
    rows: List[Dict[str, Any]] = []

    if wallet_data is None:
        return rows, fx

    # shapes supported:
    # - {"balances":[{"asset":"BTC","free":"0.1","locked":"0"}]}
    # - [{"asset":"USDC","free":...}]
    balances = None
    if isinstance(wallet_data, dict):
        balances = wallet_data.get("balances") or wallet_data.get("spot") or wallet_data.get("assets") or wallet_data.get("data")
    if balances is None and isinstance(wallet_data, list):
        balances = wallet_data

    if balances is None:
        return rows, fx

    if isinstance(balances, dict):
        it = []
        for k, v in balances.items():
            if isinstance(v, dict):
                it.append({"asset": k, **v})
        balances = it

    for b in balances if isinstance(balances, list) else []:
        if not isinstance(b, dict):
            continue
        asset = (b.get("asset") or b.get("symbol") or b.get("coin") or "").strip().upper()
        if not asset:
            continue

        free = _parse_float(b.get("free") or b.get("available") or b.get("qty"))
        locked = _parse_float(b.get("locked") or b.get("freeze"))
        total = _parse_float(b.get("total"))
        if total is None:
            total = (free or 0.0) + (locked or 0.0)

        # optional precomputed values
        v_usdc = _parse_float(b.get("value_usdc") or b.get("usdc_value") or b.get("quote_usdc") or b.get("equity_usdc"))
        v_eur = _parse_float(b.get("value_eur") or b.get("eur_value") or b.get("equity_eur"))
        if v_usdc is None and asset == "USDC":
            v_usdc = total
        if v_eur is None and v_usdc is not None and fx is not None:
            v_eur = usdc_to_eur(v_usdc, fx)

        # Hide tiny rows (< threshold USDC). If value missing, treat as 0 and hide.
        comp_usdc = float(v_usdc) if v_usdc is not None else 0.0
        if WALLET_HIDE_LT_USDC > 0 and comp_usdc < WALLET_HIDE_LT_USDC:
            continue

        rows.append({
            "asset": asset,
            "free": round(free, 8) if free is not None else 0.0,
            "locked": round(locked, 8) if locked is not None else 0.0,
            "total": round(total, 8) if total is not None else 0.0,
            "value_usdc": round(v_usdc, 6) if v_usdc is not None else None,
            "value_eur": round(v_eur, 6) if v_eur is not None else None,
        })

    rows.sort(key=lambda r: (r.get("value_usdc") is not None, r.get("value_usdc") or 0.0), reverse=True)
    return rows, fx

# -----------------------------
# Logs helpers
# -----------------------------

def list_recent_logs(limit: int = 40) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        files = []
        for pat in ("*_trades.log", "*_errors.log", "*.log", "*.csv"):
            files.extend(LOG_DIR.glob(pat))
        # unique
        uniq = {}
        for p in files:
            uniq[str(p)] = p
        files = list(uniq.values())
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[:limit]:
            out.append({
                "name": p.name,
                "path": str(p),
                "size": int(p.stat().st_size),
                "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
    except Exception:
        pass
    return out

def tail_file(p: Path, n: int = 200) -> List[str]:
    try:
        txt = safe_read_text(p, max_bytes=400_000)
        lines = txt.splitlines()[-max(1, int(n)):]
        return lines
    except Exception:
        return []

# -----------------------------
# Pages
# -----------------------------

@app.route("/")
@require_basic_auth
def index():
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

# -----------------------------
# API
# -----------------------------

@app.route("/api/status")
@require_basic_auth
def api_status():
    symbol, profile, dry_run = detect_symbol_profile()
    pos = read_runtime_json("position.json")
    return jsonify(_json_safe({
        "ok": True,
        "ts_utc": utc_now_str(),
        "token": {"symbol": symbol, "profile": profile, "dry_run": dry_run},
        "position": pos,
    }))

@app.route("/api/services")
@require_basic_auth
def api_services():
    # Dashboard UI expects ok + units list. We keep a minimal placeholder.
    # If you later want systemd integration, add it here (but keep non-failing behavior).
    return jsonify({"ok": True, "units": []})

@app.route("/api/token_now")
@require_basic_auth
def api_token_now():
    symbol, profile, dry_run = detect_symbol_profile()
    return jsonify({"ok": True, "token": {"symbol": symbol, "profile": profile, "dry_run": dry_run}})

@app.route("/api/wallet")
@require_basic_auth
def api_wallet():
    file_path, raw = load_wallet_snapshot()
    rows, fx = normalize_wallet(raw)
    return jsonify(_json_safe({
        "ok": True,
        "file": file_path,
        "fx_usdc_eur": fx,
        "rows": rows,
    }))

@app.route("/api/trades")
@require_basic_auth
def api_trades():
    trades, meta = load_trades_csv()
    by_sym = summarize_trades_by_symbol(trades)

    # UI: last tokens traded list. Keep only symbols with trades > 0 and include last_ts period.
    # Some UIs want "period" field; we provide it.
    out = []
    for r in by_sym:
        out.append({
            "symbol": r["symbol"],
            "period": r.get("last_ts") or "",
            "net_usdc": r.get("net_usdc", 0.0),
            "net_eur": r.get("net_eur"),
            "trades": r.get("trades", 0),
            "first_ts": r.get("first_ts",""),
            "last_ts": r.get("last_ts",""),
        })

    return jsonify(_json_safe({"ok": True, "rows": out, "meta": meta}))

@app.route("/api/pnl")
@require_basic_auth
def api_pnl():
    trades, meta = load_trades_csv()
    fx = get_fx_usdc_eur()

    # windows: session/week/month/year (based on ts_utc if ISO-like)
    def parse_ts(s: str) -> Optional[datetime]:
        try:
            if not s:
                return None
            # accept Z
            s2 = s.replace("Z", "+00:00")
            return datetime.fromisoformat(s2).astimezone(timezone.utc)
        except Exception:
            return None

    now = datetime.now(timezone.utc)
    windows = {
        "session": now - timedelta(days=3650),  # best effort: all
        "week": now - timedelta(days=7),
        "month": now - timedelta(days=31),
        "year": now - timedelta(days=366),
    }

    pnl_vals = []
    by_win = {k: {"pnl_usdc": 0.0, "trades": 0, "wins": 0, "losses": 0, "profit": 0.0, "loss": 0.0} for k in windows}

    for t in trades:
        pnl = _parse_float(t.get("pnl"))
        if pnl is None:
            continue
        ts = parse_ts(str(t.get("ts_utc") or ""))
        for k, start in windows.items():
            if ts is None or ts >= start:
                a = by_win[k]
                a["pnl_usdc"] += pnl
                a["trades"] += 1
                if pnl > 0:
                    a["wins"] += 1
                    a["profit"] += pnl
                elif pnl < 0:
                    a["losses"] += 1
                    a["loss"] += abs(pnl)

    def mk(a):
        trades_n = a["trades"]
        winrate = (a["wins"] / trades_n * 100.0) if trades_n else None
        pf = (a["profit"] / a["loss"]) if a["loss"] > 0 else (None if a["profit"] == 0 else float("inf"))
        pnl_usdc = a["pnl_usdc"]
        return {
            "pnl_usdc": round(pnl_usdc, 6),
            "pnl_eur": round(usdc_to_eur(pnl_usdc, fx), 6) if fx is not None else None,
            "trades": trades_n,
            "winrate": round(winrate, 1) if winrate is not None else None,
            "profit_factor": (round(pf, 3) if pf not in (None, float("inf")) else pf),
        }

    return jsonify(_json_safe({
        "ok": True,
        "fx_usdc_eur": fx,
        "session": mk(by_win["session"]),
        "week": mk(by_win["week"]),
        "month": mk(by_win["month"]),
        "year": mk(by_win["year"]),
        "meta": meta,
    }))

@app.route("/api/summary")
@require_basic_auth
def api_summary():
    token = {}
    try:
        symbol, profile, dry_run = detect_symbol_profile()
        token = {"symbol": symbol, "profile": profile, "dry_run": dry_run}
    except Exception:
        token = {"symbol": "", "profile": "", "dry_run": ""}

    wallet = api_wallet().get_json(silent=True) or {}
    trades = api_trades().get_json(silent=True) or {}
    pnl = api_pnl().get_json(silent=True) or {}
    position = read_runtime_json("position.json")

    return jsonify(_json_safe({
        "ok": True,
        "ts_utc": utc_now_str(),
        "token": token,
        "wallet": wallet,
        "trades": trades,
        "pnl": pnl,
        "position": position,
    }))

@app.route("/api/log_list")
@require_basic_auth
def api_log_list():
    limit = int(request.args.get("n", "40") or "40")
    return jsonify({"ok": True, "files": list_recent_logs(limit=limit)})

@app.route("/api/log_tail")
@require_basic_auth
def api_log_tail():
    name = (request.args.get("name") or "").strip()
    n = int(request.args.get("n", "200") or "200")
    if not name:
        return jsonify({"ok": False, "error": "missing name", "lines": []})
    p = (LOG_DIR / name)
    if not p.exists():
        return jsonify({"ok": False, "error": "not found", "lines": []})
    lines = tail_file(p, n=n)
    return jsonify({"ok": True, "name": p.name, "lines": lines})

# -----------------------------
# Main
# -----------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=DASH_PORT, debug=False)
