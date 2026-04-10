#!/usr/bin/env python3
import sys
import time
import os
from pathlib import Path
from decimal import Decimal, ROUND_CEILING


def momentum_ok(
    ticks,
    window_sec: float,
    min_pct: float,
    min_up_ratio: float,
    range_min_pct: float,
    range_relax_pct: float,
    range_relax_up_ratio: float,
    allow_warmup_entry: bool,
):
    """
    ticks: list[(ts, bid)]
    Retour: (ok, mom_pct, up_ratio, range_pct)

    Tiered relaxation:
    - momentum fort => tolérance up_ratio plus basse
    - range fort => tolérance up_ratio et min_pct plus basses
    """
    if not ticks:
        return False, 0.0, 0.0, 0.0

    now = ticks[-1][0]
    cutoff = now - window_sec
    win = [(ts, bid) for ts, bid in ticks if ts >= cutoff]

    if len(win) < 2:
        return bool(allow_warmup_entry), 0.0, 0.0, 0.0

    p0 = win[0][1]
    p1 = win[-1][1]
    if p0 <= 0 or p1 <= 0:
        return False, 0.0, 0.0, 0.0

    mom = (p1 - p0) / p0

    ups = 0
    tot = 0
    last = win[0][1]
    for _, b in win[1:]:
        if b > last:
            ups += 1
        tot += 1
        last = b

    up_ratio = (ups / tot) if tot else 0.0

    win_prices = [bid for _, bid in win]
    low = min(win_prices)
    high = max(win_prices)
    range_pct = ((high - low) / low) if low > 0 else 0.0

    relaxed = min_up_ratio
    relaxed_min_pct = min_pct
    if mom >= min_pct * 4:
        relaxed *= 0.6
    elif mom >= min_pct * 2:
        relaxed *= 0.8
    if range_pct >= range_min_pct:
        relaxed *= range_relax_up_ratio
        relaxed_min_pct *= range_relax_pct

    ok = (mom >= relaxed_min_pct) and (up_ratio >= relaxed)
    return ok, mom, up_ratio, range_pct


def instant_momentum_ok(ticks, threshold_pct: float, lookback: int, min_up_ratio: float):
    # ticks: list[(ts, bid)] -> (ok, mom_pct, up_ratio)
    if not ticks or len(ticks) < max(2, int(lookback)):
        return False, 0.0, 0.0
    lookback = int(lookback)
    recent = ticks[-lookback:]
    p0 = float(recent[0][1])
    p1 = float(recent[-1][1])
    if p0 <= 0 or p1 <= 0:
        return False, 0.0, 0.0
    mom = (p1 - p0) / p0
    ups = 0
    tot = 0
    last = float(recent[0][1])
    for _, b in recent[1:]:
        b = float(b)
        if b > last:
            ups += 1
        tot += 1
        last = b
    up_ratio = (ups / tot) if tot else 0.0
    ok = (mom >= float(threshold_pct)) and (up_ratio >= float(min_up_ratio))
    return ok, mom, up_ratio



def p1p4_ok(ticks, lookback: int = 4):
    """Simple micro-structure confirmation.
    Uses bid ticks: requires current bid strictly greater than bid N ticks ago.
    """
    try:
        lookback = int(lookback)
        if not ticks or len(ticks) < lookback:
            return False
        p_old = float(ticks[-lookback][1])
        p_now = float(ticks[-1][1])
        return (p_now > p_old)
    except Exception:
        return False

def tick_confirmation_ok(ticks, lookback: int, min_pct: float):
    """Tick-to-tick micro confirmation.
    Requires monotone non-decreasing bids over last N ticks and total progress >= min_pct.
    ticks: list[(ts, bid)] (uses bid side)
    Returns (ok, prog)
    """
    if not ticks or len(ticks) < int(lookback):
        return False, 0.0
    lookback = max(2, int(lookback))
    recent = ticks[-lookback:]
    for i in range(1, len(recent)):
        if float(recent[i][1]) < float(recent[i - 1][1]):
            return False, 0.0
    p0 = float(recent[0][1])
    plast = float(recent[-1][1])
    if p0 <= 0:
        return False, 0.0
    prog = (plast - p0) / p0
    return (prog >= float(min_pct)), prog


def flow_pressure_ok(ticks, lookback: int, min_ratio: float, max_single_drop_pct: float):
    """Defensive tape quality filter.
    Rejects fake strength where small upticks are repeatedly erased by larger downticks.
    Uses recent MID ticks and compares cumulative up/down magnitudes.
    Returns (ok, flow_ratio, worst_drop_pct, net_pct).
    """
    lookback = max(3, int(lookback))
    if not ticks or len(ticks) < lookback:
        return False, 0.0, 0.0, 0.0

    recent = [float(px) for _, px in ticks[-lookback:]]
    if recent[0] <= 0:
        return False, 0.0, 0.0, 0.0

    up_mag = 0.0
    down_mag = 0.0
    worst_drop_pct = 0.0
    for i in range(1, len(recent)):
        prev = float(recent[i - 1])
        cur = float(recent[i])
        if prev <= 0:
            continue
        d_pct = (cur - prev) / prev
        if d_pct > 0:
            up_mag += d_pct
        elif d_pct < 0:
            down_mag += (-d_pct)
            if (-d_pct) > worst_drop_pct:
                worst_drop_pct = (-d_pct)

    flow_ratio = (up_mag / down_mag) if down_mag > 0 else 999.0
    net_pct = (recent[-1] - recent[0]) / recent[0]
    ok = (
        (net_pct > 0.0)
        and (flow_ratio >= float(min_ratio))
        and (worst_drop_pct <= float(max_single_drop_pct))
    )
    return ok, flow_ratio, worst_drop_pct, net_pct


def descending_tape_ok(ticks, confirm_ticks: int) -> bool:
    confirm_ticks = max(2, int(confirm_ticks))
    if not ticks or len(ticks) < confirm_ticks:
        return False
    recent = [float(px) for _, px in ticks[-confirm_ticks:]]
    for i in range(1, len(recent)):
        if recent[i] >= recent[i - 1]:
            return False
    return True


def burst_entry_signal(ticks, spread: float, cfg):
    stats = {
        "return_pct": 0.0,
        "elapsed_sec": 0.0,
        "velocity_pct_per_sec": 0.0,
        "efficiency": 0.0,
        "pressure_ratio": 0.0,
        "max_single_drop_pct": 0.0,
        "required_return_pct": 0.0,
        "start_px": 0.0,
        "end_px": 0.0,
    }
    if not bool(getattr(cfg, "burstEntryEnabled", False)):
        return False, stats

    lookback = max(3, int(getattr(cfg, "burstLookbackTicks", 4) or 4))
    if not ticks or len(ticks) < lookback:
        return False, stats

    recent = ticks[-lookback:]
    t0 = float(recent[0][0])
    t1 = float(recent[-1][0])
    p0 = float(recent[0][1])
    p1 = float(recent[-1][1])
    elapsed = max(0.0, t1 - t0)

    stats["elapsed_sec"] = elapsed
    stats["start_px"] = p0
    stats["end_px"] = p1

    if p0 <= 0 or p1 <= 0:
        return False, stats

    max_window_sec = float(getattr(cfg, "burstMaxWindowSec", 0.0) or 0.0)
    if max_window_sec > 0 and elapsed > max_window_sec:
        return False, stats

    net_ret = (p1 - p0) / p0
    up_energy = 0.0
    down_energy = 0.0
    total_abs = 0.0
    max_single_drop = 0.0
    last = p0
    for _, px in recent[1:]:
        px = float(px)
        if last <= 0:
            last = px
            continue
        step_ret = (px - last) / last
        if step_ret > 0:
            up_energy += step_ret
        elif step_ret < 0:
            mag = -step_ret
            down_energy += mag
            if mag > max_single_drop:
                max_single_drop = mag
        total_abs += abs(step_ret)
        last = px

    pressure_ratio = (up_energy / down_energy) if down_energy > 0 else (999.0 if up_energy > 0 else 0.0)
    efficiency = (net_ret / total_abs) if total_abs > 0 and net_ret > 0 else 0.0
    velocity = (net_ret / elapsed) if elapsed > 0 else (999.0 if net_ret > 0 else 0.0)
    min_return = float(getattr(cfg, "burstMinReturnPct", 0.0) or 0.0)
    min_move_vs_spread = float(getattr(cfg, "burstMinMoveVsSpread", 0.0) or 0.0)
    required_return = max(min_return, float(spread) * min_move_vs_spread)

    stats["return_pct"] = net_ret
    stats["velocity_pct_per_sec"] = velocity
    stats["efficiency"] = efficiency
    stats["pressure_ratio"] = pressure_ratio
    stats["max_single_drop_pct"] = max_single_drop
    stats["required_return_pct"] = required_return

    ok = (
        net_ret >= required_return
        and velocity >= float(getattr(cfg, "burstMinVelocityPctPerSec", 0.0) or 0.0)
        and efficiency >= float(getattr(cfg, "burstMinEfficiency", 0.0) or 0.0)
        and pressure_ratio >= float(getattr(cfg, "burstMinPressureRatio", 0.0) or 0.0)
        and max_single_drop <= float(getattr(cfg, "burstMaxSingleDropPct", 1.0) or 1.0)
    )
    return ok, stats


