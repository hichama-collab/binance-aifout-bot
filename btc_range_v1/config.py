from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_range_env_path() -> Path | None:
    override = (os.getenv("BTC_RANGE_ENV_FILE") or "").strip()
    candidates = []

    if override:
        candidates.append(Path(override).expanduser())

    project_root = _project_root()
    candidates.extend([
        project_root / ".btc_range.env",
        project_root / "config" / ".btc_range.env",
        Path.cwd() / ".btc_range.env",
        Path.cwd() / "config" / ".btc_range.env",
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


def _load_range_env() -> None:
    env_path = resolve_range_env_path()
    if env_path is not None:
        load_dotenv(env_path, override=True)


@dataclass(frozen=True)
class BtcRangeConfig:
    apiKey: str
    apiSecret: str

    symbol: str = "BTCUSDC"
    profileName: str = "default"
    dryRun: bool = False

    baseUrl: str = "https://api.binance.com"
    httpTimeout: int = 12
    httpRetries: int = 3
    httpBackoff: float = 0.6

    ipFile: Path = Path("ip.txt")
    ipCheckTimeout: int = 8
    dataDir: Path = Path("data")
    configPath: Path = Path("config/btc_range.yaml")

    orderTtl: float = 2.5
    orderPoll: float = 0.2
    entryFillTtlSec: float = 2.5

    entryCooldownSec: float = 180.0
    cooldownWinSec: float = 90.0
    cooldownLossSec: float = 240.0

    rangeRefreshSec: float = 30.0
    rangeTimeframe: str = "5m"
    rangeWindowBars: int = 24
    contextWindowBars: int = 72

    minRangePct: float = 0.0020
    maxRangePct: float = 0.0200
    trendMaxDriftPct: float = 0.0100

    buyZoneFrac: float = 0.20
    targetZoneFrac: float = 0.80
    stopRangeFrac: float = 0.12
    minRewardRisk: float = 1.20

    reboundConfirmTicks: int = 3
    reboundMinPct: float = 0.00035
    ticksKeepSec: float = 900.0

    spreadMaxPct: float = 0.0008
    entryCrossSpread: bool = False
    entryAutoCrossSpreadPct: float = 0.00020

    maxUsdcPerTrade: float = 75.0
    minOrderNotionalUsdc: float = 10.0

    protectActivateFrac: float = 0.35
    protectLockFrac: float = 0.10
    maxHoldSec: float = 7200.0
    staleAfterSec: float = 1800.0
    staleMinProgressFrac: float = 0.12

    chkEvery: float = 30.0
    idleSleep: float = 0.5
    allowExistingBaseBalance: bool = False


def _load_yaml(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _apply_profile(cfg: BtcRangeConfig) -> BtcRangeConfig:
    data = _load_yaml(cfg.configPath)
    profile_name = (cfg.profileName or "default").strip().lower()
    profile = (data.get("profiles") or {}).get(profile_name) or {}
    if not profile:
        return cfg

    fields = getattr(cfg, "__dataclass_fields__", {})
    updates = {}
    for key, value in profile.items():
        if key not in fields:
            continue
        if key in ("ipFile", "dataDir", "configPath"):
            updates[key] = Path(str(value))
        else:
            updates[key] = value

    if not updates:
        return cfg
    return dataclasses.replace(cfg, **updates)


def loadConfig() -> BtcRangeConfig:
    load_dotenv()
    _load_range_env()

    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("ENV manquant (.env)")

    symbol = (os.getenv("BTC_RANGE_SYMBOL") or "BTCUSDC").strip().upper()
    profile = (os.getenv("BTC_RANGE_PROFILE") or "default").strip().lower()
    dry = (os.getenv("BTC_RANGE_DRY_RUN") or os.getenv("DRY_RUN") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    config_path = Path((os.getenv("BTC_RANGE_CONFIG") or "config/btc_range.yaml").strip())

    cfg = BtcRangeConfig(
        apiKey=api_key,
        apiSecret=api_secret,
        symbol=symbol or "BTCUSDC",
        profileName=profile or "default",
        dryRun=dry,
        configPath=config_path,
    )
    cfg = _apply_profile(cfg)
    return dataclasses.replace(
        cfg,
        symbol=(cfg.symbol or "BTCUSDC").strip().upper(),
        profileName=(cfg.profileName or "default").strip().lower(),
    )
