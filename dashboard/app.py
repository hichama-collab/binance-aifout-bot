from datetime import timedelta
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

from flask import Flask, Response, jsonify, render_template, request, abort

def _json_safe(obj):
    """Convert Path and other non-JSON types into JSON-safe primitives."""
    try:
        from pathlib import Path as _Path
        if isinstance(obj, _Path):
            return str(obj)
    except Exception:
        pass
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj

APP_TITLE = "botdash"

# ---- config (env) ----
BASE_DIR = Path(os.getenv("BOT_BASE_DIR", "/opt/binance-aifout-bot")).resolve()
LOG_DIR = Path(os.getenv("BOT_LOG_DIR", str(BASE_DIR / "data" / "logs"))).resolve()
RUNTIME_DIR = Path(os.getenv("BOT_RUNTIME_DIR", str(BASE_DIR / "data" / "runtime"))).resolve()
SERVICE_ENV = Path(os.getenv("BOT_SERVICE_ENV", str(BASE_DIR / ".service.env"))).resolve()

DASH_USER = os.getenv("DASH_USER", "")
DASH_PASS = os.getenv("DASH_PASS", "")

# FX rate: USDC -> EUR (set via env or BOT_RUNTIME_DIR/fx.json)
FX_USDC_EUR_ENV = os.getenv("FX_USDC_EUR", "").strip()


# systemd units allowlist (security)
UNITS = [
    "binance-aifout-bot.service",
    "token-profile-selector.service",
    "token-profile-selector.timer",
]

MAX_TAIL_LINES = 600

app = Flask(__name__)

# ---- auth ----
def _unauthorized():
    return Response("unauthorized", 401, {"WWW-Authenticate": f'Basic realm="{APP_TITLE}"'})

def require_basic_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # If creds not set, block (avoid "open dashboard" by mistake)
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
    # no shell
    p = subprocess.run(args, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out.strip()

def systemctl(action, unit):
    if unit not in UNITS:
        return False, f"unit not allowed: {unit}"
    if action not in ("start", "stop", "restart", "status"):
        return False, f"action not allowed: {action}"
    return run_cmd(["systemctl", action, unit])

def tail_file(path: Path, n_lines: int = 200):
    if n_lines < 1:
        n_lines = 1
    n_lines = min(n_lines, MAX_TAIL_LINES)
    if not path.exists():
        return []
    # simple tail (small files)
    try:
        with path.open("r", errors="ignore") as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-n_lines:]]
    except Exception:
        return []

