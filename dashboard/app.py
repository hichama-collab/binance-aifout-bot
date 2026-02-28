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
    """
    Last 10 traded symbols with net result (buys/sells) from latest *_trades.csv
    """
    trades_path = find_latest(LOG_DIR, "*_trades.csv")
    if not trades_path:
        return jsonify(ok=True, tokens=[], source=None)
    trades = load_trades_csv(trades_path)
    # group by symbol, keep most recent 10 symbols by last ts
    by_symbol = {}
    for t in trades:
        # tolerate both dict rows and list/tuple rows (older CSV parsers)
        if isinstance(t, dict):
            row = t
        elif isinstance(t, (list, tuple)):
            # best-effort mapping (keeps API resilient if CSV parsing changes)
            cols_guess = ["ts_utc", "symbol", "side", "qty", "price"]
            row = {cols_guess[k]: t[k] for k in range(min(len(t), len(cols_guess)))}
        else:
            continue

        sym = str(row.get("symbol") or "")
        side = str(row.get("side") or "")

        # qty / price may come as strings
        try:
            qty = float(row.get("qty") or 0.0)
        except Exception:
            qty = 0.0
        try:
            price = float(row.get("price") or 0.0)
        except Exception:
            price = 0.0

        key = sym or "(unknown)"
        rec = by_token.setdefault(key, {"token": key, "net_usdc": 0.0, "trades": 0, "period": ""})
        rec["net_usdc"] += (-qty * price) if side.upper().startswith("BUY") else (qty * price)
        rec["trades"] += 1

    tokens = list(by_symbol.values())
    # sort by last_ts desc and take 10
    tokens.sort(key=lambda r: r.get("last_ts") or "", reverse=True)
    tokens = tokens[:10]

    fx = get_fx_usdc_eur()
    for r in tokens:
        r["net_usdc"] = round(float(r["net_usdc"]), 6)
        r["net_eur"] = round(float(r["net_usdc"]) * fx, 6) if fx else None
        r["period"] = f'{r.get("first_ts","")} → {r.get("last_ts","")}'
    return jsonify(ok=True, tokens=tokens, source=str(trades_path), fx_usdc_eur=fx)

@app.route("/api/pnl")
@require_basic_auth
def api_pnl():
    """
    UI expects bucket pnl for: session/week/month/year + trades/winrate/profit_factor.
    Uses latest *_trades.csv.
    """
    trades_path = find_latest(LOG_DIR, "*_trades.csv")
    if not trades_path:
        return jsonify(ok=True, session=None, week=None, month=None, year=None, trades=0, winrate=None, profit_factor=None, source=None)

    trades = load_trades_csv(trades_path)
    fx = get_fx_usdc_eur()
    now = datetime.now(timezone.utc)

    def parse_ts(s):
        try:
            return datetime.fromisoformat(s.replace("Z","+00:00"))
        except Exception:
            return None

    # compute per-trade pnl isn't available in raw trades; approximate from grouped net per symbol within window
    def bucket(days=None):
        cutoff = None
        if days is not None:
            cutoff = now - timedelta(days=days)
        # filter trades by cutoff
        ftr = []
        for t in trades:
            ts = parse_ts(t.get("ts_utc") or "")
            if not ts:
                continue
            if cutoff and ts < cutoff:
                continue
            ftr.append(t)
        # net USDC = sells - buys for that window
        net = 0.0
        for t in ftr:
            side = (t.get("side") or "").upper()
            qty = float(t.get("qty") or 0.0)
            price = float(t.get("price") or 0.0)
            usdc = qty * price
            if side == "SELL":
                net += usdc
            elif side == "BUY":
                net -= usdc
        return {
            "usdc": round(net, 6),
            "eur": round(net * fx, 6) if fx else None,
            "trades": len(ftr),
        }

    session = bucket(days=None)
    week = bucket(days=7)
    month = bucket(days=30)
    year = bucket(days=365)

    # winrate/profit_factor cannot be derived reliably without closed-trade PnL; keep null
    return jsonify(
        ok=True,
        session=session,
        week=week,
        month=month,
        year=year,
        trades=session["trades"],
        winrate=None,
        profit_factor=None,
        source=str(trades_path),
        fx_usdc_eur=fx
    )


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
    csv_path = find_latest("*_trades.csv")
    trades, _ = load_trades_csv(csv_path) if csv_path else ([], [])
    rows, fx = summarize_pnl_by_symbol(trades)

    # hide empty tokens (no trades)
    rows = [r for r in rows if int(r.get("trades") or 0) > 0]

    total_usdc = 0.0
    has_pnl = False
    for r in trades:
        v = fnum(r.get("pnl"))
        if v is not None:
            total_usdc += v
            has_pnl = True
    total_usdc = round(total_usdc, 6) if has_pnl else 0.0
    total_eur = round(usdc_to_eur(total_usdc, fx), 6) if fx is not None else None

    return jsonify({
        "ok": True,
        "fx_usdc_eur": fx,
        # frontend expects .rows
        "rows": rows[:60],
        # backward compat
        "tokens": rows[:60],
        "total_usdc": total_usdc,
        "total_eur": total_eur,
        "source": str(csv_path) if csv_path else "",
    })

@app.route("/api/wallet")
@require_basic_auth
def api_wallet():
    snap = load_wallet_snapshot()
    norm_rows, fx = normalize_wallet(snap.get("data"))

    # UI (static/app.js) expects: rows[{asset, free, locked, total, usdc_value, eur_value}]
    rows = []
    for r in norm_rows:
        rows.append({
            "asset": r.get("asset"),
            "free": r.get("free"),
            "locked": r.get("locked"),
            "total": r.get("total"),
            "usdc_value": r.get("value_usdc"),
            "eur_value": r.get("value_eur"),
        })

    # totals
    t_usdc = 0.0
    used = False
    for r in rows:
        v = r.get("usdc_value")
        if isinstance(v, (int, float)):
            t_usdc += float(v)
            used = True
    t_usdc = round(t_usdc, 6) if used else None
    t_eur = round(usdc_to_eur(t_usdc, fx), 6) if (t_usdc is not None and fx is not None) else None

    return jsonify({
        "ok": True,
        "file": snap.get("file", ""),
        "fx_usdc_eur": fx,
        # primary (frontend)
        "rows": rows[:200],
        "total_usdc": t_usdc,
        "total_eur": t_eur,
        # backward compat
        "assets": rows[:200],
        "total_value_usdc": t_usdc,
        "total_value_eur": t_eur,
    })

if __name__ == "__main__":
    # local bind only; nginx will proxy
    port = int(os.getenv("DASH_PORT", "8099"))
    app.run(host="127.0.0.1", port=port)
