import os
from dataclasses import dataclass, field
import dataclasses
from pathlib import Path
from dotenv import load_dotenv
import yaml


def resolveServiceEnvPath() -> Path | None:
    override = (os.getenv("BOT_SERVICE_ENV") or "").strip()
    project_root = Path(__file__).resolve().parent.parent
    candidates = []

    if override:
        candidates.append(Path(override).expanduser())

    candidates.extend([
        project_root / ".service.env",
        project_root / "config" / ".service.env",
        Path.cwd() / ".service.env",
        Path.cwd() / "config" / ".service.env",
    ])

    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            return resolved
    return None


def _load_service_env() -> None:
    """
    Load runtime overrides from .service.env if present.
    .env keeps API secrets; .service.env can override runtime knobs
    like SYMBOL / PROFILE / DRY_RUN when systemd does not export them.
    """
    service_env = resolveServiceEnvPath()
    if service_env is not None:
        load_dotenv(service_env, override=True)


@dataclass(frozen=True)
class StrategyProfile:
    name: str
    emaFast: int
    emaSlow: int
    rsiMin: float
    rsiMax: float
    volMult: float
    spreadMax: float


PROFILES = {
    "strict": StrategyProfile(
        name="strict",
        emaFast=9,
        emaSlow=21,
        rsiMin=40,
        rsiMax=75,
        volMult=0.0,
        spreadMax=0.006,
    ),
    "aggressive": StrategyProfile(
        name="aggressive",
        emaFast=7,
        emaSlow=21,
        rsiMin=30,
        rsiMax=80,
        volMult=0.0,
        spreadMax=0.006,
    ),
}


