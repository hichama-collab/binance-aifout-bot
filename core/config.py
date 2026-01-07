import os
from dataclasses import dataclass
import dataclasses
from pathlib import Path
from dotenv import load_dotenv
import yaml


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

    idleSleep: float = 0.1
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

    dryRun: bool = False
    strategyName: str = 'momentum'
    profileName: str = 'strict'

    # sizing / risk (tunable via config/risk.yaml)
    maxUsdcPerTrade: float = 50.0

    # exits (shared)
    riskPct: float = 0.008
    tpPct: float = 0.0
    armPct: float = 0.0
    trailPct: float = 0.004
    feeBufPct: float = 0.0025
    maxPosTime: float = 15 * 60
    hardMaxPosTime: float = 0.0

    # momentum (entry) 
    momWindowSec: float = 30.0
    momMinPct: float = 0.0005
    momMinUpRatio: float = 0.55
    momRangeMinPct: float = 0.003
    momRangeRelaxPct: float = 0.6
    momRangeRelaxUpRatio: float = 0.75

    # logs
    holdCsvEvery: float = 60.0


def loadConfig() -> Config:
    load_dotenv()
    apiKey = os.getenv("BINANCE_API_KEY")
    apiSecret = os.getenv("BINANCE_API_SECRET")
    if not apiKey or not apiSecret:
        raise RuntimeError("ENV manquant (.env)")

    dry = os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")
    trades_csv = os.getenv("TRADES_CSV", "").strip()
    strat = os.getenv('STRATEGY', 'momentum').strip().lower() or 'momentum'
    prof = (os.getenv('PROFILE') or 'strict').strip().lower() or 'strict'

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
    prof = (getattr(cfg, "profileName", None) or "strict").lower()
    strat = (getattr(cfg, "strategyName", None) or "momentum").lower()

    p = (data.get("profiles") or {}).get(prof) or {}
    s = (data.get("strategies") or {}).get(strat) or {}

    fields = getattr(cfg, "__dataclass_fields__", {}) or {}
    updates = {}

    for k_yaml, k_cfg in [
        ("cap_usdc", "maxUsdcPerTrade"),
        ("riskPct", "riskPct"),
        ("tpPct", "tpPct"),
        ("armPct", "armPct"),
        ("trailPct", "trailPct"),
        ("feeBufPct", "feeBufPct"),
        ("maxPosTime", "maxPosTime"),
        ("hardMaxPosTime", "hardMaxPosTime"),
        ("mom_window_sec", "momWindowSec"),
        ("mom_min_pct", "momMinPct"),
        ("mom_min_up_ratio", "momMinUpRatio"),
        ("mom_range_min_pct", "momRangeMinPct"),
        ("mom_range_relax_pct", "momRangeRelaxPct"),
        ("mom_range_relax_up_ratio", "momRangeRelaxUpRatio"),
        ("hold_csv_every", "holdCsvEvery"),
    ]:
        if k_yaml in p and k_cfg in fields:
            updates[k_cfg] = p[k_yaml]

    if "strategyParams" in fields:
        updates["strategyParams"] = s

    if updates:
        cfg = dataclasses.replace(cfg, **updates)

    return cfg


def pickProfile() -> StrategyProfile:
    name = (os.getenv("PROFILE") or "strict").strip().lower()
    return PROFILES.get(name, PROFILES["strict"])