def find_latest(directory_or_pattern, pattern: str | None = None):
    """Return latest file by mtime.
    Compatible with older calls:
      - find_latest("*_trades.csv")  # uses LOG_DIR
      - find_latest(LOG_DIR, "*_trades.csv")
    """
    try:
        if pattern is None:
            directory = LOG_DIR
            pat = str(directory_or_pattern)
        else:
            directory = Path(directory_or_pattern)
            pat = str(pattern)
        files = sorted(directory.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None
    except Exception:
        return None

def safe_read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def parse_last_indicators(lines):
    """
    Extracts useful markers from log line like:
    CHK EMA1m:NO EMA5m:NO RSI:35.42 VOL:OK ... STATE:IDLE
    """
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
    # common tokens
    for key in ("EMA1m", "EMA5m", "RSI", "VOL", "MOM", "RANGE", "UP", "SPREAD", "BID", "ASK", "STATE"):
        m = re.search(rf"{key}:([^\s]+)", last)
        if m:
            data[key] = m.group(1)
    return data

def load_trades_csv(csv_path: Path, max_rows: int = 1500):
    """
    returns list[dict] for last rows
    expected columns (best effort): ts_utc,symbol,event,side,qty,price,reason,pnl,profile,dry_run
    """
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
        return rows, cols
    except Exception:
        return [], []

def fnum(x):
    try:
        if x is None:
            return None
        s = str(x).strip()
        if s == "":
            return None
        return float(s)
    except Exception:
        return None

def compute_stats(trades):
    # pnl column may be missing
    pnl_vals = []
    for r in trades:
        v = fnum(r.get("pnl"))
        if v is not None:
            pnl_vals.append(v)

    total_realized = sum(pnl_vals) if pnl_vals else 0.0
    wins = [v for v in pnl_vals if v > 0]
    losses = [v for v in pnl_vals if v < 0]
    winrate = (len(wins) / len(pnl_vals) * 100.0) if pnl_vals else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else (sum(wins) if wins else 0.0)

    # cumulative pnl series
    cum = []
    s = 0.0
    for v in pnl_vals:
        s += v
        cum.append(s)

    # histogram buckets (simple)
    hist = {}
    for v in pnl_vals[-400:]:
        b = round(v, 2)
        hist[str(b)] = hist.get(str(b), 0) + 1

    return {
        "total_realized": round(total_realized, 6),
        "winrate": round(winrate, 2),
        "profit_factor": round(profit_factor, 3),
        "trades": len(pnl_vals),
        "cum_pnl": cum[-400:],
        "pnl_samples": pnl_vals[-400:],
    }

def read_service_env():
    if not SERVICE_ENV.exists():
        return {}
    out = {}
    for line in SERVICE_ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out

def write_service_env(updates: dict):
    env = read_service_env()
    env.update(updates)
    # preserve order, keep comments minimal
    lines = []
    for k in sorted(env.keys()):
        lines.append(f"{k}={env[k]}")
    SERVICE_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")

def detect_symbol_profile():
    env = read_service_env()
    symbol = env.get("SYMBOL") or env.get("TOKEN") or env.get("SYMBOLUSDC") or ""
    profile = env.get("PROFILE") or ""
    dry_run = env.get("DRY_RUN") or ""
    return symbol, profile, dry_run


def _fetch_fx_usdc_eur_binance():
    # Use public endpoint: EURUSDC price = USDC per 1 EUR. So USDC->EUR = 1/price.
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

def get_fx_usdc_eur():
    # Priority:
    # 1) env FX_USDC_EUR
    # 2) BOT_RUNTIME_DIR/fx.json {"usdc_eur": 0.92}
    # 3) auto fetch from Binance (cached) and write fx.json
    try:
        if FX_USDC_EUR_ENV:
            return float(FX_USDC_EUR_ENV)
    except Exception:
        pass

    fxj = safe_read_json(RUNTIME_DIR / "fx.json") or {}
    try:
        v = fxj.get("usdc_eur")
        if v is None:
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

    # Best effort persist for other components / visibility
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

def summarize_pnl_by_symbol(trades):
    # returns list of {symbol, pnl_usdc, pnl_eur, trades, last_ts}
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
    # sort: most recent first (fallback by abs pnl)
    def keyfn(x):
        return (x.get("last_ts") or "", abs(x.get("pnl_usdc") or 0.0))
    rows.sort(key=keyfn, reverse=True)
    return rows, fx

def load_wallet_snapshot():
    # Best effort. Accept various filenames.
    for name in ("wallet.json", "balances.json", "spot_wallet.json", "account.json"):
        p = RUNTIME_DIR / name
        if p.exists():
            j = safe_read_json(p)
            if j is not None:
                return {"file": str(p), "data": j}
    return {"file": "", "data": None}

def normalize_wallet(wallet_data):
    # Output list[{asset, free, locked, total, value_usdc, value_eur}]
    fx = get_fx_usdc_eur()
    rows = []
    if not wallet_data:
        return rows, fx
    data = wallet_data
    # Common shapes:
    # - {"balances":[{"asset":"BTC","free":"0.1","locked":"0"}]}
    # - [{"asset":"USDC","free":...}]
    balances = None
    if isinstance(data, dict):
        balances = data.get("balances") or data.get("spot") or data.get("assets") or data.get("data")
    if balances is None and isinstance(data, list):
        balances = data
    if balances is None:
        return rows, fx
    if isinstance(balances, dict):
        # maybe {"USDC":{"free":..}}
        for k,v in balances.items():
            if isinstance(v, dict):
                free = fnum(v.get("free") or v.get("available"))
                locked = fnum(v.get("locked") or v.get("freeze"))
                total = (free or 0.0) + (locked or 0.0)
                rows.append({"asset": str(k).upper(), "free": free, "locked": locked, "total": total, "value_usdc": None, "value_eur": None})
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
        # try value fields
        v_usdc = fnum(b.get("value_usdc") or b.get("usdc_value") or b.get("quote_usdc") or b.get("equity_usdc"))
        v_eur = fnum(b.get("value_eur") or b.get("eur_value") or b.get("equity_eur"))
        # compute for USDC itself
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
    # sort: value_usdc desc else asset
    rows.sort(key=lambda r: (r.get("value_usdc") is not None, r.get("value_usdc") or 0.0), reverse=True)
    return rows, fx

# ---- routes ----
@app.route("/")
@require_basic_auth
def index():
    return render_template(
        "dashboard.html",
        host=os.uname().nodename,
        utc=utc_now_str(),
        base=str(BASE_DIR),
        logs=str(LOG_DIR),
    )

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
    symbol, profile, dry_run = detect_symbol_profile()

    # system metrics (best effort)
    up_ok, up_out = run_cmd(["uptime", "-p"])
    df_ok, df_out = run_cmd(["df", "-h", "/"])
    mem_ok, mem_out = run_cmd(["free", "-h"])

    units = {}
    for u in UNITS:
        ok, out = systemctl("status", u)
        # compact status parsing
        state = "unknown"
        sub = ""
        for line in out.splitlines():
            if "Active:" in line:
                # Active: active (running) since ...
                m = re.search(r"Active:\s+(\w+)\s+\((\w+)\)", line)
                if m:
                    state = m.group(1)
                    sub = m.group(2)
                else:
                    m2 = re.search(r"Active:\s+(\w+)", line)
                    if m2:
                        state = m2.group(1)
                break
        units[u] = {"ok": ok, "state": state, "sub": sub}

    units_list = []
    for name, info in units.items():
        units_list.append({
            "unit": name,
            "state": info.get("state","unknown"),
            "since": "",
            "details": info.get("sub",""),
        })

    # latest log indicators
    log_file = find_latest("*_trades.log") or find_latest("*.log")
    indicators = parse_last_indicators(tail_file(log_file, 200)) if log_file else {}

    # position (optional)
    pos = safe_read_json(RUNTIME_DIR / "position.json") or safe_read_json(RUNTIME_DIR / "position_live.json") or {}

    return jsonify({
        "ok": True,
        "ts_utc": utc_now_str(),
        "token": {"symbol": symbol, "profile": profile, "dry_run": dry_run},
        "units": units_list,
        "indicators": indicators,
        "position": pos,
    })

@app.route("/api/services")
@require_basic_auth
def api_services():
    # Same payload shape as dashboard expects
    st = api_status().get_json()
    return jsonify({"ok": True, "units": st.get("units", [])})

@app.route("/api/token_now")
@require_basic_auth
def api_token_now():
    symbol, profile, dry_run = detect_symbol_profile()
    return jsonify({"ok": True, "token": {"symbol": symbol, "profile": profile, "dry_run": dry_run}})


@app.route("/api/stats")
@require_basic_auth
def api_stats():
    # latest trades csv
    csv_path = find_latest("*_trades.csv")
    trades, cols = load_trades_csv(csv_path) if csv_path else ([], [])
    stats = compute_stats(trades)

    # session: from last BOOT marker if present
    # best effort: use all current file as "session"
    return jsonify({
        "ok": True,
        "file": str(csv_path) if csv_path else "",
        "columns": cols,
        "kpi": stats,
        "last_trades": trades[-30:],
    })

@app.route("/api/logs")
@require_basic_auth
def api_logs():
    """
    UI expects: {"ok": true, "files":[{"name":..., "size":..., "mtime":...}, ...]}
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    files = []
    try:
        for p in sorted(LOG_DIR.glob("*"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
            if not p.is_file():
                continue
            st = p.stat()
            files.append({
                "name": p.name,
                "size": int(st.st_size),
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            })
            if len(files) >= 50:
                break
    except Exception:
        files = []

    return jsonify(ok=True, files=files, logs=str(LOG_DIR), base=str(BASE_DIR))

@app.route("/api/log_tail")
@require_basic_auth
def api_log_tail():
    # UI may send ?name=... or ?file=... and optional ?n=...
    name = (request.args.get("name") or request.args.get("file") or "").strip()
    try:
        n = int(request.args.get("n") or 200)
    except Exception:
        n = 200
    if n < 10:
        n = 10
    if n > 2000:
        n = 2000
    if not name:
        return jsonify(ok=False, error="missing file"), 400

    path = (LOG_DIR / name).resolve()
    # prevent path traversal
    if LOG_DIR.resolve() not in path.parents:
        return jsonify(ok=False, error="invalid file"), 400

    lines = tail_file(path, n_lines=n)
    return jsonify(ok=True, text="\n".join(lines), file=name)

@app.route("/api/control", methods=["POST"])
@require_basic_auth
def api_control():
    """
    UI sends: {"unit": "...", "action": "start|stop|restart"}
    """
    body = request.get_json(silent=True) or {}
    unit = str(body.get("unit", "")).strip()
    action = str(body.get("action", "")).strip().lower()
    if not unit or action not in ("start", "stop", "restart"):
        return jsonify(ok=False, error="bad request"), 400
    res = systemctl(action, unit)
    return jsonify(ok=res.get('ok', False), unit=unit, action=action, active=res.get('active'), output=res.get('output',''), error=res.get('error'))

@app.route("/api/trades")
@require_basic_auth
def api_trades():
    # Aggregates latest trades per token from *_trades.csv (and optionally *_trades.log if present).
    limit = int(request.args.get("limit", 10))
    trades, meta = load_trades_csv()
    # trades can be a list of dicts; ignore non-dict rows
    items = [t for t in (trades or []) if isinstance(t, dict)]
    by_symbol = {}
    for t in items:
        sym = str(t.get("symbol") or "").strip()
        if not sym:
            continue
        side = str(t.get("side") or "").upper().strip()
        evt = str(t.get("event") or "").upper().strip()
        qty = t.get("qty")
        price = t.get("price")
        # keep only real executions (BUY/SELL) OR events *_FILLED
        is_exec = side in ("BUY", "SELL") or evt.endswith("_FILLED") or evt in ("BUY_FILLED", "SELL_FILLED")
        if not is_exec:
            continue
        d = by_symbol.setdefault(sym, {"symbol": sym, "trades": 0, "net_usdc": 0.0, "first_ts": None, "last_ts": None})
        d["trades"] += 1
        # ts can be int epoch or iso
        ts = t.get("ts") or t.get("ts_utc") or t.get("time") or t.get("timestamp")
        try:
            ts_val = int(float(ts))
        except Exception:
            ts_val = None
        if ts_val is not None:
            d["first_ts"] = ts_val if d["first_ts"] is None else min(d["first_ts"], ts_val)
            d["last_ts"] = ts_val if d["last_ts"] is None else max(d["last_ts"], ts_val)
        # pnl
        pnl = t.get("pnl")
        try:
            pnl_f = float(pnl)
        except Exception:
            pnl_f = 0.0
        d["net_usdc"] += pnl_f

    # sort by last_ts desc
    rows = list(by_symbol.values())
    rows.sort(key=lambda r: (r["last_ts"] or 0), reverse=True)
    rows = rows[:limit]

    fx = fx_usdc_eur()
    for r in rows:
        r["net_eur"] = round((r["net_usdc"] or 0.0) * fx, 4) if fx else 0.0
        # human period label
        r["period"] = ""
        if r["first_ts"] and r["last_ts"]:
            r["period"] = f'{r["first_ts"]}..{r["last_ts"]}'
    return jsonify(_json_safe({"ok": True, "rows": rows, "meta": meta, "fx_usdc_eur": fx}))

@app.route("/api/pnl")
@require_basic_auth
def api_pnl():
    trades, meta = load_trades_csv()
    items = [t for t in (trades or []) if isinstance(t, dict)]
    # keep only executed trades
    execs = []
    for t in items:
        side = str(t.get("side") or "").upper().strip()
        evt = str(t.get("event") or "").upper().strip()
        is_exec = side in ("BUY", "SELL") or evt.endswith("_FILLED") or evt in ("BUY_FILLED", "SELL_FILLED")
        if not is_exec:
            continue
        ts = t.get("ts") or t.get("ts_utc") or t.get("time") or t.get("timestamp")
        try:
            ts_val = int(float(ts))
        except Exception:
            continue
        pnl = t.get("pnl")
        try:
            pnl_f = float(pnl)
        except Exception:
            pnl_f = 0.0
        execs.append((ts_val, pnl_f))

    execs.sort(key=lambda x: x[0])
    if not execs:
        return jsonify({"ok": True, "rows": [], "fx_usdc_eur": fx_usdc_eur()})

    import datetime as _dt
    now = _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc)
    def _sum_since(delta):
        cutoff = int((now - delta).timestamp())
        return sum(p for ts,p in execs if ts >= cutoff)

    session = sum(p for _,p in execs)
    week = _sum_since(_dt.timedelta(days=7))
    month = _sum_since(_dt.timedelta(days=30))
    year = _sum_since(_dt.timedelta(days=365))
    fx = fx_usdc_eur()
    def _eur(x): return round(x*fx,4) if fx else 0.0
    return jsonify(_json_safe({
        "ok": True,
        "session": {"usdc": round(session,4), "eur": _eur(session)},
        "week": {"usdc": round(week,4), "eur": _eur(week)},
        "month": {"usdc": round(month,4), "eur": _eur(month)},
        "year": {"usdc": round(year,4), "eur": _eur(year)},
        "fx_usdc_eur": fx,
        "trades": len(execs),
    }))

@app.route("/api/action", methods=["POST"])
@require_basic_auth
def api_action():
    payload = request.get_json(force=True, silent=True) or {}
    action = str(payload.get("action", "")).strip()
    unit = str(payload.get("unit", "")).strip()

    ok, out = systemctl(action, unit)
    # trader-friendly message
    msg = "OK" if ok else "ECHEC"
    return jsonify({
        "ok": ok,
        "unit": unit,
        "action": action,
        "message": f"{msg} - {unit} - {action}",
        "output": out[:4000],
    })

@app.route("/api/config", methods=["POST"])
@require_basic_auth
def api_config():
    """
    Update .service.env (DRY_RUN, PROFILE, SYMBOL) and optionally restart bot.
    """
    payload = request.get_json(force=True, silent=True) or {}
    updates = {}
    if "DRY_RUN" in payload:
        v = str(payload["DRY_RUN"]).strip()
        if v not in ("0", "1"):
            return jsonify({"ok": False, "error": "DRY_RUN must be 0 or 1"})
        updates["DRY_RUN"] = v
    if "PROFILE" in payload:
        v = str(payload["PROFILE"]).strip()
        if not re.match(r"^[A-Za-z0-9_-]{1,24}$", v):
            return jsonify({"ok": False, "error": "invalid PROFILE"})
        updates["PROFILE"] = v
    if "SYMBOL" in payload:
        v = str(payload["SYMBOL"]).strip().upper()
        if not re.match(r"^[A-Z0-9]{3,20}$", v):
            return jsonify({"ok": False, "error": "invalid SYMBOL"})
        updates["SYMBOL"] = v

    if updates:
        try:
            write_service_env(updates)
        except Exception as e:
            return jsonify({"ok": False, "error": f"cannot write env: {e}"})

    restart = bool(payload.get("restart_bot", False))
    r_ok, r_out = (True, "")
    if restart:
        r_ok, r_out = systemctl("restart", "binance-aifout-bot.service")

    symbol, profile, dry_run = detect_symbol_profile()
    return jsonify({
        "ok": True,
        "applied": updates,
        "symbol": symbol,
        "profile": profile,
        "dry_run": dry_run,
        "restart": {"ok": r_ok, "output": r_out[:2000]},
    })


@app.route("/api/summary")
@require_basic_auth
def api_summary():
    st = api_status().json if hasattr(api_status(), "json") else {}
    wallet = api_wallet().json if hasattr(api_wallet(), "json") else {}
    pos = api_position().json if hasattr(api_position(), "json") else {}
    tr = None
    try:
        tr = api_trades()
        trj = tr.json if hasattr(tr, "json") else {}
    except Exception:
        trj = {}
    return jsonify(_json_safe({
        "ok": True,
        "status": (st or {}).get("status", {}),
        "wallet": (wallet or {}),
        "position": (pos or {}),
        "trades": (trj or {}).get("rows", []),
        "fx_usdc_eur": fx_usdc_eur(),
    }))

@app.route("/api/wallet")
@require_basic_auth
def api_wallet():
    data = read_runtime_json("wallet.json") or {}
    rows = []
    fx = fx_usdc_eur()
    min_usdc = float(request.args.get("min_usdc", 1.0))
    for r in (data.get("rows") or data.get("assets") or []):
        if not isinstance(r, dict):
            continue
        asset = str(r.get("asset") or r.get("symbol") or "").strip()
        free = float(r.get("free") or 0.0)
        locked = float(r.get("locked") or 0.0)
        total = float(r.get("total") or (free+locked))
        usdc_val = r.get("usdc") or r.get("value_usdc") or r.get("approx_usdc")
        eur_val = r.get("eur") or r.get("value_eur") or r.get("approx_eur")
        try:
            usdc_val = float(usdc_val) if usdc_val is not None else None
        except Exception:
            usdc_val = None
        try:
            eur_val = float(eur_val) if eur_val is not None else None
        except Exception:
            eur_val = None
        # If valuation missing, keep None; UI will show --
        if usdc_val is not None and usdc_val < min_usdc:
            continue
        rows.append({
            "asset": asset,
            "free": free,
            "locked": locked,
            "total": total,
            "usdc": usdc_val,
            "eur": eur_val if eur_val is not None else (round(usdc_val*fx,4) if (usdc_val is not None and fx) else None),
        })

    # sort by usdc desc
    rows.sort(key=lambda x: (x["usdc"] if x["usdc"] is not None else -1.0), reverse=True)
    tot_usdc = sum((x["usdc"] or 0.0) for x in rows if x.get("usdc") is not None)
    tot_eur = round(tot_usdc*fx,4) if fx else 0.0
    return jsonify(_json_safe({"ok": True, "rows": rows, "fx_usdc_eur": fx, "total_usdc": round(tot_usdc,4), "total_eur": tot_eur, "file": str(runtime_path("wallet.json"))}))

if __name__ == "__main__":
    # local bind only; nginx will proxy
    port = int(os.getenv("DASH_PORT", "8099"))
    app.run(host="127.0.0.1", port=port)