@dataclass(frozen=True)
class Config:
    apiKey: str
    apiSecret: str

    baseUrl: str = "https://api.binance.com"

    orderTtl: float = 2.5
    orderPoll: float = 0.2

    # entry speed gate
    entryFillTtlSec: float = 2.5
    entryCooldownSec: float = 30.0

    # microstructure entry gates
    momUseInstant: bool = False
    momLookback: int = 5
    momMaxAgeSec: float = 2.0
    obImbalanceMinRatio: float = 1.8
    obDepthLevels: int = 5
    entryCrossSpread: bool = False
    entryAutoCrossSpreadPct: float = 0.0

    idleSleep: float = 0.5
    chkEvery: float = 0.5

    cooldownWin: int = 10
    cooldownLoss: int = 30

    httpTimeout: int = 12
    httpRetries: int = 3
    httpBackoff: float = 0.6

    ipFile: Path = Path("ip.txt")
    ipCheckTimeout: int = 8

    dataDir: Path = Path("data")
    tradesCsv: Path = Path("data/trades.csv")
    riskYaml: Path = Path("config/risk.yaml")
    blockedSymbolsFile: Path = Path("data/blocked_symbols.txt")

    dryRun: bool = False
    strategyName: str = 'momentum'
    profileName: str = 'major'

    # sizing / risk (tunable via config/risk.yaml)
    maxUsdcPerTrade: float = 50.0

    # exits (shared)
    riskPct: float = 0.008
    tpPct: float = 0.0
    tpMinPct: float = 0.0
    armPct: float = 0.0
    trailPct: float = 0.004
    feeBufPct: float = 0.0025
    maxPosTime: float = 15 * 60
    hardMaxPosTime: float = 0.0

    # early profit protection before full trailing is armed
    protectArmPct: float = 0.0
    protectLockPct: float = 0.0
    protectGivebackPct: float = 0.0
    # momentum (entry)
    momWindowSec: float = 30.0
    momMinPct: float = 0.0005
    momMinUpRatio: float = 0.55
    momRangeMinPct: float = 0.003
    momRangeRelaxPct: float = 0.6
    momRangeRelaxUpRatio: float = 0.75

    # anti-range entry gate
    minRangeEntryPct: float = 0.0035
    minRangeVsSpread: float = 4.0

    # logs
    holdCsvEvery: float = 60.0
    holdLogEvery: float = 10.0

    # dust handling
    dustCooldownSec: float = 60.0
    dustStepFraction: float = 0.5
    qtyMismatchStepFraction: float = 0.5

    # wallet/account resilience
    walletMaxRetries: int = 2
    walletRetryBackoffSec: float = 0.4

    # order REST resilience
    orderRestMaxRetries: int = 3
    orderRestBackoffSec: float = 0.2

    # exit resilience
    sellBalUnavailableMaxLoops: int = 30

    # early tape-exit (PSELL) guardrails
    psellMinAgeSec: float = 25.0
    psellMinLossPct: float = 0.0035
    psellCurrentLossPct: float = 0.0
    psellStaleAgeSec: float = 0.0
    psellStaleLossPct: float = 0.0
    psellConfirmTicks: int = 4
    psellFailAgeSec: float = 0.0
    psellFailLossPct: float = 0.0
    psellFailMaxHighPct: float = 0.0

    # anti-whipsaw re-entry guards
    reentryLossCooldownSec: float = 90.0
    reentryTrailCooldownSec: float = 60.0
    reentryRecoveryPct: float = 0.0015

    # entry quality guards
    entryHardMinUpRatio: float = 0.0
    entryMinStrictUps: int = 1
    entryMinTapeProgressPct: float = 0.0
    entryMinTapeProgressVsSpread: float = 0.0

    # 5m rollover exit guard
    psell5mAgeSec: float = 0.0
    psell5mLossPct: float = 0.0
    psell5mDropPct: float = 0.0
    psell5mNegativeRetPct: float = 0.0

    # tape asymmetry defense
    flowDefenseEnabled: bool = False
    flowLookback: int = 8
    flowMinRatio: float = 1.2
    flowMaxSingleDropPct: float = 0.0025

    # burst mode: catch fast impulsive moves and bypass slow entry gates
    burstEntryEnabled: bool = False
    burstLookbackTicks: int = 4
    burstMaxWindowSec: float = 4.0
    burstMinReturnPct: float = 0.00020
    burstMinMoveVsSpread: float = 4.0
    burstMinVelocityPctPerSec: float = 0.00005
    burstMinEfficiency: float = 0.55
    burstMinPressureRatio: float = 1.8
    burstMaxSingleDropPct: float = 0.00010
    burstSpreadMaxMult: float = 1.5
    burstForceCrossSpread: bool = True
    burstExitConfirmTicks: int = 3
    burstExitGivebackMult: float = 0.55
    burstExitMinDrawdownPct: float = 0.00010
    burstExitDrawdownVsSpread: float = 6.0
    burstFailTtlSec: float = 8.0
    burstFailLossPct: float = 0.0025
    burstFailLossVsSpread: float = 2.5
    burstReversalMinPeakPct: float = 0.0012
    burstFollowTtlSec: float = 4.0
    burstFollowMinExtensionPct: float = 0.00008
    burstExitUnderEntryPct: float = 0.00004

    # entry warmup
    allowWarmupEntry: bool = False

    # ticks buffer
    ticksKeepMinSec: float = 40.0

    # internal floor above exchange MIN_NOTIONAL when desired
    minOrderNotionalUsdc: float = 0.0


    # entry anti-chase (tick microtrend)
    tickEntryEnabled: bool = False
    tickEntryLookback: int = 2
    tickEntryMinPct: float = 0.0002

    # anti-chase caps
    momMaxPct: float = 1.0
    rsiBuyMax: float = 100.0
    # momentum relax multipliers
    momRelaxStrongMult: float = 0.6
    momRelaxMidMult: float = 0.8

    # strategy params (optional; strategy may ignore)
    strategyParams: dict = field(default_factory=dict)



def loadConfig() -> Config:
    load_dotenv()
    _load_service_env()
    apiKey = os.getenv("BINANCE_API_KEY")
    apiSecret = os.getenv("BINANCE_API_SECRET")
    if not apiKey or not apiSecret:
        raise RuntimeError("ENV manquant (.env)")

    dry = os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")
    trades_csv = os.getenv("TRADES_CSV", "").strip()
    strat = os.getenv('STRATEGY', 'momentum').strip().lower() or 'momentum'
    prof = (os.getenv('PROFILE') or 'major').strip().lower() or 'major'

    cfg = Config(apiKey=apiKey, apiSecret=apiSecret, dryRun=dry, strategyName=strat, profileName=prof)
    if trades_csv:
        return dataclasses.replace(cfg, tradesCsv=Path(trades_csv))
    return cfg


