import pytest

from core.config import Config, applyRiskConfig, validateRuntimeSafety


def _cfg(**kwargs):
    data = {
        "apiKey": "k",
        "apiSecret": "s",
        "dryRun": False,
        "strategyName": "momentum",
        "profileName": "strict",
    }
    data.update(kwargs)
    return Config(**data)


def test_live_allows_strict_and_aggressive_profiles():
    validateRuntimeSafety(_cfg(profileName="strict"))
    validateRuntimeSafety(_cfg(profileName="aggressive"))


def test_live_rejects_experimental_profile():
    with pytest.raises(RuntimeError, match="PROFILE not allowed"):
        validateRuntimeSafety(_cfg(profileName="major"))


def test_dry_run_allows_experimental_profile():
    validateRuntimeSafety(_cfg(profileName="major", dryRun=True))


def test_live_rejects_unwired_reversal_strategy():
    with pytest.raises(RuntimeError, match="Only STRATEGY=momentum"):
        validateRuntimeSafety(_cfg(strategyName="reversal"))


def test_strict_and_aggressive_load_distinct_live_profiles():
    strict = applyRiskConfig(_cfg(profileName="strict"))
    aggressive = applyRiskConfig(_cfg(profileName="aggressive"))

    assert strict.burstEntryEnabled is False
    assert aggressive.burstEntryEnabled is False
    assert strict.picFilter_enabled is True
    assert aggressive.picFilter_enabled is True
    assert strict.entryMinNetEdgeMult != aggressive.entryMinNetEdgeMult
    assert strict.maxConsecutiveLosses != aggressive.maxConsecutiveLosses
