#!/usr/bin/env python3
import sys
import time
from decimal import Decimal


def momentum_ok(ticks, window_sec: float, min_pct: float, min_up_ratio: float):
    """
    ticks: list[(ts, bid)]
    Retour: (ok, mom_pct, up_ratio)

    Tiered relaxation:
    - momentum fort => tolérance up_ratio plus basse
    """
    if not ticks:
        return False, 0.0, 0.0

    now = ticks[-1][0]
    cutoff = now - window_sec
    win = [(ts, bid) for ts, bid in ticks if ts >= cutoff]

    if len(win) < 2:
        return False, 0.0, 0.0

    p0 = win[0][1]
    p1 = win[-1][1]
    if p0 <= 0 or p1 <= 0:
        return False, 0.0, 0.0

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

    relaxed = min_up_ratio
    if mom >= min_pct * 4:
        relaxed *= 0.6
    elif mom >= min_pct * 2:
        relaxed *= 0.8

    ok = (mom >= min_pct) and (up_ratio >= relaxed)
    return ok, mom, up_ratio


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


def get_usdc_balance_safe(bx: Binance) -> float:
    # Evite de crasher le bot sur un 401 ponctuel.
    try:
        balances = bx.get("/api/v3/account", signed=True)["balances"]
    except Exception:
        return 0.0
    for b in balances:
        if b.get("asset") == "USDC":
            return float(b.get("free", "0"))
    return 0.0


