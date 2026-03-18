#!/usr/bin/env python3
import os
import re
import csv
import json
import time
import hmac
import hashlib
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, render_template, request, abort


def _json_safe(obj):
    """Make objects JSON-serializable (Path/Decimal/datetime)."""
    from pathlib import Path
    from decimal import Decimal
    from datetime import datetime
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Decimal):
        try:
            return float(obj)
        except Exception:
            return str(obj)
    if isinstance(obj, datetime):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    return obj


APP_TITLE = "botdash"

# ---- config (env) ----
def _resolve_runtime_path(env_name: str, default_path: str, repo_relative: str) -> Path:
    raw = os.getenv(env_name, default_path)
    candidate = Path(raw).expanduser().resolve()
    if candidate.exists():
        return candidate

    repo_base = Path(__file__).resolve().parents[1]
    fallback = (repo_base / repo_relative).resolve()
    if fallback.exists():
        return fallback
    return candidate


BASE_DIR = _resolve_runtime_path("BOT_BASE_DIR", "/opt/binance-aifout-bot", ".")
LOG_DIR = _resolve_runtime_path("BOT_LOG_DIR", str(BASE_DIR / "data" / "logs"), "data/logs")
RUNTIME_DIR = _resolve_runtime_path("BOT_RUNTIME_DIR", str(BASE_DIR / "data" / "runtime"), "data/runtime")
SERVICE_ENV = _resolve_runtime_path("BOT_SERVICE_ENV", str(BASE_DIR / ".service.env"), ".service.env")

DASH_USER = os.getenv("DASH_USER", "")
DASH_PASS = os.getenv("DASH_PASS", "")

# FX rate: USDC -> EUR (set via env or BOT_RUNTIME_DIR/fx.json)
FX_USDC_EUR_ENV = os.getenv("FX_USDC_EUR", "").strip()
WALLET_SNAPSHOT_MAX_AGE_SEC = float(os.getenv("WALLET_SNAPSHOT_MAX_AGE_SEC", "120"))
LIVE_WALLET_CACHE_TTL_SEC = float(os.getenv("LIVE_WALLET_CACHE_TTL_SEC", "10"))
FX_CACHE_TTL_SEC = float(os.getenv("FX_CACHE_TTL_SEC", "60"))
PRICE_CACHE_TTL_SEC = float(os.getenv("PRICE_CACHE_TTL_SEC", "30"))
WALLET_MIN_DISPLAY = float(os.getenv("WALLET_MIN_DISPLAY", "0.9"))


# systemd units allowlist (security)
UNITS = [
    "binance-aifout-bot.service",
    "token-profile-selector.service",
    "token-profile-selector.timer",
]

MAX_TAIL_LINES = 600

app = Flask(__name__)
_LIVE_WALLET_CACHE = {"ts": 0.0, "data": None}
_FX_CACHE = {"ts": 0.0, "usdc_eur": None}
_PRICE_CACHE = {"ts": 0.0, "prices": None}

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

def _read_key_value_file(path: Path):
    out = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'").strip('"')
    except Exception:
        return {}
    return out

def _load_bot_env():
    merged = {}
    for path in (
        SERVICE_ENV,
        BASE_DIR / ".env",
        Path.cwd() / ".env",
        Path.cwd() / "config" / ".service.env",
    ):
        if path.exists():
            merged.update(_read_key_value_file(path))
    return merged

def _wallet_snapshot_ts(data):
    if not isinstance(data, dict):
        return None
    for key in ("ts", "timestamp", "updated_at"):
        v = data.get(key)
        try:
            return float(v)
        except Exception:
            continue
    return None

def _snapshot_is_fresh(data):
    ts = _wallet_snapshot_ts(data)
    if ts is None:
        return False
    return (time.time() - ts) <= WALLET_SNAPSHOT_MAX_AGE_SEC