def _loadRiskYaml(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def applyRiskConfig(cfg: Config) -> Config:
    data = _loadRiskYaml(cfg.riskYaml)
    prof = (getattr(cfg, "profileName", None) or "major").lower()
    strat = (getattr(cfg, "strategyName", None) or "momentum").lower()

    p = (data.get("profiles") or {}).get(prof) or {}
    s = (data.get("strategies") or {}).get(strat) or {}

    fields = getattr(cfg, "__dataclass_fields__", {}) or {}
    updates = {}

    for k_yaml, k_cfg in [
        ("cap_usdc", "maxUsdcPerTrade"),
        ("riskPct", "riskPct"),
        ("tpPct", "tpPct"),
        ("tp_min_pct", "tpMinPct"),
        ("armPct", "armPct"),
        ("trailPct", "trailPct"),
        ("feeBufPct", "feeBufPct"),
        ("maxPosTime", "maxPosTime"),
        ("hardMaxPosTime", "hardMaxPosTime"),
        ("protect_arm_pct", "protectArmPct"),
        ("protect_lock_pct", "protectLockPct"),
        ("protect_giveback_pct", "protectGivebackPct"),
        ("mom_window_sec", "momWindowSec"),
        ("mom_min_pct", "momMinPct"),
        ("mom_min_up_ratio", "momMinUpRatio"),
        ("mom_range_min_pct", "momRangeMinPct"),
        ("mom_range_relax_pct", "momRangeRelaxPct"),
        ("mom_range_relax_up_ratio", "momRangeRelaxUpRatio"),
        ("min_range_entry_pct", "minRangeEntryPct"),
        ("min_range_vs_spread", "minRangeVsSpread"),
        ("hold_csv_every", "holdCsvEvery"),
        ("hold_log_every", "holdLogEvery"),
        ("dust_cooldown_sec", "dustCooldownSec"),
        ("dust_step_fraction", "dustStepFraction"),
        ("wallet_max_retries", "walletMaxRetries"),
        ("wallet_retry_backoff_sec", "walletRetryBackoffSec"),
        ("order_rest_max_retries", "orderRestMaxRetries"),
        ("order_rest_backoff_sec", "orderRestBackoffSec"),
        ("entry_fill_ttl_sec", "entryFillTtlSec"),
        ("entry_cooldown_sec", "entryCooldownSec"),
        ("mom_use_instant", "momUseInstant"),
        ("mom_lookback", "momLookback"),
        ("mom_max_age_sec", "momMaxAgeSec"),
        ("ob_imbalance_min_ratio", "obImbalanceMinRatio"),
        ("ob_depth_levels", "obDepthLevels"),
        ("entry_cross_spread", "entryCrossSpread"),
        ("entry_auto_cross_spread_pct", "entryAutoCrossSpreadPct"),
        ("allow_warmup_entry", "allowWarmupEntry"),
        ("ticks_keep_min_sec", "ticksKeepMinSec"),
        ("tick_confirmation_enabled", "tickEntryEnabled"),
        ("tick_confirmation_lookback", "tickEntryLookback"),
        ("tick_confirmation_min_pct", "tickEntryMinPct"),
        ("tick_entry_enabled", "tickEntryEnabled"),
        ("tick_entry_lookback", "tickEntryLookback"),
        ("tick_entry_min_pct", "tickEntryMinPct"),
        ("mom_max_pct", "momMaxPct"),
        ("rsi_buy_max", "rsiBuyMax"),
        ("qty_mismatch_step_fraction", "qtyMismatchStepFraction"),
        ("mom_relax_strong_mult", "momRelaxStrongMult"),
        ("mom_relax_mid_mult", "momRelaxMidMult"),
        ("psell_min_age_sec", "psellMinAgeSec"),
        ("psell_min_loss_pct", "psellMinLossPct"),
        ("psell_current_loss_pct", "psellCurrentLossPct"),
        ("psell_stale_age_sec", "psellStaleAgeSec"),
        ("psell_stale_loss_pct", "psellStaleLossPct"),
        ("psell_confirm_ticks", "psellConfirmTicks"),
        ("psell_fail_age_sec", "psellFailAgeSec"),
        ("psell_fail_loss_pct", "psellFailLossPct"),
        ("psell_fail_max_high_pct", "psellFailMaxHighPct"),
        ("reentry_loss_cooldown_sec", "reentryLossCooldownSec"),
        ("reentry_trail_cooldown_sec", "reentryTrailCooldownSec"),
        ("reentry_recovery_pct", "reentryRecoveryPct"),
        ("entry_hard_min_up_ratio", "entryHardMinUpRatio"),
        ("entry_min_strict_ups", "entryMinStrictUps"),
        ("entry_min_tape_progress_pct", "entryMinTapeProgressPct"),
        ("entry_min_tape_progress_vs_spread", "entryMinTapeProgressVsSpread"),
        ("psell_5m_age_sec", "psell5mAgeSec"),
        ("psell_5m_loss_pct", "psell5mLossPct"),
        ("psell_5m_drop_pct", "psell5mDropPct"),
        ("psell_5m_negative_ret_pct", "psell5mNegativeRetPct"),
        ("flow_defense_enabled", "flowDefenseEnabled"),
        ("flow_lookback", "flowLookback"),
        ("flow_min_ratio", "flowMinRatio"),
        ("flow_max_single_drop_pct", "flowMaxSingleDropPct"),
        ("burst_entry_enabled", "burstEntryEnabled"),
        ("burst_lookback_ticks", "burstLookbackTicks"),
        ("burst_max_window_sec", "burstMaxWindowSec"),
        ("burst_min_return_pct", "burstMinReturnPct"),
        ("burst_min_move_vs_spread", "burstMinMoveVsSpread"),
        ("burst_min_velocity_pct_per_sec", "burstMinVelocityPctPerSec"),
        ("burst_min_efficiency", "burstMinEfficiency"),
        ("burst_min_pressure_ratio", "burstMinPressureRatio"),
        ("burst_max_single_drop_pct", "burstMaxSingleDropPct"),
        ("burst_spread_max_mult", "burstSpreadMaxMult"),
        ("burst_force_cross_spread", "burstForceCrossSpread"),
        ("burst_exit_confirm_ticks", "burstExitConfirmTicks"),
        ("burst_exit_giveback_mult", "burstExitGivebackMult"),
        ("burst_exit_min_drawdown_pct", "burstExitMinDrawdownPct"),
        ("burst_exit_drawdown_vs_spread", "burstExitDrawdownVsSpread"),
        ("burst_fail_ttl_sec", "burstFailTtlSec"),
        ("burst_fail_loss_pct", "burstFailLossPct"),
        ("burst_fail_loss_vs_spread", "burstFailLossVsSpread"),
        ("burst_reversal_min_peak_pct", "burstReversalMinPeakPct"),
        ("burst_follow_ttl_sec", "burstFollowTtlSec"),
        ("burst_follow_min_extension_pct", "burstFollowMinExtensionPct"),
        ("burst_exit_under_entry_pct", "burstExitUnderEntryPct"),
        ("min_order_notional_usdc", "minOrderNotionalUsdc"),
    ]:
        if k_yaml in p and k_cfg in fields:
            updates[k_cfg] = p[k_yaml]

    if "strategyParams" in fields:
        updates["strategyParams"] = s

    if updates:
        cfg = dataclasses.replace(cfg, **updates)

    return cfg




def pickProfile() -> StrategyProfile:
    _load_service_env()
    name = (os.getenv("PROFILE") or "major").strip().lower()
    base = PROFILES.get(name, PROFILES["strict"])

    # allow tuning via config/risk.yaml (no hardcoded thresholds required)
    try:
        data = _loadRiskYaml(Path("config/risk.yaml"))
        p = (data.get("profiles") or {}).get(name) or {}
    except Exception:
        p = {}

    return StrategyProfile(
        name=name,
        emaFast=int(p.get("ema_fast", base.emaFast)),
        emaSlow=int(p.get("ema_slow", base.emaSlow)),
        rsiMin=float(p.get("rsi_min", base.rsiMin)),
        rsiMax=float(p.get("rsi_max", base.rsiMax)),
        volMult=float(p.get("vol_mult", base.volMult)),
        spreadMax=float(p.get("spread_max", base.spreadMax)),
    )
