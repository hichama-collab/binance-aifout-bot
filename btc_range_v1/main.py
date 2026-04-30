#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from btc_range_v1.config import loadConfig
from btc_range_v1.logic import PositionState, build_range_snapshot, entry_signal, update_position
from core.logging import LogDayContext, ensureCsvHeader, errorLogger, local_timestamp, tradeCsvLogger, tradeLogger
from exchange.binance import Binance
from exchange.stream import Stream
from execution.orders import placeLimit, waitFillOrCancel
from indicators.basic import fmt
from services.ipguard import vpnCheckOrDie


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
    if tick <= 0:
        return float(price)
    dprice = Decimal(str(price))
    dtick = Decimal(str(tick))
    return float((dprice // dtick) * dtick)


def round_tick_up(price: float, tick: float) -> float:
    if tick <= 0:
        return float(price)
    dprice = Decimal(str(price))
    dtick = Decimal(str(tick))
    q = (dprice / dtick).to_integral_value(rounding=ROUND_CEILING)
    return float(q * dtick)


def get_symbol_filters(bx: Binance, symbol: str):
    exch = bx.get("/api/v3/exchangeInfo", {"symbol": symbol})
    sym = exch["symbols"][0]
    filters = {f["filterType"]: f for f in sym["filters"]}

    tick = float(filters["PRICE_FILTER"]["tickSize"])
    step = float(filters["LOT_SIZE"]["stepSize"])

    if "MIN_NOTIONAL" in filters:
        min_notional = float(filters["MIN_NOTIONAL"]["minNotional"])
    elif "NOTIONAL" in filters:
        min_notional = float(filters["NOTIONAL"]["minNotional"])
    else:
        min_notional = 0.0

    return tick, step, min_notional


def get_account_with_retry(bx: Binance, cfg):
    retries = int(getattr(cfg, "httpRetries", 0) or 0)
    backoff = float(getattr(cfg, "httpBackoff", 0.0) or 0.0)
    for attempt in range(retries + 1):
        try:
            return bx.get("/api/v3/account", signed=True)
        except Exception:
            if attempt >= retries:
                return None
            time.sleep(backoff)
    return None


def get_asset_balance_safe(bx: Binance, cfg, asset: str):
    acc = get_account_with_retry(bx, cfg)
    if not acc:
        return None
    for row in acc.get("balances", []):
        if row.get("asset") == asset:
            return float(row.get("free", "0"))
    return 0.0


def get_usdc_balance_safe(bx: Binance, cfg):
    return get_asset_balance_safe(bx, cfg, "USDC")


def fetch_klines(bx: Binance, symbol: str, timeframe: str, limit: int):
    return bx.get("/api/v3/klines", {"symbol": symbol, "interval": timeframe, "limit": limit})


def _runtime_dir(cfg) -> Path:
    d = Path(getattr(cfg, "dataDir", Path("data"))) / "runtime"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def write_status(cfg, payload: dict) -> None:
    try:
        path = _runtime_dir(cfg) / "btc_range_v1_status.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        return


def compute_buy_price(bid: float, ask: float, tick: float, cross_spread: bool) -> float:
    if cross_spread:
        return round_tick_up(ask, tick)
    return round_tick_down(bid, tick)


def ensure_symbol_account_is_safe(bx: Binance, cfg, symbol: str, min_notional: float) -> None:
    if bool(getattr(cfg, "dryRun", False)) or bool(getattr(cfg, "allowExistingBaseBalance", False)):
        return

    base_asset = symbol.replace("USDC", "")
    free_base = get_asset_balance_safe(bx, cfg, base_asset)
    if free_base is None or free_base <= 0:
        return

    ticker = bx.get("/api/v3/ticker/bookTicker", {"symbol": symbol})
    bid = float(ticker.get("bidPrice", 0.0))
    notional = free_base * bid
    if bid > 0 and notional >= float(min_notional):
        raise RuntimeError(
            f"Existing {base_asset} balance detected ({free_base:.8f}, ~{notional:.2f} USDC). "
            f"Use a dedicated account/wallet or set allowExistingBaseBalance=true knowingly."
        )


def _log_check(log_trade, message: str) -> None:
    print(message)
    try:
        log_trade(message)
    except Exception:
        pass


def main():
    cfg = loadConfig()
    symbol = cfg.symbol

    vpnCheckOrDie(cfg.ipFile, cfg.ipCheckTimeout)

    bx = Binance(
        cfg.apiKey,
        cfg.apiSecret,
        cfg.baseUrl,
        cfg.httpTimeout,
        cfg.httpRetries,
        cfg.httpBackoff,
    )

    tick, step, exchange_min_notional = get_symbol_filters(bx, symbol)
    min_notional = max(float(exchange_min_notional), float(getattr(cfg, "minOrderNotionalUsdc", 0.0) or 0.0))
    ensure_symbol_account_is_safe(bx, cfg, symbol, min_notional)

    stream = Stream(cfg, symbol)
    log_day_ctx = LogDayContext()
    log_trade = tradeLogger(cfg, symbol, log_day_ctx)
    log_csv = tradeCsvLogger(cfg, symbol, log_day_ctx)
    log_err = errorLogger(cfg, symbol, log_day_ctx)
    ensureCsvHeader(cfg, symbol, log_day_ctx)

    stream.start()

    bid_ticks: list[tuple[float, float]] = []
    pos: PositionState | None = None
    cooldown_until = 0.0
    snapshot = None
    next_refresh = 0.0
    last_chk = 0.0
    last_hold_reason = ""

    _log_check(
        log_trade,
        (
            f"BTC_RANGE_SESSION_START symbol={symbol} profile={cfg.profileName} dry_run={int(bool(cfg.dryRun))} "
            f"timeframe={cfg.rangeTimeframe} bars={cfg.rangeWindowBars}/{cfg.contextWindowBars} "
            f"min_range={cfg.minRangePct*100:.3f}% max_range={cfg.maxRangePct*100:.3f}%"
        ),
    )

    while True:
        try:
            now = time.time()
            bid, ask, tick_ts, _ = stream.snapshot()
            if bid <= 0 or ask <= 0:
                time.sleep(cfg.idleSleep)
                continue

            bid_ticks.append((float(tick_ts or now), float(bid)))
            cutoff = now - float(getattr(cfg, "ticksKeepSec", 900.0))
            while bid_ticks and bid_ticks[0][0] < cutoff:
                bid_ticks.pop(0)

            if now >= next_refresh:
                limit = max(int(cfg.contextWindowBars), int(cfg.rangeWindowBars)) + 5
                klines = fetch_klines(bx, symbol, cfg.rangeTimeframe, limit)
                snapshot = build_range_snapshot(klines, cfg)
                next_refresh = now + float(cfg.rangeRefreshSec)

            if snapshot is None:
                time.sleep(cfg.idleSleep)
                continue

            spread = (ask - bid) / bid if bid > 0 else 1.0

            if now < cooldown_until:
                if now - last_chk >= float(cfg.chkEvery):
                    left = cooldown_until - now
                    _log_check(
                        log_trade,
                        (
                            f"BTC_RANGE_COOLDOWN left={left:.1f}s state={'IN_POS' if pos else 'IDLE'} "
                            f"bid={bid:.2f} low={snapshot.low:.2f} high={snapshot.high:.2f}"
                        ),
                    )
                    last_chk = now
                time.sleep(cfg.idleSleep)
                continue

            if pos is None:
                ok, reason, plan, rebound_pct = entry_signal(snapshot, bid, spread, bid_ticks, cfg)
                if now - last_chk >= float(cfg.chkEvery):
                    zone = plan.entryZone if plan is not None else 0.0
                    target = plan.targetPrice if plan is not None else 0.0
                    rr = plan.rewardRisk if plan is not None else 0.0
                    hold_suffix = f" hold={reason}" if reason else ""
                    _log_check(
                        log_trade,
                        (
                            f"BTC_RANGE_CHK state=IDLE bid={bid:.2f} ask={ask:.2f} spread={spread*100:.4f}% "
                            f"low={snapshot.low:.2f} high={snapshot.high:.2f} mid={snapshot.mid:.2f} "
                            f"range={snapshot.rangePct*100:.3f}% drift={snapshot.driftPct*100:.3f}% "
                            f"entry_zone={zone:.2f} target={target:.2f} rr={rr:.2f} rebound={rebound_pct*100:.4f}%{hold_suffix}"
                        ),
                    )
                    last_chk = now
                last_hold_reason = reason

                write_status(
                    cfg,
                    {
                        "ts": local_timestamp(),
                        "bot": "btc_range_v1",
                        "symbol": symbol,
                        "state": "IDLE",
                        "bid": bid,
                        "ask": ask,
                        "spread_pct": spread * 100.0,
                        "snapshot": snapshot.__dict__,
                        "last_hold_reason": last_hold_reason,
                    },
                )

                if not ok or plan is None:
                    time.sleep(cfg.idleSleep)
                    continue

                usdc = get_usdc_balance_safe(bx, cfg)
                if usdc is None:
                    time.sleep(cfg.idleSleep)
                    continue
                if usdc < min_notional:
                    last_hold_reason = f"BALANCE {usdc:.2f}<{min_notional:.2f}"
                    time.sleep(cfg.idleSleep)
                    continue

                cross_spread = bool(getattr(cfg, "entryCrossSpread", False))
                auto_cross = float(getattr(cfg, "entryAutoCrossSpreadPct", 0.0) or 0.0)
                if (not cross_spread) and auto_cross > 0 and spread <= auto_cross:
                    cross_spread = True

                buy_px = compute_buy_price(float(bid), float(ask), tick, cross_spread)
                spend = min(float(usdc), float(getattr(cfg, "maxUsdcPerTrade", 0.0) or 0.0))
                qty = round_step(spend / buy_px, step) if buy_px > 0 else 0.0
                notional = qty * buy_px
                if qty > 0 and notional < min_notional:
                    min_qty = round_step_up(min_notional / buy_px, step)
                    if min_qty * buy_px <= float(usdc):
                        qty = min_qty
                        notional = qty * buy_px

                if qty <= 0 or notional < min_notional:
                    last_hold_reason = "QTY_TOO_SMALL"
                    time.sleep(cfg.idleSleep)
                    continue

                order = placeLimit(
                    bx,
                    symbol,
                    "BUY",
                    qty,
                    buy_px,
                    stepQ=step,
                    tickQ=tick,
                    dryRun=cfg.dryRun,
                )
                filled, info = waitFillOrCancel(
                    bx,
                    symbol,
                    order["orderId"],
                    float(getattr(cfg, "entryFillTtlSec", cfg.orderTtl)),
                    cfg.orderPoll,
                    dryRun=cfg.dryRun,
                    side="BUY",
                    qty=qty,
                    price=buy_px,
                )
                if not filled:
                    last_hold_reason = "ENTRY_TIMEOUT"
                    cooldown_until = time.time() + float(getattr(cfg, "entryCooldownSec", 180.0))
                    time.sleep(cfg.idleSleep)
                    continue

                exec_qty = float(info.get("executedQty", qty))
                quote_qty = float(info.get("cummulativeQuoteQty", exec_qty * buy_px))
                entry_px = quote_qty / exec_qty if exec_qty > 0 else buy_px
                pos = PositionState(
                    qty=exec_qty,
                    entry=entry_px,
                    stop=plan.stopPrice,
                    target=plan.targetPrice,
                    rangeLow=snapshot.low,
                    rangeHigh=snapshot.high,
                    rangeMid=snapshot.mid,
                    tsEntry=time.time(),
                    high=entry_px,
                )
                log_day_ctx.ensure_anchor_today()
                _log_check(
                    log_trade,
                    (
                        f"BUY symbol={symbol} qty={exec_qty:.8f} entry={entry_px:.2f} stop={plan.stopPrice:.2f} "
                        f"target={plan.targetPrice:.2f} range_low={snapshot.low:.2f} range_high={snapshot.high:.2f} "
                        f"range_pct={snapshot.rangePct*100:.3f}% rr={plan.rewardRisk:.2f}"
                    ),
                )
                log_csv(
                    {
                        "ts_utc": local_timestamp(),
                        "symbol": symbol,
                        "event": "BUY_FILLED",
                        "side": "BUY",
                        "qty": exec_qty,
                        "price": entry_px,
                        "reason": (
                            f"RANGE_BUY low={snapshot.low:.2f} high={snapshot.high:.2f} "
                            f"target={plan.targetPrice:.2f} stop={plan.stopPrice:.2f}"
                        ),
                        "pnl": "",
                        "profile": cfg.profileName,
                        "dry_run": int(cfg.dryRun),
                        "spread_pct": spread * 100.0,
                        "mom_pct": "",
                        "mom_range_pct": snapshot.rangePct * 100.0,
                        "up_ratio": "",
                        "rsi": "",
                        "ema1_ok": "",
                        "ema5_ok": "",
                        "vol_ok": "",
                        "bid": float(bid),
                        "ask": float(ask),
                        "mid": (float(bid) + float(ask)) / 2.0,
                        "entry_price": entry_px,
                        "p1": "",
                        "p2": "",
                        "p3": "",
                        "p4": "",
                        "entry_vs_mid_pct": "",
                        "mid_vs_entry_pct": "",
                    }
                )
                continue

            exit_reason = update_position(pos, bid, cfg)

            if now - last_chk >= float(cfg.chkEvery):
                _log_check(
                    log_trade,
                    (
                        f"BTC_RANGE_CHK state=IN_POS bid={bid:.2f} ask={ask:.2f} spread={spread*100:.4f}% "
                        f"entry={pos.entry:.2f} stop={pos.stop:.2f} target={pos.target:.2f} "
                        f"range_low={pos.rangeLow:.2f} range_high={pos.rangeHigh:.2f} high={pos.high:.2f}"
                    ),
                )
                last_chk = now

            write_status(
                cfg,
                {
                    "ts": local_timestamp(),
                    "bot": "btc_range_v1",
                    "symbol": symbol,
                    "state": "IN_POS",
                    "bid": bid,
                    "ask": ask,
                    "spread_pct": spread * 100.0,
                    "snapshot": snapshot.__dict__,
                    "position": {
                        "qty": pos.qty,
                        "entry": pos.entry,
                        "stop": pos.stop,
                        "target": pos.target,
                        "rangeLow": pos.rangeLow,
                        "rangeHigh": pos.rangeHigh,
                        "high": pos.high,
                        "protectArmed": pos.protectArmed,
                    },
                },
            )

            if exit_reason is None:
                time.sleep(cfg.idleSleep)
                continue

            base_asset = symbol.replace("USDC", "")
            free_base = get_asset_balance_safe(bx, cfg, base_asset)
            if free_base is None:
                time.sleep(cfg.idleSleep)
                continue
            sell_qty = round_step(free_base, step)
            if sell_qty <= 0 or (sell_qty * bid) < min_notional:
                _log_check(
                    log_trade,
                    f"SELL_SKIP_DUST asset={base_asset} qty={sell_qty:.8f} bid={bid:.2f} min_notional={min_notional:.2f}",
                )
                pos = None
                cooldown_until = time.time() + float(getattr(cfg, "cooldownLossSec", 240.0))
                time.sleep(cfg.idleSleep)
                continue

            sell_px = round_tick_down(float(bid), tick)
            order = placeLimit(
                bx,
                symbol,
                "SELL",
                sell_qty,
                sell_px,
                stepQ=step,
                tickQ=tick,
                dryRun=cfg.dryRun,
            )
            filled, info = waitFillOrCancel(
                bx,
                symbol,
                order["orderId"],
                cfg.orderTtl,
                cfg.orderPoll,
                dryRun=cfg.dryRun,
                side="SELL",
                qty=sell_qty,
                price=sell_px,
            )
            if not filled:
                time.sleep(cfg.idleSleep)
                continue

            exec_qty = float(info.get("executedQty", sell_qty))
            quote_qty = float(info.get("cummulativeQuoteQty", exec_qty * sell_px))
            exit_px = quote_qty / exec_qty if exec_qty > 0 else sell_px
            pnl = (exit_px - pos.entry) * exec_qty

            _log_check(
                log_trade,
                (
                    f"SELL symbol={symbol} qty={exec_qty:.8f} exit={exit_px:.2f} pnl={pnl:.4f} "
                    f"reason={exit_reason} entry={pos.entry:.2f} stop={pos.stop:.2f} target={pos.target:.2f}"
                ),
            )
            log_csv(
                {
                    "ts_utc": local_timestamp(),
                    "symbol": symbol,
                    "event": "SELL_FILLED",
                    "side": "SELL",
                    "qty": exec_qty,
                    "price": exit_px,
                    "reason": exit_reason,
                    "pnl": pnl,
                    "profile": cfg.profileName,
                    "dry_run": int(cfg.dryRun),
                    "spread_pct": spread * 100.0,
                    "mom_pct": "",
                    "mom_range_pct": snapshot.rangePct * 100.0 if snapshot is not None else "",
                    "up_ratio": "",
                    "rsi": "",
                    "ema1_ok": "",
                    "ema5_ok": "",
                    "vol_ok": "",
                    "bid": float(bid),
                    "ask": float(ask),
                    "mid": (float(bid) + float(ask)) / 2.0,
                    "entry_price": pos.entry,
                    "p1": "",
                    "p2": "",
                    "p3": "",
                    "p4": "",
                    "entry_vs_mid_pct": "",
                    "mid_vs_entry_pct": ((float(bid) - float(pos.entry)) / float(pos.entry) * 100.0) if pos.entry > 0 else "",
                }
            )

            cooldown_until = time.time() + (
                float(getattr(cfg, "cooldownWinSec", 90.0))
                if pnl > 0
                else float(getattr(cfg, "cooldownLossSec", 240.0))
            )
            pos = None

        except KeyboardInterrupt:
            print("STOP requested")
            break
        except Exception as exc:
            try:
                log_err("BTC_RANGE_LOOP_EXCEPTION", exc)
            except Exception:
                pass
            print("BTC_RANGE_LOOP_EXCEPTION", type(exc).__name__, str(exc))
            time.sleep(1.0)


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        import os

        os.environ["BTC_RANGE_SYMBOL"] = sys.argv[1].strip().upper()
    main()
