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
from core.logging import tradeLogger, tradeCsvLogger, errorLogger, ensureCsvHeader, local_timestamp
from services.ipguard import vpnCheckOrDie

from exchange.binance import Binance
from exchange.stream import Stream

from execution.orders import placeLimit, waitFillOrCancel
from state.wallet_sync import walletSyncEvery
from state.position import Position

from indicators.basic import fmt


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
    pendingSwitchSymbol = None
    pendingSwitchLogged = None

    # ring buffer of (ts, price_ref). We store MID=(bestBid+bestAsk)/2 for P1..P4 logic.
    ticks = []

    stream.start()
    lastChk = 0.0
    lastTickSeq = 0

    lastHoldCsv = 0.0
    holdCsvEvery = float(getattr(cfg, 'holdCsvEvery', 60))

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
                'rsi': '',
                'ema1_ok': '',
                'ema5_ok': '',
                'vol_ok': '',
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

            requested_symbol, observed_env_mtime = _pending_token_switch(symbol, last_env_mtime)
            if requested_symbol:
                pendingSwitchSymbol = requested_symbol
                last_env_mtime = observed_env_mtime
            
            bid, ask, tick_ts, tick_seq = stream.snapshot()
            if bid <= 0 or ask <= 0:
                time.sleep(cfg.idleSleep)
                continue

            mid = (float(bid) + float(ask)) / 2.0
            has_new_tick = tick_seq != lastTickSeq
            if has_new_tick:
                lastTickSeq = tick_seq
                ticks.append((float(tick_ts or now), float(mid)))
                # prune: keep last max(window, 40s)
                keep_sec = max(momWindowSec, float(getattr(cfg, "ticksKeepMinSec", 40.0)))
                cutoff = now - keep_sec
                while ticks and ticks[0][0] < cutoff:
                    ticks.pop(0)
            
            # cooldown log so it doesn't look frozen
            if now < cooldownUntil:
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
            
            # hot-reload token from .service.env (IDLE only)
            symbol, last_env_mtime = _maybe_reexec_on_token_change(symbol, pos, last_env_mtime)
            if pos is None and pendingSwitchSymbol == symbol:
                pendingSwitchSymbol = None
                pendingSwitchLogged = None

            if pendingSwitchSymbol and pendingSwitchLogged != pendingSwitchSymbol:
                msg = f"TOKEN_SWITCH_PENDING old={symbol} new={pendingSwitchSymbol} state={'IN_POS' if pos else 'IDLE'}"
                print(msg)
                logTrade(msg)
                pendingSwitchLogged = pendingSwitchSymbol

            # ===== ENTRY (P algo, MID-based) =====
