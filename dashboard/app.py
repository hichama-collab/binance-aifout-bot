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

def _parse_trade_logs(bot_log_dir: Path) -> list[dict]:
    """Best-effort parser for *_trades.log (fallback when CSV doesn't contain fills)."""
    out: list[dict] = []
    if not bot_log_dir or not bot_log_dir.exists():
        return out

    # Common patterns seen in logs: ISO timestamp + messages containing BUY/SELL (+ optional FILLED)
    iso_re = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)(?:Z|\+00:00)?")
    side_re = re.compile(r"\b(BUY|SELL)\b", re.IGNORECASE)
    pnl_re = re.compile(r"\bpnl(?:_usdc)?\b\s*[:=]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
    qty_re = re.compile(r"\bqty\b\s*[:=]\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
    price_re = re.compile(r"\bprice\b\s*[:=]\s*(\d+(?:\.\d+)?)", re.IGNORECASE)

    for log_path in sorted(bot_log_dir.glob("*_trades.log")):
        symbol = log_path.name.split("_trades.log")[0]
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    # Keep only lines that look like executions/fills, but stay permissive
                    s_m = side_re.search(line)
                    if not s_m:
                        continue
                    side = s_m.group(1).upper()

                    # Prefer lines mentioning fill/executed, but don't require it
                    if ("fill" not in line.lower()) and ("execut" not in line.lower()) and ("filled" not in line.lower()):
                        continue

                    ts_m = iso_re.search(line)
                    ts_utc = ts_m.group(1).replace(" ", "T") + "Z" if ts_m else None

                    pnl_m = pnl_re.search(line)
                    qty_m = qty_re.search(line)
                    pr_m = price_re.search(line)

                    out.append({
                        "ts_utc": ts_utc,
                        "symbol": symbol,
                        "side": side,
                        "qty": float(qty_m.group(1)) if qty_m else None,
                        "price": float(pr_m.group(1)) if pr_m else None,
                        "pnl": float(pnl_m.group(1)) if pnl_m else None,
                        "src": str(log_path.name),
                    })
        except Exception:
            continue
    return out


def load_trades_csv() -> tuple[list[dict], dict]:
    """
    Load trades from *_trades.csv files under BOT_LOG_DIR.

    Supports two formats:
    - Format A (legacy): ts_utc,symbol,side,qty,price,pnl
    - Format B (current bot): utc,event,reason,side,price,bid,ask,rsi,ema9,ema21,vol,mom,range,up,spread,pnl_usdc
      (symbol inferred from filename when missing)
    Returns: (trades_list, meta_dict)
    """
    trades: list[dict] = []
    meta: dict = {"files": [], "count": 0}

    if not BOT_LOG_DIR or not BOT_LOG_DIR.exists():
        return trades, meta

    for csv_path in sorted(BOT_LOG_DIR.glob("*_trades.csv")):
        meta["files"].append(str(csv_path.name))
        symbol_from_file = csv_path.name.split("_trades.csv")[0]

        try:
            with csv_path.open("r", newline="", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # symbol may be missing in some CSV formats
                    sym = (row.get("symbol") or row.get("sym") or "").strip() or symbol_from_file

                    # timestamp can be ts_utc or utc
                    ts_utc = (row.get("ts_utc") or row.get("utc") or row.get("timestamp") or "").strip()

                    side = (row.get("side") or "").strip().upper()
                    if side not in ("BUY", "SELL"):
                        # ignore decision-only rows
                        continue

                    def _f(key: str):
                        v = (row.get(key) or "").strip()
                        try:
                            return float(v) if v != "" else None
                        except Exception:
                            return None

                    qty = _f("qty") or _f("quantity")
                    price = _f("price")
                    pnl = _f("pnl") or _f("pnl_usdc")

                    trades.append({
                        "ts_utc": ts_utc,
                        "symbol": sym,
                        "side": side,
                        "qty": qty,
                        "price": price,
                        "pnl": pnl,
                        "src": str(csv_path.name),
                    })
        except Exception:
            continue

    # Fallback: parse *_trades.log if CSV has no fills
    if not trades:
        trades = _parse_trade_logs(BOT_LOG_DIR)

    # Sort chronologically when possible (ISO strings sort well)
    trades.sort(key=lambda t: (t.get("ts_utc") or "", t.get("symbol") or ""))

    meta["count"] = len(trades)
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

    # Hide dust to keep the table readable (keep USDC line always)
    filtered = []
    for r in rows:
        if r.get("asset") == "USDC":
            filtered.append(r)
            continue
        v = r.get("value_usdc")
        if isinstance(v, (int, float)) and v >= 1.0:
            filtered.append(r)

    return jsonify(_json_safe({
        "ok": True,
        "file": file_path,
        "fx_usdc_eur": fx,
        "rows": filtered,
    }))