def burst_exit_reason(pos, ticks, bid: float, spread: float, cfg):
    if pos is None or not bool(getattr(pos, "burstMode", False)):
        return None
    entry = float(getattr(pos, "entry", 0.0) or 0.0)
    if entry <= 0 or bid <= 0:
        return None

    age_sec = max(0.0, time.time() - float(getattr(pos, "ts_entry", time.time())))
    peak = max(float(getattr(pos, "high", bid) or bid), float(bid))
    extension_pct = ((peak - entry) / entry) if entry > 0 else 0.0
    drawdown_pct = ((peak - float(bid)) / peak) if peak > 0 else 0.0
    confirm_ticks = max(2, int(getattr(cfg, "burstExitConfirmTicks", 3) or 3))
    descending = descending_tape_ok(ticks, confirm_ticks)
    base_return_pct = max(0.0, float(getattr(pos, "burstBaseReturnPct", 0.0) or 0.0))

    drawdown_trigger = max(
        float(getattr(cfg, "burstExitMinDrawdownPct", 0.0) or 0.0),
        base_return_pct * float(getattr(cfg, "burstExitGivebackMult", 0.0) or 0.0),
        float(spread) * float(getattr(cfg, "burstExitDrawdownVsSpread", 0.0) or 0.0),
    )
    follow_ttl = float(getattr(cfg, "burstFollowTtlSec", 0.0) or 0.0)
    follow_min_extension = float(getattr(cfg, "burstFollowMinExtensionPct", 0.0) or 0.0)
    under_entry_pct = max(
        float(getattr(cfg, "burstExitUnderEntryPct", 0.0) or 0.0),
        float(getattr(cfg, "feeBufPct", 0.0) or 0.0) * 0.25,
    )
    under_entry = float(bid) <= (entry * (1.0 - under_entry_pct))

    if follow_ttl > 0 and age_sec <= follow_ttl and descending and extension_pct < follow_min_extension and under_entry:
        return (
            f"BURST_FAIL age={age_sec:.2f}s ext={extension_pct*100:.4f}% "
            f"need={follow_min_extension*100:.4f}% bid={bid:.8f} entry={entry:.8f}"
        )

    if descending and drawdown_pct >= drawdown_trigger:
        return (
            f"BURST_REVERSAL age={age_sec:.2f}s drawdown={drawdown_pct*100:.4f}% "
            f"trigger={drawdown_trigger*100:.4f}% ext={extension_pct*100:.4f}%"
        )

    if follow_ttl > 0 and age_sec > follow_ttl and extension_pct < follow_min_extension and under_entry:
        return (
            f"BURST_STALL age={age_sec:.2f}s ext={extension_pct*100:.4f}% "
            f"need={follow_min_extension*100:.4f}% bid={bid:.8f} entry={entry:.8f}"
        )
    return None


def ticks_fresh(ticks, max_age_sec: float) -> bool:
    if not ticks:
        return False
    age = time.time() - float(ticks[-1][0])
    return age <= float(max_age_sec)


def orderbook_imbalance_ok(bx, symbol: str, min_ratio: float, depth_levels: int):
    # Returns (ok, ratio). Uses public depth endpoint.
    try:
        depth_levels = max(1, min(100, int(depth_levels)))
        depth = bx.get('/api/v3/depth', {'symbol': symbol, 'limit': max(5, depth_levels)})
        bids = (depth.get('bids') or [])[:depth_levels]
        asks = (depth.get('asks') or [])[:depth_levels]
        bid_vol = sum(float(q) for _, q in bids)
        ask_vol = sum(float(q) for _, q in asks)
        if ask_vol <= 0:
            return False, 0.0
        ratio = bid_vol / ask_vol
        return (ratio >= float(min_ratio)), ratio
    except Exception:
        return False, 0.0


from core.config import loadConfig, pickProfile, applyRiskConfig
from core.logging import LogDayContext, tradeLogger, tradeCsvLogger, errorLogger, ensureCsvHeader, local_timestamp
from services.ipguard import vpnCheckOrDie

from exchange.binance import Binance
from exchange.stream import Stream

from execution.orders import placeLimit, waitFillOrCancel
from state.wallet_sync import walletSyncEvery
from state.position import Position

from indicators.basic import fmt, computeSignals, computeMarketContext


