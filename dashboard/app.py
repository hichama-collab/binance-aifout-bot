#!/usr/bin/env python3
import os
import re
import csv
import json
import time
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

APP_TITLE = "botdash"

# ---- config (env) ----
BASE_DIR = Path(os.getenv("BOT_BASE_DIR", "/opt/binance-aifout-bot")).resolve()
LOG_DIR = Path(os.getenv("BOT_LOG_DIR", str(BASE_DIR / "data" / "logs"))).resolve()
RUNTIME_DIR = Path(os.getenv("BOT_RUNTIME_DIR", str(BASE_DIR / "data" / "runtime"))).resolve()
SERVICE_ENV = Path(os.getenv("BOT_SERVICE_ENV", str(BASE_DIR / ".service.env"))).resolve()

DASH_USER = os.getenv("DASH_USER", "")
DASH_PASS = os.getenv("DASH_PASS", "")

FX_USDC_EUR_ENV = os.getenv("FX_USDC_EUR", "").strip()
FX_CACHE_TTL_SEC = int(os.getenv("FX_CACHE_TTL_SEC", "600"))

# systemd units allowlist (security)
UNITS = [
    "binance-aifout-bot.service",
    "token-profile-selector.service",
    "token-profile-selector.timer",
]

MAX_TAIL_LINES = 600
MAX_LOG_FILES = 200

app = Flask(__name__)

# ---- auth ----
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

# ---- helpers ----
def utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

def run_cmd(args):
    p = subprocess.run(args, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out.strip()

def systemctl(action, unit):
    if unit not in UNITS:
        return False, f"unit not allowed: {unit}"
    if action not in ("start", "stop", "restart", "status"):
        return False, f"action not allowed: {action}"
    return run_cmd(["systemctl", action, unit])

def safe_read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def tail_file(path: Path, n_lines: int = 200):
    if n_lines < 1:
        n_lines = 1
    n_lines = min(n_lines, MAX_TAIL_LINES)
    if not path or not path.exists():
        return []
    try:
        with path.open("r", errors="ignore") as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-n_lines:]]
    except Exception:
        return []

def find_latest(directory: Path, pattern: str = "*"):
    try:
        files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None
    except Exception:
        return None


def list_latest(directory: Path, pattern: str, limit: int = 10):
    try:
        files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[:limit]
    except Exception:
        return []

def _cache_dir():
    d = (Path(__file__).resolve().parent / 'cache')
    d.mkdir(parents=True, exist_ok=True)
    return d

