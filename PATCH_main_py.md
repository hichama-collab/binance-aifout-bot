# PATCH pour main.py - Modifications à apporter manuellement

## 1. AJOUTER l'import (après les autres imports)
```python
# NEW: Import range logic for BTC Range V1
from btc_range_v1.logic import build_range_snapshot, range_market_ok, entry_signal, update_position, RangeSnapshot
```

## 2. AJOUTER la fonction save_status_json (avant main())
```python
def save_status_json(runtime_dir: Path, data: dict):
    """Save current bot status to JSON for dashboard monitoring."""
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        tmp = runtime_dir / "btc_range_v1_status.json.tmp"
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(runtime_dir / "btc_range_v1_status.json")
    except Exception:
        pass
```

## 3. AJOUTER après la ligne `bidTicks = []`:
```python
    # NEW: Klines buffer for range analysis
    klines_buffer = []
    last_kline_fetch = 0.0
    kline_refresh_sec = 30.0
```

## 4. AJOUTER après `runtime_dir`:
```python
    # NEW: Runtime dir for status JSON
    runtime_dir = Path(getattr(cfg, "dataDir", "data")) / "runtime"
```

## 5. AJOUTER la fonction fetch_klines_for_range (dans main(), avant la boucle while):
```python
    # NEW: Fetch klines for range analysis
    def fetch_klines_for_range():
        nonlocal klines_buffer, last_kline_fetch
        now = time.time()
        if now - last_kline_fetch < kline_refresh_sec and klines_buffer:
            return klines_buffer
        try:
            limit = max(range_window_bars + 10, 50)
            klines = bx.get("/api/v3/klines", {
                "symbol": symbol,
                "interval": range_timeframe,
                "limit": limit
            })
            if isinstance(klines, list) and len(klines) >= range_window_bars:
                klines_buffer = klines
                last_kline_fetch = now
                return klines_buffer
        except Exception as e:
            logErr("KLINES_FETCH_FAIL", e)
        return klines_buffer
```

## 6. MODIFIER la section CHK log (après P4 = _get_p):
```python
            # NEW: Range analysis for BTC Range V1
            range_snapshot = None
            range_plan = None
            range_signal = None
            range_rebound = 0.0
            if range_enabled:
                try:
                    klines = fetch_klines_for_range()
                    if klines and len(klines) >= range_window_bars:
                        range_snapshot = build_range_snapshot(klines, cfg)
                        range_ok, range_reason = range_market_ok(range_snapshot, cfg)
                        if range_ok:
                            range_signal, range_reason, range_plan, range_rebound = entry_signal(
                                range_snapshot, bid, spread, bidTicks, cfg
                            )
                except Exception as e:
                    logErr("RANGE_ANALYSIS_FAIL", e)
```

## 7. MODIFIER le CHK log pour ajouter les métriques range:
```python
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
                # NEW: Add range metrics to CHK log
                if range_snapshot:
                    chk_msg += (
                        f" | RANGE_V1 low={fmt(range_snapshot.low)} high={fmt(range_snapshot.high)} "
                        f"range_pct={fmt(range_snapshot.rangePct*100)}% "
                        f"drift={fmt(range_snapshot.driftPct*100)}% "
                        f"trend_ok={range_snapshot.trendOk} atr={fmt(range_snapshot.atr)}"
                    )
                print(chk_msg)
                logTrade(chk_msg)

                # NEW: Save status JSON for dashboard
                status_data = {
                    "ts": now,
                    "symbol": symbol,
                    "bid": bid,
                    "ask": ask,
                    "spread_pct": spread * 100,
                    "state": "IN_POS" if pos else "IDLE",
                    "mom_pct": momPct * 100,
                    "mom_range_pct": momRangePct * 100,
                    "up_ratio": upRatio * 100,
                }
                if range_snapshot:
                    status_data["snapshot"] = {
                        "low": range_snapshot.low,
                        "high": range_snapshot.high,
                        "mid": range_snapshot.mid,
                        "rangePct": range_snapshot.rangePct,
                        "driftPct": range_snapshot.driftPct,
                        "trendOk": range_snapshot.trendOk,
                        "atr": range_snapshot.atr,
                    }
                if pos:
                    status_data["position"] = {
                        "qty": getattr(pos, 'qty', 0),
                        "entry": getattr(pos, 'entry', 0),
                        "stop": getattr(pos, 'stop', 0),
                        "target": getattr(pos, 'target', 0),
                        "protectArmed": getattr(pos, 'protectArmed', False),
                    }
                if range_plan:
                    status_data["range_plan"] = {
                        "entryZone": range_plan.entryZone,
                        "targetPrice": range_plan.targetPrice,
                        "stopPrice": range_plan.stopPrice,
                        "rewardRisk": range_plan.rewardRisk,
                    }
                save_status_json(runtime_dir, status_data)
```

