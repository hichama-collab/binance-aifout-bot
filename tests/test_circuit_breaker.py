import time
import pytest
from risk.circuit_breaker import CircuitBreaker

def test_daily_loss_blocks():
    cb = CircuitBreaker(daily_max_loss_usdc=1.0)
    cb.record_trade(-1.01)
    blocked, reason = cb.should_block_entries()
    assert blocked
    assert "daily_loss" in reason

def test_consecutive_losses_block():
    cb = CircuitBreaker(max_consecutive_losses=3)
    cb.record_trade(-0.05)
    cb.record_trade(-0.05)
    cb.record_trade(-0.05)
    blocked, reason = cb.should_block_entries()
    assert blocked
    assert "consecutive" in reason

def test_win_resets_consecutive():
    cb = CircuitBreaker(max_consecutive_losses=3)
    cb.record_trade(-0.05)
    cb.record_trade(-0.05)
    cb.record_trade(+0.10)
    cb.record_trade(-0.05)
    blocked, _ = cb.should_block_entries()
    assert not blocked

def test_not_blocked_initially():
    cb = CircuitBreaker(daily_max_loss_usdc=1.0)
    blocked, _ = cb.should_block_entries()
    assert not blocked

def test_cooldown():
    cb = CircuitBreaker(cooldown_after_break_sec=10.0)
    cb.trigger_cooldown("test")
    blocked, reason = cb.should_block_entries()
    assert blocked
    assert "cooldown" in reason