def _signed_binance_get(path: str, params: dict | None = None):
    env = _load_bot_env()
    api_key = (os.getenv("BINANCE_API_KEY") or env.get("BINANCE_API_KEY") or "").strip()
    api_secret = (os.getenv("BINANCE_API_SECRET") or env.get("BINANCE_API_SECRET") or "").strip()
    base_url = (os.getenv("BINANCE_BASE_URL") or env.get("BINANCE_BASE_URL") or "https://api.binance.com").strip()
    if not api_key or not api_secret:
        return None

    payload = dict(params or {})
    payload["timestamp"] = int(time.time() * 1000)
    payload["recvWindow"] = 5000
    query = urlencode(payload)
    sig = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
    url = f"{base_url}{path}?{query}&signature={sig}"
    req = urllib.request.Request(url, headers={"X-MBX-APIKEY": api_key, "User-Agent": "botdash"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _fetch_live_wallet_snapshot():
    now = time.time()
    cached = _LIVE_WALLET_CACHE.get("data")
    if cached is not None and (now - float(_LIVE_WALLET_CACHE.get("ts", 0.0))) < LIVE_WALLET_CACHE_TTL_SEC:
        return cached
    try:
        account = _signed_binance_get("/api/v3/account")
        if not isinstance(account, dict):
            return None
        snap = {
            "ts": now,
            "source": "binance_live",
            "balances": account.get("balances") or [],
            "accountType": account.get("accountType"),
            "updateTime": account.get("updateTime"),
        }
        _LIVE_WALLET_CACHE["ts"] = now
        _LIVE_WALLET_CACHE["data"] = snap
        return snap
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

def load_trades_csv(csv_path: Path | None = None, max_rows: int | None = 1500):
    """
    Returns list[dict] for recent rows.
    If csv_path is None, aggregate every *_trades.csv under LOG_DIR.
    """
    rows = []
    cols = []
    paths = []
    if csv_path is None:
        paths = sorted(LOG_DIR.glob("*_trades.csv"))
    elif csv_path.exists():
        paths = [csv_path]

    if not paths:
        return [], []

    for path in paths:
        try:
            with path.open("r", newline="", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                if not cols and reader.fieldnames:
                    cols = reader.fieldnames
                symbol_from_file = path.name.split("_trades.csv")[0]
                for r in reader:
                    row = dict(r)
                    if not row.get("symbol"):
                        row["symbol"] = symbol_from_file
                    row.setdefault("ts_utc", row.get("utc") or row.get("timestamp") or "")
                    row.setdefault("event", "")
                    row.setdefault("reason", "")
                    row.setdefault("side", "")
                    row.setdefault("qty", "")
                    row.setdefault("price", "")
                    row.setdefault("pnl", row.get("pnl_usdc") or "")
                    row["src"] = path.name
                    rows.append(row)
        except Exception:
            continue

    rows.sort(key=lambda r: (str(r.get("ts_utc") or ""), str(r.get("symbol") or "")))
    if isinstance(max_rows, int) and max_rows > 0 and len(rows) > max_rows:
        rows = rows[-max_rows:]
    return rows, cols

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

def parse_trade_ts(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if re.fullmatch(r"\d{10}(?:\.\d+)?", s):
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
        if re.fullmatch(r"\d{13}", s):
            return datetime.fromtimestamp(float(s) / 1000.0, tz=timezone.utc)
    except Exception:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None

def extract_closed_pnl_rows(trades):
    rows = []
    for t in trades:
        pnl = fnum(t.get("pnl"))
        if pnl is None:
            continue
        ts = parse_trade_ts(t.get("ts_utc") or t.get("ts") or t.get("timestamp"))
        rows.append({
            "ts": ts,
            "symbol": (t.get("symbol") or "").strip().upper(),
            "pnl": pnl,
            "src": t.get("src") or "",
        })
    rows.sort(key=lambda r: ((r.get("ts") or datetime.min.replace(tzinfo=timezone.utc)), r.get("symbol") or ""))
    return rows

def build_equity_points(pnl_rows, fx):
    points = []
    cum_usdc = 0.0
    for row in pnl_rows:
        cum_usdc += float(row.get("pnl") or 0.0)
        ts = row.get("ts")
        points.append({
            "ts": ts.isoformat() if isinstance(ts, datetime) else "",
            "usdc": round(cum_usdc, 6),
            "eur": round(usdc_to_eur(cum_usdc, fx), 6) if fx is not None else None,
            "symbol": row.get("symbol") or "",
        })
    return points

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

def _fetch_all_ticker_prices_binance():
    url = "https://api.binance.com/api/v3/ticker/price"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "botdash"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, list):
            return None
        prices = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            price = fnum(item.get("price"))
            if symbol and isinstance(price, float) and price > 0:
                prices[symbol] = price
        return prices
    except Exception:
        return None

def get_all_ticker_prices():
    now = time.time()
    cached = _PRICE_CACHE.get("prices")
    if isinstance(cached, dict) and (now - float(_PRICE_CACHE.get("ts", 0.0))) < PRICE_CACHE_TTL_SEC:
        return cached

    prices = _fetch_all_ticker_prices_binance()
    _PRICE_CACHE["ts"] = now
    _PRICE_CACHE["prices"] = prices if isinstance(prices, dict) else None
    return _PRICE_CACHE["prices"]

def asset_to_usdc(asset, total, prices):
    try:
        qty = float(total)
    except Exception:
        return None
    if qty <= 0:
        return None

    asset = str(asset or "").strip().upper()
    if not asset:
        return None
    if asset == "USDC":
        return qty
    if not isinstance(prices, dict) or not prices:
        return None

    direct = prices.get(f"{asset}USDC")
    if isinstance(direct, (int, float)) and direct > 0:
        return qty * float(direct)

    inverse = prices.get(f"USDC{asset}")
    if isinstance(inverse, (int, float)) and inverse > 0:
        return qty / float(inverse)

    via_usdt = prices.get(f"{asset}USDT")
    usdt_usdc = prices.get("USDTUSDC")
    if isinstance(via_usdt, (int, float)) and via_usdt > 0 and isinstance(usdt_usdc, (int, float)) and usdt_usdc > 0:
        return qty * float(via_usdt) * float(usdt_usdc)

    via_usdt_inverse = prices.get(f"USDT{asset}")
    if isinstance(via_usdt_inverse, (int, float)) and via_usdt_inverse > 0 and isinstance(usdt_usdc, (int, float)) and usdt_usdc > 0:
        return qty / float(via_usdt_inverse) * float(usdt_usdc)

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
                if _snapshot_is_fresh(j):
                    return {"file": str(p), "data": j}
                break
    live = _fetch_live_wallet_snapshot()
    if live is not None:
        return {"file": "binance_live", "data": live}
    if 'j' in locals() and j is not None:
        return {"file": str(p), "data": j}
    return {"file": "", "data": None}

def normalize_wallet(wallet_data):
    # Output list[{asset, free, locked, total, value_usdc, value_eur}]
    fx = get_fx_usdc_eur()
    prices = get_all_ticker_prices()
    rows = []

    def has_visible_balance(free, locked, total, value_usdc, value_eur):
        values = (free, locked, total, value_usdc, value_eur)
        return any(isinstance(v, (int, float)) and abs(v) > 1e-12 for v in values)

    def display_metric(row):
        v_usdc = row.get("value_usdc")
        if isinstance(v_usdc, (int, float)):
            return float(v_usdc)
        total = row.get("total")
        if isinstance(total, (int, float)):
            return float(total)
        return 0.0

    if not wallet_data:
        return rows, fx
    data = wallet_data
    if isinstance(data, dict) and isinstance(data.get("account"), dict):
        data = data.get("account") or data
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
                if not has_visible_balance(free, locked, total, None, None):
                    continue
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
        if v_usdc is None and total is not None:
            v_usdc = asset_to_usdc(asset, total, prices)
        if v_eur is None and v_usdc is not None and fx is not None:
            v_eur = usdc_to_eur(v_usdc, fx)
        if not has_visible_balance(free, locked, total, v_usdc, v_eur):
            continue
        rows.append({
            "asset": asset,
            "free": free,
            "locked": locked,
            "total": total,
            "value_usdc": round(v_usdc, 6) if v_usdc is not None else None,
            "value_eur": round(v_eur, 6) if v_eur is not None else None,
        })
    rows = [r for r in rows if display_metric(r) > WALLET_MIN_DISPLAY]
    rows.sort(key=lambda r: (display_metric(r), r.get("asset") or ""), reverse=True)
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
        "host": os.uname().nodename,
        "utc": utc_now_str(),
        "base": str(BASE_DIR),
        "logs": str(LOG_DIR),
        "fx_usdc_eur": get_fx_usdc_eur(),
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
    trades, cols = load_trades_csv()
    stats = compute_stats(trades)

    # session: from last BOOT marker if present
    # best effort: use all current file as "session"
    return jsonify({
        "ok": True,
        "file": str(find_latest("*_trades.csv") or ""),
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
    ok, out = systemctl(action, unit)
    return jsonify(ok=ok, unit=unit, action=action, output=out, error="" if ok else out)

@app.route("/api/trades")
@require_basic_auth
def api_trades():
    """
    Recent trade/decision rows for the dashboard table.
    """
    trades, _cols = load_trades_csv()
    rows = sorted(trades, key=lambda t: str(t.get("ts_utc") or ""), reverse=True)[:120]
    return jsonify(ok=True, rows=rows, source=str(find_latest("*_trades.csv") or ""))

@app.route("/api/pnl")
@require_basic_auth
def api_pnl():
    """
    UI expects bucket pnl for: session/week/month/year + trades/winrate/profit_factor.
    Uses latest *_trades.csv.
    """
    trades, _cols = load_trades_csv(max_rows=None)
    trades_path = find_latest(LOG_DIR, "*_trades.csv")
    if not trades:
        return jsonify(
            ok=True,
            session=None,
            week=None,
            month=None,
            year=None,
            trades=0,
            winrate=None,
            profit_factor=None,
            source=None,
            equity_points=[],
        )

    fx = get_fx_usdc_eur()
    now = datetime.now(timezone.utc)
    pnl_rows = extract_closed_pnl_rows(trades)
    session_rows = []
    if trades_path and Path(trades_path).exists():
        session_trades, _ = load_trades_csv(csv_path=Path(trades_path), max_rows=None)
        session_rows = extract_closed_pnl_rows(session_trades)

    def bucket(days=None, rows=None):
        cutoff = None
        if days is not None:
            cutoff = now - timedelta(days=days)
        pnl_vals = []
        scope = rows if rows is not None else pnl_rows
        for t in scope:
            ts = t.get("ts")
            pnl = t.get("pnl")
            if cutoff and ts < cutoff:
                continue
            pnl_vals.append(pnl)
        net = sum(pnl_vals)
        wins = [v for v in pnl_vals if v > 0]
        losses = [v for v in pnl_vals if v < 0]
        return {
            "usdc": round(net, 6),
            "eur": round(net * fx, 6) if fx else None,
            "trades": len(pnl_vals),
            "winrate": round((len(wins) / len(pnl_vals) * 100.0), 2) if pnl_vals else None,
            "profit_factor": round((sum(wins) / abs(sum(losses))), 3) if losses else (round(sum(wins), 3) if wins else None),
        }

    session = bucket(rows=session_rows or pnl_rows)
    week = bucket(days=7, rows=pnl_rows)
    month = bucket(days=30, rows=pnl_rows)
    year = bucket(days=365, rows=pnl_rows)
    equity_points = build_equity_points(pnl_rows[-400:], fx)

    # winrate/profit_factor cannot be derived reliably without closed-trade PnL; keep null
    return jsonify(
        ok=True,
        session=session,
        week=week,
        month=month,
        year=year,
        trades=session["trades"],
        winrate=session["winrate"],
        profit_factor=session["profit_factor"],
        source=str(trades_path),
        fx_usdc_eur=fx,
        equity_points=equity_points,
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
    trades, _ = load_trades_csv()
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

    return jsonify(_json_safe({
        "ok": True,
        "fx_usdc_eur": fx,
        # frontend expects .rows
        "rows": rows[:60],
        # backward compat
        "tokens": rows[:60],
        "total_usdc": total_usdc,
        "total_eur": total_eur,
        "source": csv_path,
    }))
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