def main():
    if len(sys.argv) < 2:
        print("USAGE: python3 main.py SYMBOLUSDC")
        sys.exit(1)

    symbol = sys.argv[1].strip().upper()

    cfg = loadConfig()
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

    logTrade(f"SESSION_START symbol={symbol} dry_run={int(bool(getattr(cfg,'dryRun',False)))} profile={profile.name} strategy={getattr(cfg,'strategyName','')} base_url={cfg.baseUrl}")

    print(f"INIT {symbol} tick={tick} step={step} minNotional~{minNotional} cap={cap}USDC")
    print(
        f"PROFILE {profile.name} "
        f"EMA={profile.emaFast}/{profile.emaSlow} "
        f"RSI=[{profile.rsiMin},{profile.rsiMax}] "
        f"VOL_MULT={profile.volMult} "
        f"SPREAD_MAX={profile.spreadMax}"
    )
    print(f"MOM window={momWindowSec}s minPct={momMinPct} minUpRatio={momMinUpRatio}")
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

    def maybe_hold(now, reason, spread, momPct, upRatio, rsi, ema1_ok, ema5_ok, vol_ok):
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
                f"up={upRatio*100:.2f}% rsi={float(rsi) if rsi is not None else 'NA'}"
            )
        except Exception:
            pass

    while True:
        try:
            now = time.time()
            
            bid, ask = stream.bestBidAsk()
            if bid <= 0 or ask <= 0:
                time.sleep(cfg.idleSleep)
                continue
            
            # update ticks buffer
            ticks.append((now, float(bid)))
            # prune: keep last max(window, 40s)
            keep_sec = max(momWindowSec, 40.0)
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
                intervalSec=5
            )
            
            # compute signals (kept as filter, not the trigger)
            try:
                s1, s5 = computeSignals(bx, symbol, profile)
            except Exception:
                time.sleep(cfg.idleSleep)
                continue
            
            spread = (ask - bid) / bid if bid > 0 else 1.0
            
            momOk, momPct, upRatio = momentum_ok(ticks, momWindowSec, momMinPct, momMinUpRatio)
            ind = strat.compute(s1, s5, momOk, momPct, upRatio, spread)
            
            if now - lastChk >= cfg.chkEvery:
                lastChk = now
                chk_msg = (
                    f"CHK EMA1m:{'OK' if s1.ema_ok else 'NO'} EMA5m:{'OK' if s5.ema_ok else 'NO'} "
                    f"RSI:{fmt(s1.rsi)} VOL:{'OK' if s1.vol_ok else 'NO'} "
                    f"MOM:{fmt(momPct*100, Decimal('0.01'))}% UP:{fmt(upRatio*100, Decimal('0.01'))}% "
                    f"SPREAD:{fmt(spread*100)}% BID:{fmt(bid)} ASK:{fmt(ask)} "
                    f"STATE:{'IN_POS' if pos else 'IDLE'}"
                )
                print(chk_msg)
                logTrade(chk_msg)
            
            # ===== ENTRY =====
            if pos is None:
                if spread > profile.spreadMax:
                    maybe_hold(now, 'HOLD_SPREAD', spread, momPct, upRatio, s1.rsi, s1.ema_ok, s5.ema_ok, s1.vol_ok)
                    time.sleep(cfg.idleSleep)
                    continue
            
                # primary trigger
                if not momOk:
                    maybe_hold(now, 'HOLD_MOM', spread, momPct, upRatio, s1.rsi, s1.ema_ok, s5.ema_ok, s1.vol_ok)
                    time.sleep(cfg.idleSleep)
                    continue
                # secondary filters (EMA kept for logs only)
                if not (profile.rsiMin <= s1.rsi <= profile.rsiMax):
                    maybe_hold(now, 'HOLD_RSI', spread, momPct, upRatio, s1.rsi, s1.ema_ok, s5.ema_ok, s1.vol_ok)
                    time.sleep(cfg.idleSleep)
                    continue
            
                usdc = get_usdc_balance_safe(bx)
                if usdc < minNotional:
                    maybe_hold(now, 'HOLD_BAL', spread, momPct, upRatio, s1.rsi, s1.ema_ok, s5.ema_ok, s1.vol_ok)
                    time.sleep(cfg.idleSleep)
                    continue
            
                spend = min(cap, usdc)
            
                # BUY marketable LIMIT at ASK (fill)
                buyPx = ask
            
                qty = round_step(spend / buyPx, step)
                if qty <= 0 or (qty * buyPx) < minNotional:
                    maybe_hold(now, 'HOLD_QTY', spread, momPct, upRatio, s1.rsi, s1.ema_ok, s5.ema_ok, s1.vol_ok)
                    time.sleep(cfg.idleSleep)
                    continue
            
                order = placeLimit(
                    bx, symbol, "BUY",
                    qty, buyPx,
                    stepQ=step, tickQ=tick
                ,
                        dryRun=getattr(cfg, "dryRun", False)
                    )
            
                filled, info = waitFillOrCancel(
                    bx, symbol, order["orderId"],
                    cfg.orderTtl, cfg.orderPoll,
                    dryRun=getattr(cfg, "dryRun", False),
                    side="BUY",
                    qty=qty,
                    price=buyPx
                )
            
                if not filled:
                    print("BUY_NOFILL")
                    time.sleep(cfg.idleSleep)
                    continue
            
                execQty = float(info.get("executedQty", qty))
                quoteQty = float(info.get("cummulativeQuoteQty", execQty * buyPx))
                entryPx = quoteQty / execQty if execQty > 0 else buyPx
            
                pos = Position(qty=execQty, entry=entryPx, high=entryPx, stop=0.0, ts_entry=time.time())
                pos.init_stops(cfg, profile, tick=tick)
            
                print("BUY_FILLED", pos.qty, "@", fmt(pos.entry), "STOP", fmt(pos.stop))
                logTrade(f"BUY symbol={symbol} qty={pos.qty} entry={pos.entry} momPct={momPct} upRatio={upRatio} profile={profile.name}")
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
                })
                continue
            
            # ===== EXIT =====
            # normalize placeholder positions (wallet sync entry=0)
            if pos.entry <= 0:
                pos.entry = bid
                pos.high = max(pos.high, bid)
                pos.ts_entry = time.time()
                pos.init_stops(cfg, profile, tick=tick)
            
            pos.update(bid, cfg, profile, tick=tick)
            
            exitReason = pos.exit_reason(bid, cfg, profile)
            
            if exitReason is None:
                time.sleep(cfg.idleSleep)
                continue
            
            sellQty = round_step(pos.qty, step)
            if sellQty <= 0:
                print("SELL_SKIP_QTY_TOO_SMALL")
                pos = None
                time.sleep(cfg.idleSleep)
                continue
            
            # SELL marketable LIMIT at BID (fill)
            sellPx = bid
            
            order = placeLimit(
                bx, symbol, "SELL",
                sellQty, sellPx,
                stepQ=step, tickQ=tick
            ,
                        dryRun=getattr(cfg, "dryRun", False)
                    )
            
            filled, info = waitFillOrCancel(
                bx, symbol, order["orderId"],
                cfg.orderTtl, cfg.orderPoll,
                dryRun=getattr(cfg, "dryRun", False),
                side="SELL",
                qty=sellQty,
                price=sellPx
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