def cache_load(name: str):
    p = _cache_dir() / name
    try:
        with p.open('r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

def cache_save(name: str, payload: dict):
    p = _cache_dir() / name
    try:
        tmp = p.with_suffix(p.suffix + '.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        tmp.replace(p)
        return True
    except Exception:
        return False

def fnum(v):
    try:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None

def detect_symbol_profile():
    # From SERVICE_ENV (SYMBOL=XXX, PROFILE=..., DRY_RUN=...)
    symbol = None
    profile = None
    dry_run = None

    try:
        if SERVICE_ENV.exists():
            txt = SERVICE_ENV.read_text(encoding="utf-8", errors="ignore")
            for line in txt.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip().upper()
                v = v.strip()
                if k == "SYMBOL":
                    symbol = v.upper()
                elif k == "PROFILE":
                    profile = v
                elif k == "DRY_RUN":
                    try:
                        dry_run = int(v)
                    except Exception:
                        dry_run = v
    except Exception:
        pass

    return symbol or "--", profile or "--", dry_run if dry_run is not None else "--"

_FX_CACHE = {"ts": 0.0, "usdc_eur": None}

def _fetch_fx_usdc_eur_binance():
    # Best effort: uses public endpoint on binance.com, may fail.
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbol=EURUSDC"
        with urllib.request.urlopen(url, timeout=5) as r:
            j = json.loads(r.read().decode("utf-8", errors="ignore"))
        p = fnum(j.get("price"))
        if p and p > 0:
            return 1.0 / p  # EURUSDC => USDC/EUR
    except Exception:
        pass
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbol=USDCEUR"
        with urllib.request.urlopen(url, timeout=5) as r:
            j = json.loads(r.read().decode("utf-8", errors="ignore"))
        p = fnum(j.get("price"))
        if p and p > 0:
            return p
    except Exception:
        return None
    return None

def get_fx_usdc_eur():
    try:
        if FX_USDC_EUR_ENV:
            return float(FX_USDC_EUR_ENV)
    except Exception:
        pass

    fxj = safe_read_json(RUNTIME_DIR / "fx.json") or {}
    try:
        v = fxj.get("usdc_eur") if isinstance(fxj, dict) else None
        if v is None and isinstance(fxj, dict):
            v = fxj.get("FX_USDC_EUR")
        if v is not None:
            return float(v)
    except Exception:
        pass

    now = time.time()
    if (now - float(_FX_CACHE.get("ts", 0.0))) < FX_CACHE_TTL_SEC:
        return _FX_CACHE.get("usdc_eur")

    fx = _fetch_fx_usdc_eur_binance()
    _FX_CACHE["ts"] = now
    _FX_CACHE["usdc_eur"] = fx

    if fx is not None:
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            tmp = RUNTIME_DIR / "fx.json.tmp"
            tmp.write_text(json.dumps({"usdc_eur": fx, "ts": utc_now_str()}, indent=2), encoding="utf-8")
            tmp.replace(RUNTIME_DIR / "fx.json")
        except Exception:
            pass
    return fx

def usdc_to_eur(usdc, fx):
    try:
        if usdc is None or fx is None:
            return None
        return float(usdc) * float(fx)
    except Exception:
        return None

def parse_last_indicators(lines):
    if not lines:
        return {}
    last = ""
    for l in reversed(lines):
        if "CHK " in l or "STATE:" in l or "RSI:" in l:
            last = l
            break
    if not last:
        last = lines[-1]
    data = {}
    for key in ("EMA1m", "EMA5m", "RSI", "VOL", "MOM", "RANGE", "UP", "SPREAD", "BID", "ASK", "STATE"):
        m = re.search(rf"{key}:([^\s]+)", last)
        if m:
            data[key] = m.group(1)
    return data

def load_trades_csv(csv_path: Path, max_rows: int = 2000):
    if not csv_path or not csv_path.exists():
        return [], []
    rows = []
    cols = []
    try:
        with csv_path.open("r", newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames or []
            for r in reader:
                rows.append(r)
        if len(rows) > max_rows:
            rows = rows[-max_rows:]
        # normalize numeric fields
        for r in rows:
            for k in ("qty", "price", "pnl"):
                if k in r:
                    r[k] = fnum(r.get(k))
        return rows, cols
    except Exception:
        return [], []

def summarize_pnl_by_symbol(trades):
    fx = get_fx_usdc_eur()
    agg = {}
    for r in trades:
        sym = (r.get("symbol") or "").strip().upper()
        if not sym:
            continue
        pnl = fnum(r.get("pnl"))
        if pnl is None:
            continue
        ts = (r.get("ts_utc") or r.get("ts") or "").strip()
        a = agg.get(sym) or {"symbol": sym, "pnl_usdc": 0.0, "trades": 0, "last_ts": ts}
        a["pnl_usdc"] += pnl
        a["trades"] += 1
        if ts:
            a["last_ts"] = ts
        agg[sym] = a
    rows = []
    for sym, a in agg.items():
        pnl_usdc = a["pnl_usdc"]
        rows.append({
            "symbol": sym,
            "pnl_usdc": round(pnl_usdc, 6),
            "pnl_eur": round(usdc_to_eur(pnl_usdc, fx), 6) if fx is not None else None,
            "trades": a["trades"],
            "last_ts": a["last_ts"],
        })
    rows.sort(key=lambda x: (x.get("last_ts") or "", abs(x.get("pnl_usdc") or 0.0)), reverse=True)
    return rows, fx

def load_wallet_snapshot():
    # Search multiple locations because bot path may differ by version.
    candidates = []
    for d in (
        RUNTIME_DIR,
        BASE_DIR / "state",
        BASE_DIR / "data" / "runtime",
        BASE_DIR / "data",
        BASE_DIR,
    ):
        candidates.append(d)

    names = ("wallet.json", "balances.json", "spot_wallet.json", "account.json", "wallet_spot.json")
    for d in candidates:
        for name in names:
            p = d / name
            if p.exists():
                j = safe_read_json(p)
                if j is not None:
                    return {"file": str(p), "data": j}
    return {"file": "", "data": None}

def normalize_wallet(wallet_data):
    fx = get_fx_usdc_eur()
    rows = []
    if not wallet_data:
        return rows, fx

    data = wallet_data
    balances = None
    if isinstance(data, dict):
        balances = data.get("balances") or data.get("spot") or data.get("assets") or data.get("data")
    if balances is None and isinstance(data, list):
        balances = data

    if balances is None:
        # sometimes dict of assets
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    free = fnum(v.get("free") or v.get("available"))
                    locked = fnum(v.get("locked") or v.get("freeze"))
                    total = (free or 0.0) + (locked or 0.0)
                    rows.append({"asset": str(k).upper(), "free": free, "locked": locked, "total": total,
                                 "value_usdc": total if str(k).upper() == "USDC" else None,
                                 "value_eur": usdc_to_eur(total, fx) if (fx is not None and str(k).upper()=="USDC") else None})
        return rows, fx

    if isinstance(balances, dict):
        for k, v in balances.items():
            if isinstance(v, dict):
                free = fnum(v.get("free") or v.get("available"))
                locked = fnum(v.get("locked") or v.get("freeze"))
                total = (free or 0.0) + (locked or 0.0)
                v_usdc = fnum(v.get("value_usdc") or v.get("usdc_value"))
                if v_usdc is None and str(k).upper() == "USDC":
                    v_usdc = total
                v_eur = fnum(v.get("value_eur") or v.get("eur_value"))
                if v_eur is None and v_usdc is not None and fx is not None:
                    v_eur = usdc_to_eur(v_usdc, fx)
                rows.append({"asset": str(k).upper(), "free": free, "locked": locked, "total": total,
                             "value_usdc": v_usdc, "value_eur": v_eur})
        rows.sort(key=lambda r: (r.get("value_usdc") is not None, r.get("value_usdc") or 0.0), reverse=True)
        return rows, fx

    for b in balances:
        if not isinstance(b, dict):
            continue
        asset = (b.get("asset") or b.get("symbol") or b.get("coin") or "").strip().upper()
        if not asset:
            continue
        free = fnum(b.get("free") or b.get("available") or b.get("qty"))
        locked = fnum(b.get("locked") or b.get("freeze"))
        total = fnum(b.get("total"))
        if total is None:
            total = (free or 0.0) + (locked or 0.0)

        v_usdc = fnum(b.get("value_usdc") or b.get("usdc_value") or b.get("quote_usdc") or b.get("equity_usdc"))
        v_eur = fnum(b.get("value_eur") or b.get("eur_value") or b.get("equity_eur"))
        if v_usdc is None and asset == "USDC":
            v_usdc = total
        if v_eur is None and v_usdc is not None and fx is not None:
            v_eur = usdc_to_eur(v_usdc, fx)

        rows.append({
            "asset": asset,
            "free": free,
            "locked": locked,
            "total": total,
            "value_usdc": round(v_usdc, 6) if v_usdc is not None else None,
            "value_eur": round(v_eur, 6) if v_eur is not None else None,
        })

    rows.sort(key=lambda r: (r.get("value_usdc") is not None, r.get("value_usdc") or 0.0), reverse=True)
    return rows, fx

def load_position_snapshot():
    candidates = []
    for d in (
        RUNTIME_DIR,
        BASE_DIR / "state",
        BASE_DIR / "data" / "runtime",
        BASE_DIR / "data",
        BASE_DIR,
    ):
        candidates.append(d)
    names = ("position.json", "position_live.json", "position_state.json")
    for d in candidates:
        for name in names:
            p = d / name
            if p.exists():
                j = safe_read_json(p)
                if j is not None:
                    return j
    return {}

def list_logs():
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    files = []
    try:
        for p in sorted(LOG_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)[:MAX_LOG_FILES]:
            if not p.is_file():
                continue
            files.append({
                "name": p.name,
                "size": p.stat().st_size,
                "mtime": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            })
    except Exception:
        pass
    return files

def compute_pnl_kpis(trades):
    # Minimal KPI set based on pnl column (per row).
    pnl_vals = [fnum(r.get("pnl")) for r in trades]
    pnl_vals = [p for p in pnl_vals if p is not None]
    total = sum(pnl_vals) if pnl_vals else 0.0
    wins = [p for p in pnl_vals if p > 0]
    losses = [p for p in pnl_vals if p < 0]
    winrate = (len(wins) / len(pnl_vals) * 100.0) if pnl_vals else None
    pf = None
    if losses:
        pf = (sum(wins) / abs(sum(losses))) if sum(losses) != 0 else None
    return {
        "usdc": round(total, 6),
        "wins": len(wins),
        "losses": len(losses),
        "winrate": round(winrate, 3) if winrate is not None else None,
        "profit_factor": round(pf, 6) if pf is not None else None,
        "trades": len(pnl_vals),
    }

# ---- pages ----
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

# ---- api ----
@app.route("/api/status")
@require_basic_auth
def api_status():
    symbol, profile, dry_run = detect_symbol_profile()

    units_list = []
    for u in UNITS:
        ok, out = systemctl("status", u)
        state = "unknown"
        sub = ""
        since = ""
        for line in out.splitlines():
            if "Active:" in line:
                m = re.search(r"Active:\s+(\w+)\s+\((\w+)\)\s+since\s+([^;]+)", line)
                if m:
                    state = m.group(1)
                    sub = m.group(2)
                    since = m.group(3).strip()
                else:
                    m2 = re.search(r"Active:\s+(\w+)\s+\((\w+)\)", line)
                    if m2:
                        state = m2.group(1)
                        sub = m2.group(2)
                break
        units_list.append({"unit": u, "state": state, "since": since, "details": sub})

    log_file = find_latest(LOG_DIR, "*_trades.log") or find_latest(LOG_DIR, "*.log")
    indicators = parse_last_indicators(tail_file(log_file, 200)) if log_file else {}

    pos = load_position_snapshot()

    return jsonify({
        "ok": True,
        "ts_utc": utc_now_str(),
        "host": os.uname().nodename,
        "utc": utc_now_str(),
        "base": str(BASE_DIR),
        "logs": str(LOG_DIR),
        "fx_usdc_eur": get_fx_usdc_eur(),
        "token": {"symbol": symbol, "profile": profile, "dry_run": dry_run},
        "units": units_list,
        "indicators": indicators,
        "position": pos,
    })

@app.route("/api/wallet")
@require_basic_auth
def api_wallet():
    snap = load_wallet_snapshot()
    rows, fx = normalize_wallet(snap.get("data"))
    # Always return object expected by front
    return jsonify({"ok": True, "file": snap.get("file",""), "rows": rows, "fx_usdc_eur": fx})

@app.route("/api/summary")
@require_basic_auth
def api_summary():
    # Last 10 traded tokens based on per-token *_trades.csv files (newest mtime first).
    fx = get_fx_usdc_eur()
    files = list_latest(LOG_DIR, '*_trades.csv', limit=10)
    sig = [{'p': str(p), 'mtime': p.stat().st_mtime, 'size': p.stat().st_size} for p in files] if files else []
    cache_key = {'v': 1, 'sig': sig, 'fx': fx}
    cached = cache_load('summary_last_tokens.json')
    if cached and cached.get('key') == cache_key and isinstance(cached.get('rows'), list):
        return jsonify({'ok': True, 'rows': cached['rows'], 'fx_usdc_eur': fx, 'cached': True})

    rows = []
    for p in files:
        sym = p.name.replace('_trades.csv', '')
        trades, _ = load_trades_csv(p)
        k = compute_pnl_kpis(trades)
        net_usdc = k.get('usdc', 0.0)
        net_eur = usdc_to_eur(net_usdc, fx) if fx is not None else None
        last_ts = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        rows.append({
            'symbol': sym,
            'last_ts': last_ts,
            'pnl_usdc': net_usdc,
            'pnl_eur': round(net_eur, 6) if net_eur is not None else None,
            'trades': k.get('trades', 0),
        })

    payload = {'key': cache_key, 'rows': rows}
    cache_save('summary_last_tokens.json', payload)
    return jsonify({'ok': True, 'rows': rows, 'fx_usdc_eur': fx, 'cached': False})

@app.route("/api/trades")
@require_basic_auth
def api_trades():
    csv_path = find_latest(LOG_DIR, "*_trades.csv")
    trades, cols = load_trades_csv(csv_path) if csv_path else ([], [])
    # output newest first
    rows = list(reversed(trades[-200:])) if trades else []
    return jsonify({"ok": True, "file": str(csv_path) if csv_path else "", "columns": cols, "rows": rows})

@app.route("/api/logs")
@require_basic_auth
def api_logs():
    return jsonify({"ok": True, "files": list_logs()})

@app.route("/api/log_tail")
@require_basic_auth
def api_log_tail():
    name = (request.args.get("name") or "").strip()
    n = request.args.get("n") or "200"
    try:
        n = int(n)
    except Exception:
        n = 200
    # security: basename only
    if not name or "/" in name or ".." in name or "\\" in name:
        return jsonify({"ok": False, "error": "bad name"}), 400
    p = LOG_DIR / name
    lines = tail_file(p, n)
    return jsonify({"ok": True, "name": name, "lines": lines, "text": "\n".join(lines)})

@app.route("/api/pnl")
@require_basic_auth
def api_pnl():
    # Aggregate KPIs across all *_trades.csv (cached by mtimes).
    fx = get_fx_usdc_eur()
    files = sorted(LOG_DIR.glob('*_trades.csv'), key=lambda p: p.stat().st_mtime, reverse=True)
    sig = [{'p': str(p), 'mtime': p.stat().st_mtime, 'size': p.stat().st_size} for p in files]
    cache_key = {'v': 2, 'sig': sig, 'fx': fx}
    cached = cache_load('pnl_kpis.json')
    if cached and cached.get('key') == cache_key and isinstance(cached.get('kpis'), dict):
        k = cached['kpis']
    else:
        total = 0.0
        wins = 0
        losses = 0
        trades_n = 0
        sum_wins = 0.0
        sum_losses = 0.0
        for p in files:
            trades, _ = load_trades_csv(p)
            for r in trades:
                v = fnum(r.get('pnl'))
                if v is None:
                    continue
                trades_n += 1
                total += v
                if v > 0:
                    wins += 1
                    sum_wins += v
                elif v < 0:
                    losses += 1
                    sum_losses += v
        winrate = (wins / trades_n * 100.0) if trades_n else None
        pf = None
        if losses and sum_losses != 0:
            pf = (sum_wins / abs(sum_losses))
        k = {
            'usdc': round(total, 6),
            'wins': wins,
            'losses': losses,
            'winrate': round(winrate, 3) if winrate is not None else None,
            'profit_factor': round(pf, 6) if pf is not None else None,
            'trades': trades_n,
        }
        cache_save('pnl_kpis.json', {'key': cache_key, 'kpis': k})

    total_usdc = k.get('usdc', 0.0)
    total_eur = usdc_to_eur(total_usdc, fx) if fx is not None else None

    return jsonify({
        'ok': True,
        'file': str(files[0]) if files else '',
        'fx_usdc_eur': fx,
        'session': {'usdc': total_usdc, 'eur': total_eur or 0.0},
        'week': {'usdc': total_usdc, 'eur': total_eur or 0.0},
        'month': {'usdc': total_usdc, 'eur': total_eur or 0.0},
        'year': {'usdc': total_usdc, 'eur': total_eur or 0.0},
        'trades': k.get('trades', 0),
        'winrate': k.get('winrate'),
        'profit_factor': k.get('profit_factor'),
    })

@app.route("/api/control", methods=["POST"])
@require_basic_auth
def api_control():
    try:
        j = request.get_json(force=True) or {}
    except Exception:
        j = {}
    unit = (j.get("unit") or "").strip()
    action = (j.get("action") or "").strip()
    ok, out = systemctl(action, unit)
    return jsonify({"ok": ok, "unit": unit, "action": action, "output": out})

if __name__ == "__main__":
    host = os.getenv("DASH_HOST", "0.0.0.0")
    port = int(os.getenv("DASH_PORT", "8099"))
    app.run(host=host, port=port, debug=False)