# BUY if (P1 >= P2 >= P3 >= P4)
            buySignal = False
            if (
                pos is None
                and not pendingSwitchSymbol
                and has_new_tick
                and (P1 is not None)
                and (P2 is not None)
                and (P3 is not None)
                and (P4 is not None)
            ):
                buySignal = (P1 >= P2) and (P2 >= P3) and (P3 >= P4)

            if buySignal:
                max_mom_pct = float(getattr(cfg, "momMaxPct", 1.0) or 1.0)
                min_range_entry_pct = float(getattr(cfg, "minRangeEntryPct", 0.0) or 0.0)
                min_range_vs_spread = float(getattr(cfg, "minRangeVsSpread", 0.0) or 0.0)
                required_range_pct = max(min_range_entry_pct, float(spread) * min_range_vs_spread)

                # Momentum gate intentionally disabled here.
                # The P1 >= P2 >= P3 >= P4 check already validates the same
                # short-term directional move and is enough as an anti-noise
                # filter for entry. Keeping momOk on top of P1..P4 was
                # redundant and blocked too many otherwise valid setups.

                if max_mom_pct > 0 and momPct > max_mom_pct:
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

                if required_range_pct > 0 and momRangePct < required_range_pct:
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

                if spread > spreadLimit:
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
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                # Refresh quote balance right before placing a BUY and keep a small
                # safety margin to avoid Binance "insufficient balance" rejects.
                live_usdc = get_usdc_balance_safe(bx, cfg)
                if live_usdc is not None:
                    usdc = live_usdc

                fee_buf = float(getattr(cfg, "feeBufPct", 0.0) or 0.0)
                buy_safety_buf = max(fee_buf, 0.0025)
                spendable_usdc = max(0.0, float(usdc) * (1.0 - buy_safety_buf))
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
                    )
                    time.sleep(cfg.idleSleep)
                    continue

                # BUY LIMIT at ASK (rounded up to tick)
                buyPx = round_tick_up(float(ask), tick)
                qty = round_step(spend / buyPx, step)
                if qty <= 0 or (qty * buyPx) < float(minNotional):
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

                print("BUY_FILLED", getattr(pos, 'qty', ''), "@", fmt(getattr(pos, 'entry', 0.0)), "STOP", fmt(getattr(pos, 'stop', 0.0)))
                logTrade(f"BUY symbol={symbol} qty={getattr(pos,'qty','')} entry={getattr(pos,'entry','')} P1={P1} P2={P2} P3={P3} P4={P4}")
                entry_vs_mid_pct = ((float(getattr(pos, 'entry', 0.0)) - float(mid)) / float(mid) * 100.0) if mid > 0 else ""
                logCsv({
                    "ts_utc": local_timestamp(),
                    "symbol": symbol,
                    "event": "BUY_FILLED",
                    "side": "BUY",
                    "qty": getattr(pos, 'qty', ''),
                    "price": getattr(pos, 'entry', ''),
                    "reason": f"PBUY P1={P1} P2={P2} P3={P3} P4={P4}",
                    "pnl": "",
                    "profile": profile.name,
                    "dry_run": int(getattr(cfg, "dryRun", False)),
                    "mom_pct": float(momPct) * 100.0,
                    "mom_range_pct": float(momRangePct) * 100.0,
                    "up_ratio": float(upRatio) * 100.0,
                    "rsi": "",
                    "ema1_ok": "",
                    "ema5_ok": "",
                    "vol_ok": "",
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
            # If position was adopted from wallet (entry=0), treat as untracked.
            # We do not fabricate an entry; we liquidate when sellable, otherwise we clear as dust.
            if pendingSwitchSymbol:
                exitReason = f"TOKEN_SWITCH new={pendingSwitchSymbol}"
            elif pos.entry <= 0:
                exitReason = "WALLET_UNTRACKED"
            else:
                pos.update(bid, cfg, profile, tick=tick)
                exitReason = pos.exit_reason(bid, cfg, profile)

            # Additional SELL rules (P algo, MID-based)
            # SELL if (P1 < P3) AND (P3 < entryPrice)
            if exitReason is None and (pos is not None) and (pos.entry > 0):
                sellSignal = False
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
                    below_entry_guard = float(pos.entry) * (1.0 - min_loss_pct)
                    confirm_ticks = max(3, int(getattr(cfg, "psellConfirmTicks", 4) or 4))
                    descending_tape = (P1 < P2) and (P2 < P3)
                    if confirm_ticks >= 4 and (P4 is not None):
                        descending_tape = descending_tape and (P3 < P4)
                    sellSignal = (
                        age_sec >= min_signal_exit_sec
                        and descending_tape
                        and (P3 < below_entry_guard)
                        and weak_tape
                    )
                if sellSignal:
                    exitReason = f"PSELL P1={P1} P2={P2} P3={P3} P4={P4} entry={pos.entry}"
            
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
            quoteQty = float(info.get("cummulativeQuoteQty", execQty * sellPx))
            exitPx = quoteQty / execQty if execQty > 0 else sellPx
            pnl = (exitPx - pos.entry) * sellQty
            mid_vs_entry_pct = ((float(mid) - float(pos.entry)) / float(pos.entry) * 100.0) if pos.entry > 0 else ""
            
            print("SELL_FILLED", sellQty, "@", fmt(exitPx), "PNL", fmt(pnl, Decimal('0.0001')), exitReason)
            logTrade(f"SELL symbol={symbol} qty={sellQty} exit={exitPx} pnl={pnl} reason={exitReason} profile={profile.name}")
            logCsv({
                "ts_utc": local_timestamp(),
                "symbol": symbol,
                "event": "SELL_FILLED",
                "side": "SELL",
                "qty": sellQty,
                "price": exitPx,
                "reason": exitReason,
                "pnl": pnl,
                "profile": profile.name,
                "dry_run": int(getattr(cfg, "dryRun", False)),
                "spread_pct": float(spread) * 100.0,
                "mom_pct": float(momPct) * 100.0,
                "mom_range_pct": float(momRangePct) * 100.0,
                "up_ratio": float(upRatio) * 100.0,
                "rsi": "",
                "ema1_ok": "",
                "ema5_ok": "",
                "vol_ok": "",
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
