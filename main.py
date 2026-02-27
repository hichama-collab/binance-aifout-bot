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
from core.logging import tradeLogger, tradeCsvLogger, errorLogger, ensureCsvHeader
from services.ipguard import vpnCheckOrDie

from exchange.binance import Binance
from exchange.stream import Stream

from execution.orders import placeLimit, waitFillOrCancel
from state.wallet_sync import walletSyncEvery
from state.position import Position

from indicators.basic import computeSignals, fmt
from strategy.factory import getStrategy


def round_step(qty: float, step: float) -> float:
    dqty = Decimal(str(qty))
    dstep = Decimal(str(step))
    return float((dqty // dstep) * dstep)



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
def compute_buy_price(best_ask: float, _range_pct: float) -> float:
    """Return a BUY price at the current ask (LIMIT), so the order can fill immediately.
    (Still LIMIT; we just cross the spread.)
    """
    return best_ask


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
        msg = f"TOKEN_SWITCH old={current_symbol} new={new_symbol} source=.service.env"
        print(msg)
        try:
            logTrade(msg)
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), new_symbol])
    except SystemExit:
        raise
    except Exception:
        return current_symbol, last_env_mtime
    return current_symbol, last_env_mtime

def main():
    if len(sys.argv) < 2:
        print("USAGE: python3 main.py SYMBOLUSDC")
        sys.exit(1)

    symbol = sys.argv[1].strip().upper()
    last_env_mtime = 0.0

    cfg = loadConfig()
    poll = float(getattr(cfg, 'idleSleep', getattr(cfg, 'poll', 0.2)))  # legacy alias
    cfg = applyRiskConfig(cfg)
    profile = pickProfile()
    strat = getStrategy(cfg, profile)

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
    logTrade = tradeLogger(cfg, symbol)
    logCsv = tradeCsvLogger(cfg, symbol)
    logErr = errorLogger(cfg, symbol)
    ensureCsvHeader(cfg, symbol)

    try:


        tick, step, minNotional = get_symbol_filters(bx, symbol)


    except RuntimeError as e:


        print(str(e))


        sys.exit(1)

    cap = float(getattr(cfg, "maxUsdcPerTrade", 50.0))

    # Momentum params (defaults: scalping spot)
    momWindowSec = float(getattr(cfg, "momWindowSec", 30.0))
    momMinPct = float(getattr(cfg, "momMinPct", 0.0005))          # 0.05%
    momMinUpRatio = float(getattr(cfg, "momMinUpRatio", 0.55))    # 55% ticks up
    momRangeMinPct = float(getattr(cfg, "momRangeMinPct", 0.003))
    momRangeRelaxPct = float(getattr(cfg, "momRangeRelaxPct", 0.6))
    momRangeRelaxUpRatio = float(getattr(cfg, "momRangeRelaxUpRatio", 0.75))

    logTrade(f"SESSION_START symbol={symbol} dry_run={int(bool(getattr(cfg,'dryRun',False)))} profile={profile.name} strategy={getattr(cfg,'strategyName','')} base_url={cfg.baseUrl}")

    print(f"INIT {symbol} tick={tick} step={step} minNotional~{minNotional} cap={cap}USDC")
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

    # ring buffer of (ts, bid)
    ticks = []

    stream.start()
    lastChk = 0.0

    lastHoldCsv = 0.0
    holdCsvEvery = float(getattr(cfg, 'holdCsvEvery', 60))

    def maybe_hold(
        now,
        reason,
        spread,
        momPct,
        momRangePct,
        upRatio,
        rsi,
        ema1_ok,
        ema5_ok,
        vol_ok,
    ):
        nonlocal lastHoldCsv
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
                'rsi': float(rsi) if rsi is not None else '',
                'ema1_ok': int(bool(ema1_ok)),
                'ema5_ok': int(bool(ema5_ok)),
                'vol_ok': int(bool(vol_ok)),
            })
        except Exception:
            pass
        try:
            logTrade(
                f"DECIDE_HOLD reason={reason} spread={spread*100:.4f}% mom={momPct*100:.4f}% "
                f"range={momRangePct*100:.4f}% up={upRatio*100:.2f}% "
                f"rsi={float(rsi) if rsi is not None else 'NA'}"
            )
            print(
                f"DECIDE_HOLD reason={reason} spread={spread*100:.4f}% mom={momPct*100:.4f}% "
                f"range={momRangePct*100:.4f}% up={upRatio*100:.2f}% "
                f"rsi={float(rsi) if rsi is not None else 'NA'}"
            )
        except Exception:
            pass

    while True:
        free_usdc = 0.0
        try:
            now = time.time()
            
            bid, ask = stream.bestBidAsk()
            if bid <= 0 or ask <= 0:
                time.sleep(cfg.idleSleep)
                continue
            
            # update ticks buffer
            ticks.append((now, float(bid)))
            # prune: keep last max(window, 40s)
            keep_sec = max(momWindowSec, float(getattr(cfg, "ticksKeepMinSec", 40.0)))
            cutoff = now - keep_sec
            while ticks and ticks[0][0] < cutoff:
                ticks.pop(0)
            
            # cooldown log so it doesn't look frozen
            if now < cooldownUntil:
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

            
            # compute signals (kept as filter, not the trigger)
            try:
                s1, s5 = computeSignals(bx, symbol, profile)
            except Exception:
                time.sleep(cfg.idleSleep)
                continue
            
            spread = (ask - bid) / bid if bid > 0 else 1.0
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

            p1p4Ok = p1p4_ok(ticks, lookback=4)
            ind = strat.compute(s1, s5, momOk, momPct, upRatio, spread, p1p4Ok)
            
            if now - lastChk >= cfg.chkEvery:
                lastChk = now
                chk_msg = (
                    f"CHK EMA1m:{'OK' if s1.ema_ok else 'NO'} EMA5m:{'OK' if s5.ema_ok else 'NO'} "
                    f"RSI:{fmt(s1.rsi)} VOL:{'OK' if s1.vol_ok else 'NO'} "
                    f"MOM:{fmt(momPct*100, Decimal('0.01'))}% "
                    f"RANGE:{fmt(momRangePct*100, Decimal('0.01'))}% "
                    f"UP:{fmt(upRatio*100, Decimal('0.01'))}% "
                    f"SPREAD:{fmt(spread*100)}% BID:{fmt(bid)} ASK:{fmt(ask)} "
                    f"STATE:{'IN_POS' if pos else 'IDLE'}"
                )
                print(chk_msg)
                logTrade(chk_msg)
            
            # hot-reload token from .service.env (IDLE only)
            symbol, last_env_mtime = _maybe_reexec_on_token_change(symbol, pos, last_env_mtime)

            # ===== ENTRY =====
            if pos is None:
                # tick entry (micro): computed early for spread relaxation + primary trigger
                tick_entry_ok, tick_entry_prog = tick_confirmation_ok(
                    ticks,
                    int(getattr(cfg, 'tickEntryLookback', getattr(cfg, 'tickConfirmationLookback', 3))),
                    float(getattr(cfg, 'tickEntryMinPct', getattr(cfg, 'tickConfirmationMinPct', 0.0002))),
                )

                spreadMax = profile.spreadMax
                if tick_entry_ok:
                    spreadMax = spreadMax * float(getattr(cfg, 'spreadRelaxOnTick', 1.5))
                if spread > spreadMax:
                    maybe_hold(
                        now,
                        'HOLD_SPREAD',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                # anti-range gate: avoid chop (range too small vs fees/spread)
                min_range_entry = float(getattr(cfg, 'minRangeEntryPct', 0.0))
                min_range_vs_spread = float(getattr(cfg, 'minRangeVsSpread', 0.0))
                if min_range_entry > 0 and momRangePct < min_range_entry:
                    maybe_hold(
                        now,
                        'HOLD_RANGE_CHOP',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue
                if min_range_vs_spread > 0 and spread > 0 and (momRangePct / spread) < min_range_vs_spread:
                    maybe_hold(
                        now,
                        'HOLD_RANGE_SPREAD',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                # microstructure gate: orderbook imbalance (bid_vol/ask_vol)
                ob_ok, ob_ratio = orderbook_imbalance_ok(
                    bx, symbol,
                    float(getattr(cfg, 'obImbalanceMinRatio', 0.0)),
                    int(getattr(cfg, 'obDepthLevels', 5)),
                )
                if float(getattr(cfg, 'obImbalanceMinRatio', 0.0)) > 0 and not ob_ok:
                    maybe_hold(
                        now,
                        'HOLD_IMBALANCE',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                # microstructure gate: stale ticks (only when instant mode is on)
                if bool(getattr(cfg, 'momUseInstant', False)) and not ticks_fresh(
                    ticks, float(getattr(cfg, 'momMaxAgeSec', 2.0))
                ):
                    maybe_hold(
                        now,
                        'HOLD_STALE',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                # primary trigger
                # primary trigger: allow MOM or fast tick-entry (avoid late entries)
                if not (momOk or tick_entry_ok):
                    maybe_hold(
                        now,
                        'HOLD_MOM',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue
                # secondary filters (EMA kept for logs only)
                # secondary filters (EMA kept for logs only)
                if not (profile.rsiMin <= s1.rsi <= profile.rsiMax):
                    maybe_hold(
                        now,
                        'HOLD_RSI',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                # tick confirmation (micro)
                if bool(getattr(cfg, "tickConfirmationEnabled", False)):
                    tick_ok, tick_prog = tick_confirmation_ok(
                        ticks,
                        int(getattr(cfg, "tickConfirmationLookback", 3)),
                        float(getattr(cfg, "tickConfirmationMinPct", 0.0005)),
                    )
                    if not tick_ok:
                        maybe_hold(
                            now,
                            "HOLD_TICK_CONF",
                            spread,
                            momPct,
                            momRangePct,
                            upRatio,
                            s1.rsi,
                            s1.ema_ok,
                            s5.ema_ok,
                            s1.vol_ok,
                        )
                        time.sleep(cfg.idleSleep)
                        continue
                if usdc is None:
                    maybe_hold(
                        now,
                        'HOLD_BAL',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue
            
                if usdc < minNotional:
                    maybe_hold(
                        now,
                        'HOLD_MIN_NOTIONAL',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                spend = min(cap, usdc)
            
                # BUY LIMIT (dynamic offset in compression; no offset in expansion)
                if bool(getattr(cfg, 'entryCrossSpread', False)):
                    buyPx = ask
                else:
                    buyPx = ask
                # align BUY to tick (ceil) so it is never below ask
                buyPx = round_tick_up(buyPx, tick)
            
                qty = round_step(spend / buyPx, step)
                if qty <= 0:
                    maybe_hold(
                        now,
                        'HOLD_QTY',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue
                if (qty * buyPx) < minNotional:
                    maybe_hold(
                        now,
                        'HOLD_QTY',
                        spread,
                        momPct,
                        momRangePct,
                        upRatio,
                        s1.rsi,
                        s1.ema_ok,
                        s5.ema_ok,
                        s1.vol_ok,
                    )
                    time.sleep(cfg.idleSleep)
                    continue
            
                order = placeLimit(
                    bx, symbol, 'BUY',
                    qty, buyPx,
                    stepQ=step, tickQ=tick,
                    dryRun=getattr(cfg, 'dryRun', False)
                )
            
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
            
                pos = Position(qty=execQty, entry=entryPx, high=entryPx, stop=0.0, ts_entry=time.time())
                pos.init_stops(cfg, profile, tick=tick)
            
                print("BUY_FILLED", pos.qty, "@", fmt(pos.entry), "STOP", fmt(pos.stop))
                logTrade(
                    f"BUY symbol={symbol} qty={pos.qty} entry={pos.entry} momPct={momPct} "
                    f"rangePct={momRangePct} upRatio={upRatio} profile={profile.name}"
                )
                logCsv({
                    "ts_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                    "symbol": symbol,
                    "event": "BUY_FILLED",
                    "side": "BUY",
                    "qty": pos.qty,
                    "price": pos.entry,
                    "reason": "MOM",
                    "pnl": "",
                    "profile": profile.name,
                    "dry_run": int(getattr(cfg, "dryRun", False)),
                    "mom_pct": float(momPct) * 100.0,
                    "mom_range_pct": float(momRangePct) * 100.0,
                    "up_ratio": float(upRatio) * 100.0,
                    "rsi": float(s1.rsi),
                    "ema1_ok": int(bool(s1.ema_ok)),
                    "ema5_ok": int(bool(s5.ema_ok)),
                    "vol_ok": int(bool(s1.vol_ok)),
                    "spread_pct": float(spread) * 100.0,
                })
                continue
            
            # ===== EXIT =====
            # If position was adopted from wallet (entry=0), treat as untracked.
            # We do not fabricate an entry; we liquidate when sellable, otherwise we clear as dust.
            if pos.entry <= 0:
                exitReason = "WALLET_UNTRACKED"
            else:
                pos.update(bid, cfg, profile, tick=tick)
                exitReason = pos.exit_reason(bid, cfg, profile)
            
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
            quoteQty = float(info.get("cummulativeQuoteQty", execQty * sellPx))
            exitPx = quoteQty / execQty if execQty > 0 else sellPx
            pnl = (exitPx - pos.entry) * sellQty
            
            print("SELL_FILLED", sellQty, "@", fmt(exitPx), "PNL", fmt(pnl, Decimal('0.0001')), exitReason)
            logTrade(f"SELL symbol={symbol} qty={sellQty} exit={exitPx} pnl={pnl} reason={exitReason} profile={profile.name}")
            logCsv({
                "ts_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                "symbol": symbol,
                "event": "SELL_FILLED",
                "side": "SELL",
                "qty": sellQty,
                "price": exitPx,
                "reason": exitReason,
                "pnl": pnl,
                "profile": profile.name,
                "dry_run": int(getattr(cfg, "dryRun", False)),
            })
            
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