## 8. MODIFIER la section ENTRY pour ajouter Range V1:
```python
            # ===== ENTRY =====
            buySignal = False
            entryMode = ""
            pEntryEnabled = bool(getattr(cfg, "pEntryEnabled", True))
            if pos is None and not pendingSwitchSymbol and has_new_tick:
                # NEW: BTC Range V1 entry signal
                if range_enabled and range_signal:
                    buySignal = True
                    entryMode = "RANGE_V1"
                elif burstOk:
                    buySignal = True
                    entryMode = "BURST"
                elif pEntryEnabled and (
                    (P1 is not None)
                    and (P2 is not None)
                    and (P3 is not None)
                    and (P4 is not None)
                ):
                    # Require a rising tape with actual progress, not just flat equal ticks.
                    buySignal = (P1 >= P2) and (P2 >= P3) and (P3 >= P4) and (P1 > P4)
                    if buySignal:
                        entryMode = "P"
```

## 9. AJOUTER après `burstOverride = entryMode == "BURST"`:
```python
                rangeOverride = entryMode == "RANGE_V1"
```

## 10. AJOUTER la validation Range V1 (après la validation BURST):
```python
                # NEW: Range V1 validation
                if rangeOverride and range_plan:
                    if not range_signal:
                        maybe_hold(
                            now,
                            'HOLD_RANGE_V1',
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
                            detail=f"range_reason={range_reason}",
                        )
                        time.sleep(cfg.idleSleep)
                        continue
                    range_msg = (
                        f"RANGE_V1_TRIGGER low={range_snapshot.low:.2f} high={range_snapshot.high:.2f} "
                        f"entry_zone={range_plan.entryZone:.2f} target={range_plan.targetPrice:.2f} "
                        f"stop={range_plan.stopPrice:.2f} rr={range_plan.rewardRisk:.2f} "
                        f"rebound={range_rebound*100:.4f}%"
                    )
                    print(range_msg)
                    logTrade(range_msg)
```

## 11. REMPLACER tous les `not burstOverride` par `not burstOverride and not rangeOverride`
Dans les validations d'entrée (momOk, strict_up_moves, etc.)
```
# Chercher: if (not burstOverride) and 
# Remplacer par: if (not burstOverride and not rangeOverride) and 
```

## 12. AJOUTER dans le log d'entrée (après burstOverride log):
```python
                if rangeOverride:
                    entry_reason = (
                        f"RANGE_V1 low={range_snapshot.low:.2f} high={range_snapshot.high:.2f} "
                        f"entry_zone={range_plan.entryZone:.2f} target={range_plan.targetPrice:.2f} "
                        f"stop={range_plan.stopPrice:.2f} rr={range_plan.rewardRisk:.2f}"
                    )
```

## 13. AJOUTER les paramètres Range V1 dans la config:
Dans ton fichier de config (config.py ou .env), ajouter:
```python
rangeEnabled = True  # Activer le mode Range V1
rangeTimeframe = "5m"
rangeWindowBars = 24
```