def round_step(qty: float, step: float) -> float:
    dqty = Decimal(str(qty))
    dstep = Decimal(str(step))
    return float((dqty // dstep) * dstep)



def round_step_up(qty: float, step: float) -> float:
    if step <= 0:
        return float(qty)
    dqty = Decimal(str(qty))
    dstep = Decimal(str(step))
    q = (dqty / dstep).to_integral_value(rounding=ROUND_CEILING)
    return float(q * dstep)


def round_tick_down(price: float, tick: float) -> float:
    """Floor price to tick size."""
    if tick <= 0:
        return float(price)
    dprice = Decimal(str(price))
    dtick = Decimal(str(tick))
    return float((dprice // dtick) * dtick)


def round_tick_up(price: float, tick: float) -> float:
    """Ceil price to tick size."""
    if tick <= 0:
        return float(price)
    dprice = Decimal(str(price))
    dtick = Decimal(str(tick))
    q = (dprice / dtick).to_integral_value(rounding=ROUND_CEILING)
    return float(q * dtick)
def compute_buy_price(best_bid: float, best_ask: float, cross_spread: bool, tick: float) -> float:
    """Return the BUY price according to the configured aggressiveness."""
    if cross_spread:
        return round_tick_up(best_ask, tick)
    return round_tick_down(best_bid, tick)


def load_blocked_symbols(path: Path) -> set[str]:
    try:
        if not path.exists():
            return set()
        blocked = set()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            symbol = line.strip().upper()
            if symbol:
                blocked.add(symbol)
        return blocked
    except Exception:
        return set()


def persist_blocked_symbol(path: Path, symbol: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        blocked = load_blocked_symbols(path)
        symbol = (symbol or "").strip().upper()
        if not symbol or symbol in blocked:
            return
        with path.open("a", encoding="utf-8") as f:
            f.write(symbol + "\n")
    except Exception:
        pass


def get_symbol_filters(bx: Binance, symbol: str):
    try:
        exch = bx.get("/api/v3/exchangeInfo", {"symbol": symbol})
    except Exception as e:
        # invalid symbol or API error
        raise RuntimeError(f"Invalid or non-spot symbol: {symbol}") from e
    sym = exch["symbols"][0]
    filters = {f["filterType"]: f for f in sym["filters"]}

    tick = float(filters["PRICE_FILTER"]["tickSize"])
    step = float(filters["LOT_SIZE"]["stepSize"])

    if "MIN_NOTIONAL" in filters:
        minNotional = float(filters["MIN_NOTIONAL"]["minNotional"])
    elif "NOTIONAL" in filters:
        minNotional = float(filters["NOTIONAL"]["minNotional"])
    else:
        minNotional = 0.0

    return tick, step, minNotional


def get_account_with_retry(bx: Binance, cfg):
    max_retries = int(getattr(cfg, "walletMaxRetries", 0))
    backoff = float(getattr(cfg, "walletRetryBackoffSec", 0.0))
    for attempt in range(max_retries + 1):
        try:
            return bx.get("/api/v3/account", signed=True)
        except Exception:
            if attempt >= max_retries:
                return None
            time.sleep(backoff)
    return None


def get_usdc_balance_safe(bx: Binance, cfg):
    acc = get_account_with_retry(bx, cfg)
    if not acc:
        return None
    balances = acc.get("balances", [])
    for b in balances:
        if b.get("asset") == "USDC":
            return float(b.get("free", "0"))
    return 0.0


def get_asset_balance_safe(bx: Binance, cfg, asset: str):
    acc = get_account_with_retry(bx, cfg)
    if not acc:
        return None
    balances = acc.get("balances", [])
    for b in balances:
        if b.get("asset") == asset:
            return float(b.get("free", "0"))
    return 0.0


def _read_service_env_symbol(env_path: Path) -> str | None:
    try:
        if not env_path.exists():
            return None
        symbol = None
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().upper()
            v = v.strip()
            if k == "SYMBOL":
                symbol = v.upper()
                break
        if symbol:
            return symbol
        return None
    except Exception:
        return None


def _resolve_start_symbol() -> str | None:
    """
    Resolve trading symbol from argv, env, then .service.env.
    This keeps the bot launchable even if systemd forgets to inject SYMBOL.
    """
    arg_symbol = sys.argv[1].strip().upper() if len(sys.argv) >= 2 else ""
    if arg_symbol:
        return arg_symbol

    env_symbol = (os.getenv("SYMBOL") or "").strip().upper()
    if env_symbol:
        return env_symbol

    env_path = Path(__file__).resolve().parent / ".service.env"
    return _read_service_env_symbol(env_path)


def _maybe_reexec_on_token_change(current_symbol: str, pos, last_env_mtime: float):
    """
    Hot-reload token only when IDLE (pos is None).
    If .service.env SYMBOL changed -> re-exec current process with new argv.
    """
    env_path = Path(__file__).resolve().parent / ".service.env"
    try:
        if not env_path.exists():
            return current_symbol, last_env_mtime
        mtime = env_path.stat().st_mtime
        if mtime == last_env_mtime:
            return current_symbol, last_env_mtime
        # only consider switching when not in position
        if pos is not None:
            return current_symbol, mtime
        new_symbol = _read_service_env_symbol(env_path)
        if not new_symbol or new_symbol == current_symbol:
            return current_symbol, mtime
        _reexec_to_symbol(current_symbol, new_symbol, source=".service.env")
    except SystemExit:
        raise
    except Exception:
        return current_symbol, last_env_mtime
    return current_symbol, last_env_mtime


def _reexec_to_symbol(current_symbol: str, new_symbol: str, *, source: str = ".service.env"):
    msg = f"TOKEN_SWITCH old={current_symbol} new={new_symbol} source={source}"
    print(msg)
    try:
        logTrade(msg)
    except Exception:
        pass
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), new_symbol])


def _pending_token_switch(current_symbol: str, last_env_mtime: float):
    """
    Read .service.env and report a requested symbol switch without re-execing yet.
    Returns: (requested_symbol_or_none, updated_mtime)
    """
    env_path = Path(__file__).resolve().parent / ".service.env"
    try:
        if not env_path.exists():
            return None, last_env_mtime
        mtime = env_path.stat().st_mtime
        if mtime == last_env_mtime:
            return None, last_env_mtime
        new_symbol = _read_service_env_symbol(env_path)
        if not new_symbol or new_symbol == current_symbol:
            return None, mtime
        return new_symbol, mtime
    except Exception:
        return None, last_env_mtime

def main():
    symbol = _resolve_start_symbol()
    if not symbol:
        print("USAGE: python3 main.py SYMBOLUSDC")
        print("Or define SYMBOL in environment / .service.env")
        sys.exit(1)
    last_env_mtime = 0.0

    cfg = loadConfig()
    poll = float(getattr(cfg, 'idleSleep', getattr(cfg, 'poll', 0.2)))  # legacy alias
    cfg = applyRiskConfig(cfg)
    profile = pickProfile()
    vpnCheckOrDie(cfg.ipFile, cfg.ipCheckTimeout)

    bx = Binance(
        cfg.apiKey,
        cfg.apiSecret,
        cfg.baseUrl,
        cfg.httpTimeout,
        cfg.httpRetries,
        cfg.httpBackoff
    )

    stream = Stream(cfg, symbol)
    logDayCtx = LogDayContext()
    logTrade = tradeLogger(cfg, symbol, logDayCtx)
    logCsv = tradeCsvLogger(cfg, symbol, logDayCtx)
    logErr = errorLogger(cfg, symbol, logDayCtx)
    ensureCsvHeader(cfg, symbol, logDayCtx)

    try:


        tick, step, minNotional = get_symbol_filters(bx, symbol)


    except RuntimeError as e:


        print(str(e))


        sys.exit(1)

    exchangeMinNotional = float(minNotional)
    minNotional = max(
        exchangeMinNotional,
        float(getattr(cfg, "minOrderNotionalUsdc", 0.0) or 0.0),
    )

    cap = float(getattr(cfg, "maxUsdcPerTrade", 50.0))

    # Momentum params (defaults: scalping spot)
    momWindowSec = float(getattr(cfg, "momWindowSec", 30.0))
    momMinPct = float(getattr(cfg, "momMinPct", 0.0005))          # 0.05%
    momMinUpRatio = float(getattr(cfg, "momMinUpRatio", 0.55))    # 55% ticks up
    momRangeMinPct = float(getattr(cfg, "momRangeMinPct", 0.003))
    momRangeRelaxPct = float(getattr(cfg, "momRangeRelaxPct", 0.6))
    momRangeRelaxUpRatio = float(getattr(cfg, "momRangeRelaxUpRatio", 0.75))

    logTrade(f"SESSION_START symbol={symbol} dry_run={int(bool(getattr(cfg,'dryRun',False)))} profile={profile.name} strategy={getattr(cfg,'strategyName','')} base_url={cfg.baseUrl}")

    print(
        f"INIT {symbol} tick={tick} step={step} "
        f"minNotional~{minNotional}USDC (exchange={exchangeMinNotional}) cap={cap}USDC"
    )
    print(
        f"PROFILE {profile.name} "
        f"EMA={profile.emaFast}/{profile.emaSlow} "
        f"RSI=[{profile.rsiMin},{profile.rsiMax}] "
        f"VOL_MULT={profile.volMult} "
        f"SPREAD_MAX={profile.spreadMax}"
    )
    print(
        f"MOM window={momWindowSec}s minPct={momMinPct} minUpRatio={momMinUpRatio} "
        f"rangeMinPct={momRangeMinPct} rangeRelaxPct={momRangeRelaxPct} "
        f"rangeRelaxUpRatio={momRangeRelaxUpRatio}"
    )
    print(f"CFG TTL={cfg.orderTtl}s POLL={cfg.orderPoll}s")
    if getattr(cfg, "dryRun", False):
        print("DRY_RUN ON (no real orders)")

    pos = None
    cooldownUntil = 0.0

    syncState = {"next": 0.0}
    syncInfo = {"usdc": 0.0}
    pendingSwitchSymbol = None
    pendingSwitchLogged = None
    lastExitInfo = None
    blockedSymbols = set()
    blocked_symbols_file = Path(getattr(cfg, "blockedSymbolsFile", Path("data/blocked_symbols.txt")))
    blockedSymbols.update(load_blocked_symbols(blocked_symbols_file))
    if symbol in blockedSymbols:
        msg = f"SYMBOL_BLOCKED_PERSISTED symbol={symbol} file={blocked_symbols_file}"
        print(msg)
        logTrade(msg)
        sys.exit(0)

    # ring buffers:
    # - ticks uses MID for legacy P1..P4 logic
    # - bidTicks uses BID for burst mode and fast exits
    ticks = []
    bidTicks = []

    stream.start()
    lastChk = 0.0
    lastTickSeq = 0

    lastHoldCsv = 0.0
    holdCsvEvery = float(getattr(cfg, 'holdCsvEvery', 60))
    signalCache = {"ts": 0.0, "s1": None, "s5": None, "market": None}
    signalRefreshSec = float(getattr(cfg, "signalRefreshSec", 15.0) or 15.0)

    def sync_log_day_anchor(position):
        if position is None:
            logDayCtx.clear_anchor()
            return
        logDayCtx.ensure_anchor_today()

    def load_signal_snapshot(now):
        cached_s1 = signalCache.get("s1")
        cached_s5 = signalCache.get("s5")
        cached_market = signalCache.get("market")
        if (
            cached_s1 is not None
            and cached_s5 is not None
            and cached_market is not None
            and (now - float(signalCache.get("ts", 0.0))) <= signalRefreshSec
        ):
            return cached_s1, cached_s5, cached_market
        try:
            s1, s5 = computeSignals(bx, symbol, profile)
            market = computeMarketContext(bx, symbol)
            signalCache["ts"] = now
            signalCache["s1"] = s1
            signalCache["s5"] = s5
            signalCache["market"] = market
            return s1, s5, market
        except Exception as e:
            logErr("SIGNAL_FETCH_FAIL", e)
            return None, None, None

    def maybe_hold(
        now,
        reason,
        spread,
        momPct,
        momRangePct,
        upRatio,
        bid,
        ask,
        mid,
        p1,
        p2,
        p3,
        p4,
        detail="",
        signal=None,
    ):
        nonlocal lastHoldCsv
        signal = signal or {}
        if (now - lastHoldCsv) < holdCsvEvery:
            return
        lastHoldCsv = now
        try:
            logCsv({
                'ts_utc': int(now),
                'symbol': symbol,
                'event': 'DECIDE_HOLD',
                'side': '',
                'qty': '',
                'price': '',
                'reason': reason,
                'pnl': '',
                'profile': profile.name,
                'dry_run': int(getattr(cfg, 'dryRun', False)),
                'spread_pct': float(spread)*100.0,
                'mom_pct': float(momPct)*100.0,
                'mom_range_pct': float(momRangePct)*100.0,
                'up_ratio': float(upRatio)*100.0,
                'rsi': '' if signal.get('rsi') is None else float(signal.get('rsi')),
                'ema1_ok': '' if signal.get('ema1_ok') is None else int(bool(signal.get('ema1_ok'))),
                'ema5_ok': '' if signal.get('ema5_ok') is None else int(bool(signal.get('ema5_ok'))),
                'vol_ok': '' if signal.get('vol_ok') is None else int(bool(signal.get('vol_ok'))),
                'bid': float(bid),
                'ask': float(ask),
                'mid': float(mid),
                'entry_price': '',
                'p1': '' if p1 is None else float(p1),
                'p2': '' if p2 is None else float(p2),
                'p3': '' if p3 is None else float(p3),
                'p4': '' if p4 is None else float(p4),
                'entry_vs_mid_pct': '',
                'mid_vs_entry_pct': '',
            })
        except Exception:
            pass
        try:
            detail_suffix = f" {detail}" if detail else ""
            logTrade(
                f"DECIDE_HOLD reason={reason} spread={spread*100:.4f}% mom={momPct*100:.4f}% "
                f"range={momRangePct*100:.4f}% up={upRatio*100:.2f}% "
                f"mid={mid:.8f} P1={p1} P2={p2} P3={p3} P4={p4}{detail_suffix}"
            )
            print(
                f"DECIDE_HOLD reason={reason} spread={spread*100:.4f}% mom={momPct*100:.4f}% "
                f"range={momRangePct*100:.4f}% up={upRatio*100:.2f}% "
                f"mid={mid:.8f} P1={p1} P2={p2} P3={p3} P4={p4}{detail_suffix}"
            )
        except Exception:
            pass

    while True:
        free_usdc = 0.0
        try:
            now = time.time()
            
            bid, ask, tick_ts, tick_seq = stream.snapshot()
            if bid <= 0 or ask <= 0:
                time.sleep(cfg.idleSleep)
                continue

            mid = (float(bid) + float(ask)) / 2.0
            has_new_tick = tick_seq != lastTickSeq
            if has_new_tick:
                lastTickSeq = tick_seq
                ticks.append((float(tick_ts or now), float(mid)))
                bidTicks.append((float(tick_ts or now), float(bid)))
                # prune: keep last max(window, 40s)
                keep_sec = max(momWindowSec, float(getattr(cfg, "ticksKeepMinSec", 40.0)))
                cutoff = now - keep_sec
                while ticks and ticks[0][0] < cutoff:
                    ticks.pop(0)
                while bidTicks and bidTicks[0][0] < cutoff:
                    bidTicks.pop(0)
            
            # cooldown log so it doesn't look frozen
            if now < cooldownUntil:
                if pos is None:
                    symbol, last_env_mtime = _maybe_reexec_on_token_change(symbol, pos, last_env_mtime)
                if pendingSwitchSymbol and pos is None:
                    _reexec_to_symbol(symbol, pendingSwitchSymbol, source="pending_switch")
                if now - lastChk >= cfg.chkEvery:
                    lastChk = now
                    left = cooldownUntil - now
                    print(f"CHK COOLDOWN:{fmt(left)}s BID:{fmt(bid)} ASK:{fmt(ask)}")
                time.sleep(cfg.idleSleep)
                continue
            
            # sync wallet -> adopt/clear position
            pos, syncState, syncInfo = walletSyncEvery(
                bx, symbol, pos, cfg,
                step=step,
                minNotional=minNotional,
                syncState=syncState,
                intervalSec=float(getattr(cfg, 'walletSyncSec', 5))
            )

            # cache USDC balance from wallet sync (used for entry sizing)
            usdc = None
            try:
                if isinstance(syncInfo, dict):
                    usdc = float(syncInfo.get('usdc', 0.0))
            except Exception:
                usdc = None


            # wallet placeholder guard: if entry==0, adopt current bid as entry (prevents phantom exits)
            if pos is not None and getattr(pos, 'entry', 0.0) == 0.0:
                if bid > 0:
                    pos.entry = float(bid)
                    pos.high = float(bid)
                    pos.stop = 0.0
                    try:
                        pos.init_stops(cfg, profile, tick=tick)
                    except Exception:
                        pass

            sync_log_day_anchor(pos)

            
            spread = (ask - bid) / bid if bid > 0 else 1.0
            spreadLimit = float(getattr(profile, "spreadMax", 1.0) or 1.0)
            momModeInstant = bool(getattr(cfg, 'momUseInstant', False))
            if momModeInstant:
                momOk, momPct, upRatio = instant_momentum_ok(
                    ticks,
                    float(momMinPct),
                    int(getattr(cfg, 'momLookback', 5)),
                    float(getattr(cfg, 'momMinUpRatio', 0.99)),
                )
                momRangePct = 0.0
            else:
                momOk, momPct, upRatio, momRangePct = momentum_ok(
                    ticks,
                    momWindowSec,
                    momMinPct,
                    momMinUpRatio,
                    momRangeMinPct,
                    momRangeRelaxPct,
                    momRangeRelaxUpRatio,
                    bool(getattr(cfg, 'allowWarmupEntry', False)),
                )

            burstOk, burstStats = burst_entry_signal(bidTicks, spread, cfg)
            market_ctx = signalCache.get("market")
            rsi_now = None
            ema1_ok = None
            ema5_ok = None
            vol_ok = None

            # P1..P4 (MID-based)
            def _get_p(tl, n_from_end: int):
                try:
                    return float(tl[-n_from_end][1])
                except Exception:
                    return None

            P1 = _get_p(ticks, 1)
            P2 = _get_p(ticks, 2)
            P3 = _get_p(ticks, 3)
            P4 = _get_p(ticks, 4)

            if now - lastChk >= cfg.chkEvery:
                lastChk = now
                chk_msg = (
                    f"CHK MOM:{fmt(momPct*100, Decimal('0.01'))}% "
                    f"RANGE:{fmt(momRangePct*100, Decimal('0.01'))}% "
                    f"UP:{fmt(upRatio*100, Decimal('0.01'))}% "
                    f"SPREAD:{fmt(spread*100)}% BID:{fmt(bid)} ASK:{fmt(ask)} MID:{fmt(mid)} "
                    f"P1:{fmt(P1) if P1 is not None else 'NA'} P2:{fmt(P2) if P2 is not None else 'NA'} "
                    f"P3:{fmt(P3) if P3 is not None else 'NA'} P4:{fmt(P4) if P4 is not None else 'NA'} "
                    f"STATE:{'IN_POS' if pos else 'IDLE'}"
                )
                print(chk_msg)
                logTrade(chk_msg)
            
            # Management rule: never consult .service.env while a position is open.
            # Token changes are only read/applied when fully idle.
            if pos is None:
                # Re-exec first, otherwise reading the pending switch would consume
                # the new mtime and block the actual symbol change.
                symbol, last_env_mtime = _maybe_reexec_on_token_change(symbol, pos, last_env_mtime)
                requested_symbol, observed_env_mtime = _pending_token_switch(symbol, last_env_mtime)
                if requested_symbol:
                    pendingSwitchSymbol = requested_symbol
                    last_env_mtime = observed_env_mtime
                if pendingSwitchSymbol == symbol:
                    pendingSwitchSymbol = None
                    pendingSwitchLogged = None

                if pendingSwitchSymbol and pendingSwitchLogged != pendingSwitchSymbol:
                    msg = f"TOKEN_SWITCH_PENDING old={symbol} new={pendingSwitchSymbol} state=IDLE"
                    print(msg)
                    logTrade(msg)
                    pendingSwitchLogged = pendingSwitchSymbol
            else:
                pendingSwitchSymbol = None
                pendingSwitchLogged = None

            # ===== ENTRY =====
            buySignal = False
            entryMode = ""
            if pos is None and not pendingSwitchSymbol and has_new_tick:
                if burstOk:
                    buySignal = True
                    entryMode = "BURST"
                elif (
                    (P1 is not None)
                    and (P2 is not None)
                    and (P3 is not None)
                    and (P4 is not None)
                ):
                    # Require a rising tape with actual progress, not just flat equal ticks.
                    buySignal = (P1 >= P2) and (P2 >= P3) and (P3 >= P4) and (P1 > P4)
                    if buySignal:
                        entryMode = "P"

            if buySignal:
                burstOverride = entryMode == "BURST"

                if symbol in blockedSymbols:
                    maybe_hold(
                        now,
                        'HOLD_BLOCKED_SYMBOL',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                max_mom_pct = float(getattr(cfg, "momMaxPct", 1.0) or 1.0)
                min_range_entry_pct = float(getattr(cfg, "minRangeEntryPct", 0.0) or 0.0)
                min_range_vs_spread = float(getattr(cfg, "minRangeVsSpread", 0.0) or 0.0)
                required_range_pct = max(min_range_entry_pct, float(spread) * min_range_vs_spread)
                strict_up_moves = int(P1 > P2) + int(P2 > P3) + int(P3 > P4)
                entry_min_strict_ups = max(1, int(getattr(cfg, "entryMinStrictUps", 1) or 1))
                hard_min_up_ratio = float(getattr(cfg, "entryHardMinUpRatio", 0.0) or 0.0)
                tape_progress_pct = ((float(P1) - float(P4)) / float(P4)) if (P1 is not None and P4 is not None and float(P4) > 0) else 0.0
                min_tape_progress_pct = float(getattr(cfg, "entryMinTapeProgressPct", 0.0) or 0.0)
                min_tape_progress_vs_spread = float(getattr(cfg, "entryMinTapeProgressVsSpread", 0.0) or 0.0)
                required_tape_progress_pct = max(
                    min_tape_progress_pct,
                    float(spread) * min_tape_progress_vs_spread,
                )

                if burstOverride:
                    burst_spread_limit = float(spreadLimit) * float(getattr(cfg, "burstSpreadMaxMult", 1.0) or 1.0)
                    if burst_spread_limit > 0 and spread > burst_spread_limit:
                        maybe_hold(
                            now,
                            'HOLD_BURST_SPREAD',
                            spread,
                            momPct,
                            momRangePct,
                            upRatio,
                            bid,
                            ask,
                            mid,
                            P1,
                            P2,
                            P3,
                            P4,
                            detail=f"spread_limit={burst_spread_limit*100:.4f}%",
                        )
                        time.sleep(cfg.idleSleep)
                        continue
                    burst_msg = (
                        f"BURST_TRIGGER ret={burstStats['return_pct']*100:.4f}% "
                        f"need={burstStats['required_return_pct']*100:.4f}% "
                        f"vel={burstStats['velocity_pct_per_sec']*100:.4f}%/s "
                        f"eff={burstStats['efficiency']:.3f} "
                        f"pressure={burstStats['pressure_ratio']:.2f} "
                        f"drop={burstStats['max_single_drop_pct']*100:.4f}% "
                        f"dt={burstStats['elapsed_sec']:.3f}s"
                    )
                    print(burst_msg)
                    logTrade(burst_msg)

                if (not burstOverride) and (not momOk):
                    maybe_hold(
                        now,
                        'HOLD_MOM',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                if (not burstOverride) and strict_up_moves < entry_min_strict_ups:
                    maybe_hold(
                        now,
                        'HOLD_TAPE',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                        detail=f"strict_ups={strict_up_moves} need={entry_min_strict_ups}",
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                if (not burstOverride) and required_tape_progress_pct > 0 and tape_progress_pct < required_tape_progress_pct:
                    maybe_hold(
                        now,
                        'HOLD_TAPE',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                        detail=(
                            f"tape_progress={tape_progress_pct*100:.4f}% "
                            f"required={required_tape_progress_pct*100:.4f}%"
                        ),
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                if (not burstOverride) and hard_min_up_ratio > 0 and upRatio < hard_min_up_ratio:
                    maybe_hold(
                        now,
                        'HOLD_UPRATIO',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                        detail=f"hard_min_up_ratio={hard_min_up_ratio*100:.2f}%",
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                if (not burstOverride) and bool(getattr(cfg, "tickEntryEnabled", False)):
                    tick_ok, tick_prog = tick_confirmation_ok(
                        ticks,
                        int(getattr(cfg, "tickEntryLookback", 3)),
                        float(getattr(cfg, "tickEntryMinPct", 0.0004)),
                    )
                    if not tick_ok:
                        maybe_hold(
                            now,
                            'HOLD_TICK',
                            spread,
                            momPct,
                            momRangePct,
                            upRatio,
                            bid,
                            ask,
                            mid,
                            P1,
                            P2,
                            P3,
                            P4,
                            detail=f"tick_prog={tick_prog*100:.4f}%",
                        )
                        time.sleep(cfg.idleSleep)
                        continue

                if (not burstOverride) and bool(getattr(cfg, "flowDefenseEnabled", False)):
                    flow_ok, flow_ratio, worst_drop_pct, flow_net_pct = flow_pressure_ok(
                        ticks,
                        int(getattr(cfg, "flowLookback", 8)),
                        float(getattr(cfg, "flowMinRatio", 1.2)),
                        float(getattr(cfg, "flowMaxSingleDropPct", 0.0025)),
                    )
                    if not flow_ok:
                        maybe_hold(
                            now,
                            'HOLD_FLOW',
                            spread,
                            momPct,
                            momRangePct,
                            upRatio,
                            bid,
                            ask,
                            mid,
                            P1,
                            P2,
                            P3,
                            P4,
                            detail=(
                                f"flow_ratio={flow_ratio:.2f} "
                                f"worst_drop={worst_drop_pct*100:.4f}% "
                                f"net={flow_net_pct*100:.4f}%"
                            ),
                        )
                        time.sleep(cfg.idleSleep)
                        continue

                if (not burstOverride) and lastExitInfo and lastExitInfo.get("symbol") == symbol:
                    last_reason = str(lastExitInfo.get("reason") or "")
                    last_pnl = float(lastExitInfo.get("pnl", 0.0) or 0.0)
                    last_exit_ts = float(lastExitInfo.get("ts", 0.0) or 0.0)
                    age_since_exit = max(0.0, now - last_exit_ts)

                    if last_pnl <= 0.0:
                        loss_cd = float(getattr(cfg, "reentryLossCooldownSec", 0.0) or 0.0)
                        if age_since_exit < loss_cd:
                            maybe_hold(
                                now,
                                'HOLD_REENTRY',
                                spread,
                                momPct,
                                momRangePct,
                                upRatio,
                                bid,
                                ask,
                                mid,
                                P1,
                                P2,
                                P3,
                                P4,
                                detail=f"last_reason={last_reason} age={age_since_exit:.1f}s cooldown={loss_cd:.1f}s",
                            )
                            time.sleep(cfg.idleSleep)
                            continue

                        reclaim_ref = max(
                            float(lastExitInfo.get("entry", 0.0) or 0.0),
                            float(lastExitInfo.get("exit", 0.0) or 0.0),
                        )
                        reclaim_pct = float(getattr(cfg, "reentryRecoveryPct", 0.0) or 0.0)
                        reclaim_need = reclaim_ref * (1.0 + reclaim_pct) if reclaim_ref > 0 else 0.0
                        if reclaim_need > 0 and mid < reclaim_need:
                            maybe_hold(
                                now,
                                'HOLD_RECLAIM',
                                spread,
                                momPct,
                                momRangePct,
                                upRatio,
                                bid,
                                ask,
                                mid,
                                P1,
                                P2,
                                P3,
                                P4,
                                detail=f"last_reason={last_reason} mid={mid:.8f} reclaim_need={reclaim_need:.8f}",
                            )
                            time.sleep(cfg.idleSleep)
                            continue
                    elif last_reason == "TRAIL":
                        trail_cd = float(getattr(cfg, "reentryTrailCooldownSec", 0.0) or 0.0)
                        if age_since_exit < trail_cd:
                            maybe_hold(
                                now,
                                'HOLD_REENTRY',
                                spread,
                                momPct,
                                momRangePct,
                                upRatio,
                                bid,
                                ask,
                                mid,
                                P1,
                                P2,
                                P3,
                                P4,
                                detail=f"last_reason={last_reason} age={age_since_exit:.1f}s cooldown={trail_cd:.1f}s",
                            )
                            time.sleep(cfg.idleSleep)
                            continue

                if (not burstOverride) and max_mom_pct > 0 and momPct > max_mom_pct:
                    maybe_hold(
                        now,
                        'HOLD_CHASE',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                if (not burstOverride) and required_range_pct > 0 and momRangePct < required_range_pct:
                    maybe_hold(
                        now,
                        'HOLD_RANGE',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                        detail=f"required_range={required_range_pct*100:.4f}%",
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                if (not burstOverride) and spread > spreadLimit:
                    maybe_hold(
                        now,
                        'HOLD_SPREAD',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                        detail=f"spread_limit={spreadLimit*100:.4f}%",
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                if not burstOverride:
                    s1, s5, market_ctx = load_signal_snapshot(now)
                    if s1 is None or s5 is None or market_ctx is None:
                        maybe_hold(
                            now,
                            'HOLD_SIGNAL',
                            spread,
                            momPct,
                            momRangePct,
                            upRatio,
                            bid,
                            ask,
                            mid,
                            P1,
                            P2,
                            P3,
                            P4,
                        )
                        time.sleep(cfg.idleSleep)
                        continue

                    rsi_now = float(getattr(s1, "rsi", 0.0))
                    ema1_ok = bool(getattr(s1, "ema_ok", False))
                    ema5_ok = bool(getattr(s5, "ema_ok", False))
                    vol_ok = bool(getattr(s1, "vol_ok", False))
                    signal_state = {
                        "rsi": rsi_now,
                        "ema1_ok": ema1_ok,
                        "ema5_ok": ema5_ok,
                        "vol_ok": vol_ok,
                    }

                    strong_trend_resume = (
                        ema1_ok
                        and momPct >= max(float(getattr(cfg, "momMinPct", 0.0) or 0.0) * 1.25, 0.0005)
                        and momRangePct >= max(required_range_pct * 1.15, 0.0008)
                        and upRatio >= max(float(getattr(cfg, "momMinUpRatio", 0.0) or 0.0), 0.45)
                    )
                    if not ema1_ok or (not ema5_ok and not strong_trend_resume):
                        maybe_hold(
                            now,
                            'HOLD_EMA',
                            spread,
                            momPct,
                            momRangePct,
                            upRatio,
                            bid,
                            ask,
                            mid,
                            P1,
                            P2,
                            P3,
                            P4,
                            detail=(
                                f"ema1={int(ema1_ok)} ema5={int(ema5_ok)} "
                                f"resume={int(strong_trend_resume)}"
                            ),
                            signal=signal_state,
                        )
                        time.sleep(cfg.idleSleep)
                        continue

                    rsi_min = float(getattr(profile, "rsiMin", 0.0) or 0.0)
                    rsi_max = min(
                        float(getattr(profile, "rsiMax", 100.0) or 100.0),
                        float(getattr(cfg, "rsiBuyMax", 100.0) or 100.0),
                    )
                    if not (rsi_min <= rsi_now <= rsi_max):
                        maybe_hold(
                            now,
                            'HOLD_RSI',
                            spread,
                            momPct,
                            momRangePct,
                            upRatio,
                            bid,
                            ask,
                            mid,
                            P1,
                            P2,
                            P3,
                            P4,
                            detail=f"rsi={rsi_now:.2f} range=[{rsi_min:.2f},{rsi_max:.2f}]",
                            signal=signal_state,
                        )
                        time.sleep(cfg.idleSleep)
                        continue

                    if not vol_ok:
                        maybe_hold(
                            now,
                            'HOLD_VOL',
                            spread,
                            momPct,
                            momRangePct,
                            upRatio,
                            bid,
                            ask,
                            mid,
                            P1,
                            P2,
                            P3,
                            P4,
                            signal=signal_state,
                        )
                        time.sleep(cfg.idleSleep)
                        continue

                # Refresh quote balance right before entry checks so a stale cached
                # wallet snapshot cannot incorrectly block a valid trade.
                live_usdc = get_usdc_balance_safe(bx, cfg)
                if live_usdc is not None:
                    usdc = live_usdc

                if usdc is None:
                    maybe_hold(
                        now,
                        'HOLD_BAL',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                if usdc < float(minNotional):
                    maybe_hold(
                        now,
                        'HOLD_MIN_NOTIONAL',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                        detail=f"usdc={usdc:.8f} min_notional={float(minNotional):.8f}",
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                fee_buf = float(getattr(cfg, "feeBufPct", 0.0) or 0.0)
                buy_safety_buf = max(fee_buf, 0.0025)
                reserve_target = float(usdc) * buy_safety_buf
                reserve_cap = max(0.0, float(usdc) - float(minNotional))
                quote_reserve = min(reserve_target, reserve_cap)
                spendable_usdc = max(0.0, float(usdc) - quote_reserve)
                spend = min(cap, spendable_usdc)

                if spend < float(minNotional):
                    maybe_hold(
                        now,
                        'HOLD_MIN_NOTIONAL',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                        detail=(
                            f"spend={spend:.8f} reserve={quote_reserve:.8f} "
                            f"min_notional={float(minNotional):.8f}"
                        ),
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                auto_cross_spread_pct = float(getattr(cfg, "entryAutoCrossSpreadPct", 0.0) or 0.0)
                cross_spread = bool(getattr(cfg, "entryCrossSpread", False))
                if burstOverride and bool(getattr(cfg, "burstForceCrossSpread", True)):
                    cross_spread = True
                if (not cross_spread) and auto_cross_spread_pct > 0 and float(spread) <= auto_cross_spread_pct:
                    cross_spread = True

                buyPx = compute_buy_price(
                    float(bid),
                    float(ask),
                    cross_spread,
                    tick,
                )
                max_quote_budget = min(float(cap), float(usdc))
                qty = round_step(spend / buyPx, step)
                notional = qty * buyPx
                min_qty = 0.0
                required_quote = 0.0
                if qty > 0 and notional < float(minNotional):
                    min_qty = round_step_up(float(minNotional) / buyPx, step)
                    required_quote = min_qty * buyPx
                    if min_qty > 0 and required_quote <= (max_quote_budget + 1e-9):
                        qty = min_qty
                        notional = required_quote

                if qty <= 0 or notional < float(minNotional):
                    missing_quote = max(0.0, required_quote - max_quote_budget) if required_quote > 0 else 0.0
                    maybe_hold(
                        now,
                        'HOLD_QTY',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        bid,
                        ask,
                        mid,
                        P1,
                        P2,
                        P3,
                        P4,
                        detail=(
                            f"qty={qty:.8f} notional={notional:.8f} "
                            f"buy_px={buyPx:.8f} min_notional={float(minNotional):.8f} "
                            f"budget={max_quote_budget:.8f} "
                            f"required_qty={min_qty:.8f} required_quote={required_quote:.8f} "
                            f"missing_quote={missing_quote:.8f}"
                        ),
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                try:
                    order = placeLimit(
                        bx, symbol, 'BUY',
                        qty, buyPx,
                        stepQ=step, tickQ=tick,
                        dryRun=getattr(cfg, 'dryRun', False)
                    )
                except Exception as e:
                    msg = str(e)
                    if "not permitted for this account" in msg.lower():
                        blockedSymbols.add(symbol)
                        persist_blocked_symbol(blocked_symbols_file, symbol)
                        try:
                            logTrade(f"BLOCK_SYMBOL symbol={symbol} reason=ACCOUNT_PERMISSION")
                            print("BLOCK_SYMBOL", symbol, "ACCOUNT_PERMISSION")
                        except Exception:
                            pass
                        cooldownUntil = time.time() + max(float(getattr(cfg, 'entryCooldownSec', 30.0)), 60.0)
                        time.sleep(cfg.idleSleep)
                        continue
                    raise

                filled, info = waitFillOrCancel(
                    bx, symbol, order["orderId"],
                    float(getattr(cfg, 'entryFillTtlSec', cfg.orderTtl)), cfg.orderPoll,
                    dryRun=getattr(cfg, "dryRun", False),
                    side="BUY",
                    qty=qty,
                    price=buyPx,
                    maxRestRetries=int(getattr(cfg, "orderRestMaxRetries", 3)),
                    restBackoffSec=float(getattr(cfg, "orderRestBackoffSec", 0.2)),
                )
                if not filled:
                    print('BUY_NOFILL')
                    logTrade('ENTRY_TIMEOUT_CANCEL')
                    cooldownUntil = time.time() + float(getattr(cfg, 'entryCooldownSec', 30.0))
                    time.sleep(cfg.idleSleep)
                    continue

                execQty = float(info.get("executedQty", qty))
                quoteQty = float(info.get("cummulativeQuoteQty", execQty * buyPx))
                entryPx = quoteQty / execQty if execQty > 0 else buyPx

                if pos is None:
                    pos = Position(qty=execQty, entry=entryPx, high=entryPx, stop=0.0, ts_entry=time.time())
                    pos.init_stops(cfg, profile, tick=tick)
                else:
                    # add to existing position (weighted average)
                    old_qty = float(getattr(pos, 'qty', 0.0))
                    old_entry = float(getattr(pos, 'entry', 0.0))
                    new_qty = old_qty + execQty
                    if new_qty > 0:
                        new_entry = ((old_qty * old_entry) + quoteQty) / new_qty
                        pos.qty = new_qty
                        pos.entry = new_entry
                        try:
                            pos.high = max(float(getattr(pos, 'high', new_entry)), float(bid))
                        except Exception:
                            pos.high = float(getattr(pos, 'high', new_entry))
                        pos.init_stops(cfg, profile, tick=tick)

                setattr(pos, "burstMode", bool(burstOverride))
                setattr(pos, "burstBaseReturnPct", float(burstStats.get("return_pct", 0.0) or 0.0))
                setattr(pos, "burstTriggerElapsedSec", float(burstStats.get("elapsed_sec", 0.0) or 0.0))
                setattr(pos, "burstEntryMode", entryMode)
                entry_market = market_ctx if market_ctx is not None else object()
                setattr(pos, "entryRet1m", float(getattr(entry_market, "ret_1m", 0.0)))
                setattr(pos, "entryRet3m", float(getattr(entry_market, "ret_3m", 0.0)))
                setattr(pos, "entryRet5m", float(getattr(entry_market, "ret_5m", 0.0)))
                setattr(pos, "entryRange5m", float(getattr(entry_market, "range_5m", 0.0)))

                sync_log_day_anchor(pos)
                if burstOverride:
                    entry_reason = (
                        f"BURST ret={burstStats['return_pct']*100:.4f}% "
                        f"vel={burstStats['velocity_pct_per_sec']*100:.4f}%/s "
                        f"eff={burstStats['efficiency']:.3f} "
                        f"pressure={burstStats['pressure_ratio']:.2f} "
                        f"drop={burstStats['max_single_drop_pct']*100:.4f}%"
                    )
                else:
                    entry_reason = f"PBUY P1={P1} P2={P2} P3={P3} P4={P4}"
                print("BUY_FILLED", getattr(pos, 'qty', ''), "@", fmt(getattr(pos, 'entry', 0.0)), "STOP", fmt(getattr(pos, 'stop', 0.0)))
                logTrade(
                    f"BUY symbol={symbol} qty={getattr(pos,'qty','')} entry={getattr(pos,'entry','')} "
                    f"mode={entryMode} reason={entry_reason} P1={P1} P2={P2} P3={P3} P4={P4}"
                )
                entry_vs_mid_pct = ((float(getattr(pos, 'entry', 0.0)) - float(mid)) / float(mid) * 100.0) if mid > 0 else ""
                logCsv({
                    "ts_utc": local_timestamp(),
                    "symbol": symbol,
                    "event": "BUY_FILLED",
                    "side": "BUY",
                    "qty": getattr(pos, 'qty', ''),
                    "price": getattr(pos, 'entry', ''),
                    "reason": entry_reason,
                    "pnl": "",
                    "profile": profile.name,
                    "dry_run": int(getattr(cfg, "dryRun", False)),
                    "mom_pct": float(momPct) * 100.0,
                    "mom_range_pct": float(momRangePct) * 100.0,
                    "up_ratio": float(upRatio) * 100.0,
                    "rsi": "" if rsi_now is None else rsi_now,
                    "ema1_ok": "" if ema1_ok is None else int(bool(ema1_ok)),
                    "ema5_ok": "" if ema5_ok is None else int(bool(ema5_ok)),
                    "vol_ok": "" if vol_ok is None else int(bool(vol_ok)),
                    "spread_pct": float(spread) * 100.0,
                    "bid": float(bid),
                    "ask": float(ask),
                    "mid": float(mid),
                    "entry_price": float(getattr(pos, 'entry', 0.0)),
                    "p1": "" if P1 is None else float(P1),
                    "p2": "" if P2 is None else float(P2),
                    "p3": "" if P3 is None else float(P3),
                    "p4": "" if P4 is None else float(P4),
                    "entry_vs_mid_pct": entry_vs_mid_pct,
                    "mid_vs_entry_pct": "",
                })
                continue
            
            if pos is None:
                time.sleep(cfg.idleSleep)
                continue

            # ===== EXIT =====
            position_market_ctx = signalCache.get("market")
            if pos.entry > 0:
                _, _, refreshed_market_ctx = load_signal_snapshot(time.time())
                if refreshed_market_ctx is not None:
                    position_market_ctx = refreshed_market_ctx

            # If position was adopted from wallet (entry=0), treat as untracked.
            # We do not fabricate an entry; we liquidate when sellable, otherwise we clear as dust.
            if pos.entry <= 0:
                exitReason = "WALLET_UNTRACKED"
            else:
                pos.update(bid, cfg, profile, tick=tick)
                exitReason = pos.exit_reason(bid, cfg, profile)

            if exitReason is None and pos is not None and pos.entry > 0:
                burstExit = burst_exit_reason(pos, bidTicks, bid, spread, cfg)
                if burstExit:
                    exitReason = burstExit

            # Additional SELL rules (P algo, MID-based)
            # Exit weak losers earlier when the tape is rolling over or stalling too long.
            if exitReason is None and (pos is not None) and (pos.entry > 0):
                sellSignal = False
                sellSignalMode = ""
                if has_new_tick and (P1 is not None) and (P2 is not None) and (P3 is not None):
                    age_sec = max(0.0, time.time() - float(getattr(pos, "ts_entry", time.time())))
                    min_signal_exit_sec = max(
                        float(getattr(cfg, "psellMinAgeSec", 25.0) or 25.0),
                        float(getattr(cfg, "entryFillTtlSec", 2.5)) * 4.0,
                    )
                    weak_tape = (momPct <= 0.0) or (upRatio < max(0.35, float(getattr(cfg, "momMinUpRatio", 0.0)) * 0.6))
                    min_loss_pct = max(
                        float(getattr(cfg, "psellMinLossPct", 0.0035) or 0.0035),
                        float(getattr(cfg, "feeBufPct", 0.0) or 0.0) * 1.25,
                    )
                    current_loss_pct = float(getattr(cfg, "psellCurrentLossPct", 0.0) or 0.0)
                    if current_loss_pct <= 0.0:
                        current_loss_pct = max(
                            float(getattr(cfg, "feeBufPct", 0.0) or 0.0) * 1.25,
                            min_loss_pct * 0.85,
                        )
                    stale_age_sec = float(getattr(cfg, "psellStaleAgeSec", 0.0) or 0.0)
                    if stale_age_sec <= 0.0:
                        stale_age_sec = max(
                            min_signal_exit_sec * 2.0,
                            min(120.0, float(getattr(cfg, "maxPosTime", 240.0) or 240.0) * 0.5),
                        )
                    stale_loss_pct = float(getattr(cfg, "psellStaleLossPct", 0.0) or 0.0)
                    if stale_loss_pct <= 0.0:
                        stale_loss_pct = max(
                            float(getattr(cfg, "feeBufPct", 0.0) or 0.0) * 1.25,
                            min_loss_pct * 0.75,
                        )
                    fail_age_sec = float(getattr(cfg, "psellFailAgeSec", 0.0) or 0.0)
                    if fail_age_sec <= 0.0:
                        fail_age_sec = max(
                            min_signal_exit_sec + 20.0,
                            min(60.0, float(getattr(cfg, "maxPosTime", 240.0) or 240.0) * 0.25),
                        )
                    fail_loss_pct = float(getattr(cfg, "psellFailLossPct", 0.0) or 0.0)
                    if fail_loss_pct <= 0.0:
                        fail_loss_pct = max(
                            float(getattr(cfg, "feeBufPct", 0.0) or 0.0) * 0.8,
                            stale_loss_pct * 0.8,
                        )
                    fail_max_high_pct = float(getattr(cfg, "psellFailMaxHighPct", 0.0) or 0.0)
                    if fail_max_high_pct <= 0.0:
                        fail_max_high_pct = max(
                            float(getattr(cfg, "feeBufPct", 0.0) or 0.0),
                            min(
                                max(
                                    float(getattr(cfg, "protectArmPct", 0.0) or 0.0) * 0.75,
                                    float(getattr(cfg, "armPct", 0.0) or 0.0) * 0.33,
                                ),
                                0.0020,
                            ),
                        )
                    current_guard = float(pos.entry) * (1.0 - current_loss_pct)
                    stale_guard = float(pos.entry) * (1.0 - stale_loss_pct)
                    fail_guard = float(pos.entry) * (1.0 - fail_loss_pct)
                    confirm_ticks = max(3, int(getattr(cfg, "psellConfirmTicks", 4) or 4))
                    descending_tape = (P1 < P2) and (P2 < P3)
                    if confirm_ticks >= 4 and (P4 is not None):
                        descending_tape = descending_tape and (P3 < P4)
                    latest_ref = min(float(bid), float(P1))
                    peak_progress_pct = max(
                        0.0,
                        (float(getattr(pos, "high", float(pos.entry))) - float(pos.entry)) / float(pos.entry),
                    )
                    fast_signal = (
                        age_sec >= min_signal_exit_sec
                        and descending_tape
                        and (latest_ref < current_guard)
                        and weak_tape
                    )
                    fail_signal = (
                        age_sec >= fail_age_sec
                        and (latest_ref < fail_guard)
                        and (peak_progress_pct <= fail_max_high_pct)
                        and weak_tape
                    )
                    stale_signal = (
                        age_sec >= stale_age_sec
                        and (latest_ref < stale_guard)
                        and (weak_tape or descending_tape)
                    )
                    five_min_signal = False
                    if position_market_ctx is not None:
                        entry_ret_5m = float(getattr(pos, "entryRet5m", 0.0) or 0.0)
                        current_ret_5m = float(getattr(position_market_ctx, "ret_5m", 0.0) or 0.0)
                        five_min_age_sec = float(getattr(cfg, "psell5mAgeSec", 0.0) or 0.0)
                        five_min_loss_pct = float(getattr(cfg, "psell5mLossPct", 0.0) or 0.0)
                        five_min_drop_pct = float(getattr(cfg, "psell5mDropPct", 0.0) or 0.0)
                        five_min_negative_ret_pct = float(getattr(cfg, "psell5mNegativeRetPct", 0.0) or 0.0)
                        five_min_guard = float(pos.entry) * (1.0 - five_min_loss_pct)
                        five_min_rolled_negative = (
                            entry_ret_5m > five_min_negative_ret_pct
                            and current_ret_5m <= five_min_negative_ret_pct
                        )
                        five_min_dropped = current_ret_5m <= (entry_ret_5m - five_min_drop_pct)
                        five_min_signal = (
                            five_min_age_sec > 0.0
                            and age_sec >= five_min_age_sec
                            and latest_ref < five_min_guard
                            and weak_tape
                            and (five_min_rolled_negative or five_min_dropped)
                        )
                    if fast_signal:
                        sellSignal = True
                        sellSignalMode = (
                            f"FAST age={age_sec:.1f}s "
                            f"latest={latest_ref:.8f} guard={current_guard:.8f}"
                        )
                    elif fail_signal:
                        sellSignal = True
                        sellSignalMode = (
                            f"FAIL age={age_sec:.1f}s "
                            f"latest={latest_ref:.8f} guard={fail_guard:.8f} "
                            f"high={float(getattr(pos, 'high', pos.entry)):.8f} "
                            f"peak={peak_progress_pct*100:.4f}%"
                        )
                    elif stale_signal:
                        sellSignal = True
                        sellSignalMode = (
                            f"STALE age={age_sec:.1f}s "
                            f"latest={latest_ref:.8f} guard={stale_guard:.8f}"
                        )
                    elif five_min_signal:
                        sellSignal = True
                        sellSignalMode = (
                            f"ROLL5 age={age_sec:.1f}s "
                            f"latest={latest_ref:.8f} "
                            f"ret5m={current_ret_5m*100:.4f}% "
                            f"entry5m={entry_ret_5m*100:.4f}% "
                            f"delta5m={(current_ret_5m-entry_ret_5m)*100:.4f}%"
                        )
                if sellSignal:
                    exitReason = (
                        f"PSELL {sellSignalMode} "
                        f"P1={P1} P2={P2} P3={P3} P4={P4} entry={pos.entry}"
                    )
            
            if exitReason is None:
                time.sleep(cfg.idleSleep)
                continue
            
            baseAsset = symbol.replace("USDC", "")
            freeBase = get_asset_balance_safe(bx, cfg, baseAsset)
            if freeBase is None:
                try:
                    logTrade("SELL_BAL_UNAVAILABLE")
                except Exception:
                    pass
                time.sleep(cfg.idleSleep)
                continue
            sellQty = round_step(freeBase, step)

            if sellQty <= 0 or (sellQty * bid) < float(minNotional):
                try:
                    print("DUST_SKIP_SELL", baseAsset, "qty", sellQty, "notional", sellQty * bid, "minNotional", minNotional, "step", step)
                    logTrade(f"DUST_SKIP_SELL asset={baseAsset} qty={sellQty} notional={sellQty*bid} minNotional={minNotional} step={step}")
                except Exception:
                    pass
                pos = None
                cooldownUntil = time.time() + float(getattr(cfg, "dustCooldownSec", 60))
                syncState["next"] = 0.0
                time.sleep(cfg.idleSleep)
                continue

            sellPx = bid
            sellPx = round_tick_down(sellPx, tick)
            try:
                print(
                    "SELL_TRIGGER",
                    "reason", exitReason,
                    "qty", fmt(sellQty, step),
                    "bid", fmt(bid, tick),
                    "px", fmt(sellPx, tick),
                    "entry", fmt(getattr(pos, "entry", 0.0), tick),
                    "high", fmt(getattr(pos, "high", 0.0), tick),
                    "stop", fmt(getattr(pos, "stop", 0.0), tick),
                )
            except Exception:
                pass

            try:
                order = placeLimit(
                    bx, symbol, "SELL",
                    sellQty, sellPx,
                    stepQ=step, tickQ=tick,
                    dryRun=getattr(cfg, "dryRun", False)
                )
            except Exception as e:
                msg = str(e)
                low = msg.lower()
                if ("MIN_NOTIONAL" in msg) or ("LOT_SIZE" in msg) or ("too small" in low) or ("insufficient" in low):
                    try:
                        notional = sellQty * bid
                        print("DUST_SKIP_SELL", baseAsset, "qty", sellQty, "notional", notional, "minNotional", minNotional, "step", step, "msg", msg)
                        logTrade(f"DUST_SKIP_SELL asset={baseAsset} qty={sellQty} notional={notional} minNotional={minNotional} step={step} msg={msg}")
                    except Exception:
                        pass
                    pos = None
                    cooldownUntil = time.time() + float(getattr(cfg, "dustCooldownSec", 60))
                    syncState["next"] = 0.0
                    time.sleep(cfg.idleSleep)
                    continue
                raise

            filled, info = waitFillOrCancel(
                bx, symbol, order["orderId"],
                cfg.orderTtl, cfg.orderPoll,
                dryRun=getattr(cfg, "dryRun", False),
                side="SELL",
                qty=sellQty,
                price=sellPx,
                maxRestRetries=int(getattr(cfg, "orderRestMaxRetries", 3)),
                restBackoffSec=float(getattr(cfg, "orderRestBackoffSec", 0.2)),
            )
            
            if not filled:
                print("SELL_NOFILL", exitReason)
                time.sleep(cfg.idleSleep)
                continue
            
            execQty = float(info.get("executedQty", sellQty))
            filledQty = execQty if execQty > 0 else sellQty
            quoteQty = float(info.get("cummulativeQuoteQty", filledQty * sellPx))
            exitPx = quoteQty / filledQty if filledQty > 0 else sellPx
            pnl = (exitPx - pos.entry) * filledQty
            mid_vs_entry_pct = ((float(mid) - float(pos.entry)) / float(pos.entry) * 100.0) if pos.entry > 0 else ""
            exit_s1, exit_s5, _ = load_signal_snapshot(time.time())
            exit_rsi = ""
            exit_ema1_ok = ""
            exit_ema5_ok = ""
            exit_vol_ok = ""
            if exit_s1 is not None and exit_s5 is not None:
                try:
                    exit_rsi = float(getattr(exit_s1, "rsi", 0.0))
                    exit_ema1_ok = int(bool(getattr(exit_s1, "ema_ok", False)))
                    exit_ema5_ok = int(bool(getattr(exit_s5, "ema_ok", False)))
                    exit_vol_ok = int(bool(getattr(exit_s1, "vol_ok", False)))
                except Exception:
                    exit_rsi = ""
                    exit_ema1_ok = ""
                    exit_ema5_ok = ""
                    exit_vol_ok = ""
            
            print("SELL_FILLED", filledQty, "@", fmt(exitPx), "PNL", fmt(pnl, Decimal('0.0001')), exitReason)
            logTrade(f"SELL symbol={symbol} qty={filledQty} exit={exitPx} pnl={pnl} reason={exitReason} profile={profile.name}")
            logCsv({
                "ts_utc": local_timestamp(),
                "symbol": symbol,
                "event": "SELL_FILLED",
                "side": "SELL",
                "qty": filledQty,
                "price": exitPx,
                "reason": exitReason,
                "pnl": pnl,
                "profile": profile.name,
                "dry_run": int(getattr(cfg, "dryRun", False)),
                "spread_pct": float(spread) * 100.0,
                "mom_pct": float(momPct) * 100.0,
                "mom_range_pct": float(momRangePct) * 100.0,
                "up_ratio": float(upRatio) * 100.0,
                "rsi": exit_rsi,
                "ema1_ok": exit_ema1_ok,
                "ema5_ok": exit_ema5_ok,
                "vol_ok": exit_vol_ok,
                "bid": float(bid),
                "ask": float(ask),
                "mid": float(mid),
                "entry_price": float(pos.entry),
                "p1": "" if P1 is None else float(P1),
                "p2": "" if P2 is None else float(P2),
                "p3": "" if P3 is None else float(P3),
                "p4": "" if P4 is None else float(P4),
                "entry_vs_mid_pct": "",
                "mid_vs_entry_pct": mid_vs_entry_pct,
            })

            lastExitInfo = {
                "symbol": symbol,
                "ts": time.time(),
                "reason": exitReason,
                "pnl": pnl,
                "entry": float(pos.entry),
                "exit": float(exitPx),
            }
            
            # cooldown and reset
            cooldownUntil = time.time() + (cfg.cooldownWin if pnl > 0 else cfg.cooldownLoss)
            pos = None
            # force immediate resync after sell
            syncState["next"] = 0.0
            
            
        except Exception as e:
            try:
                logErr("LOOP_EXCEPTION", e)
            except Exception:
                pass
            print("LOOP_EXCEPTION", type(e).__name__, str(e))
            time.sleep(1.0)
            continue
if __name__ == "__main__":
    main()
