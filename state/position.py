from dataclasses import dataclass
import time


def floor_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return x
    return int(x / tick) * tick


@dataclass
class Position:
    qty: float
    entry: float
    high: float
    stop: float
    ts_entry: float

    # internal flags (kept as attrs; wallet sync doesn't need them)
    armed: bool = False
    protectArmed: bool = False
    tp: float = 0.0

    def _raise_stop(self, candidate: float, tick: float):
        if candidate <= 0:
            return
        candidate = floor_tick(candidate, tick)
        if candidate > self.stop:
            self.stop = candidate

    def init_stops(self, cfg, profile, tick: float):
        # SL uses cfg.riskPct by default; fallback = 0.8%
        sl_pct = float(getattr(cfg, "riskPct", 0.008))
        if sl_pct <= 0:
            sl_pct = 0.008

        # TP default derived from riskPct, but not too small.
        tp_pct = float(getattr(cfg, "tpPct", max(0.006, sl_pct * 3.0)))
        tp_min_pct = float(getattr(cfg, "tpMinPct", 0.0) or 0.0)
        if tp_min_pct > 0:
            tp_pct = max(tp_pct, tp_min_pct)
        arm_pct = float(getattr(cfg, "armPct", max(0.0045, sl_pct * 1.5)))
        fee_buf = float(getattr(cfg, "feeBufPct", 0.0025))
        trail_pct = float(getattr(cfg, "trailPct", 0.004))

        # Store into profile/cfg-less internal
        self.armed = False
        self.protectArmed = False

        if self.high <= 0:
            self.high = self.entry

        if self.stop <= 0:
            self.stop = self.entry * (1.0 - sl_pct)
            self.stop = floor_tick(self.stop, tick)

        # take profit price
        self.tp = self.entry * (1.0 + tp_pct)
        self.tp = floor_tick(self.tp, tick)

        # keep these stored (no new abstractions)
        self._sl_pct = sl_pct
        self._tp_pct = tp_pct
        self._arm_pct = arm_pct
        self._fee_buf = fee_buf
        self._trail_pct = trail_pct
        self._protect_arm_pct = float(getattr(cfg, "protectArmPct", 0.0) or 0.0)
        if self._protect_arm_pct <= 0:
            self._protect_arm_pct = max(
                self._fee_buf * 1.5,
                min(
                    max(0.0030, self._arm_pct * 0.5),
                    max(0.0030, (self._tp_pct * 0.33) if self._tp_pct > 0 else (self._sl_pct * 0.5)),
                ),
            )
        self._protect_lock_pct = float(getattr(cfg, "protectLockPct", 0.0) or 0.0)
        if self._protect_lock_pct <= 0:
            self._protect_lock_pct = max(self._fee_buf, self._protect_arm_pct * 0.5)
        self._protect_giveback_pct = float(getattr(cfg, "protectGivebackPct", 0.0) or 0.0)
        if self._protect_giveback_pct <= 0:
            self._protect_giveback_pct = max(self._fee_buf, self._protect_arm_pct - self._protect_lock_pct)

        # first layer: if the trade moves enough in our favor, lock a small gain
        self.protectArmed = self.high >= (self.entry * (1.0 + self._protect_arm_pct))
        if self.protectArmed:
            protect_floor = self.entry * (1.0 + self._protect_lock_pct)
            protect_stop = self.high * (1.0 - self._protect_giveback_pct)
            if protect_stop < protect_floor:
                protect_stop = protect_floor
            self._raise_stop(protect_stop, tick)

        # second layer: regular trailing once the move is strong enough
        self.armed = self.high >= (self.entry * (1.0 + arm_pct))
        if self.armed:
            be_stop = self.entry * (1.0 + fee_buf)
            self._raise_stop(be_stop, tick)
            trail_stop = self.high * (1.0 - self._trail_pct)
            if trail_stop < be_stop:
                trail_stop = be_stop
            self._raise_stop(trail_stop, tick)

    def update(self, price: float, cfg, profile, tick: float):
        if price <= 0:
            return

        if self.high <= 0:
            self.high = price

        if price > self.high:
            self.high = price

        # first protection layer before the main trailing is armed
        if not self.protectArmed and self.high >= (self.entry * (1.0 + self._protect_arm_pct)):
            self.protectArmed = True

        if self.protectArmed and not self.armed:
            protect_floor = self.entry * (1.0 + self._protect_lock_pct)
            protect_stop = self.high * (1.0 - self._protect_giveback_pct)
            if protect_stop < protect_floor:
                protect_stop = protect_floor
            self._raise_stop(protect_stop, tick)

        # arm trailing only after profit threshold
        if not self.armed and self.high >= (self.entry * (1.0 + self._arm_pct)):
            self.armed = True
            # once armed, force stop >= break-even + buffer
            be_stop = floor_tick(self.entry * (1.0 + self._fee_buf), tick)
            if be_stop > self.stop:
                self.stop = be_stop

        if not self.armed:
            return

        # trailing stop based on high
        new_stop = self.high * (1.0 - self._trail_pct)

        # floor at break-even+buffer
        be_stop = self.entry * (1.0 + self._fee_buf)
        if new_stop < be_stop:
            new_stop = be_stop

        new_stop = floor_tick(new_stop, tick)

        if new_stop > self.stop:
            self.stop = new_stop

    def exit_reason(self, price: float, cfg, profile):
        # TP first
        if self.tp > 0 and price >= self.tp:
            return "TP"

        # stop
        if price <= self.stop:
            if self.armed:
                return "TRAIL"
            if self.protectArmed:
                return "PROTECT"
            return "STOP"

        # time stop
        max_hold = float(getattr(cfg, "maxPosTime", 15 * 60))
        hard_hold = float(getattr(cfg, "hardMaxPosTime", 0.0))
        age = time.time() - self.ts_entry

        # Hard time-stop: exit no matter what
        if hard_hold > 0 and age >= hard_hold:
            return "TIME_HARD"

        # Soft time-stop: exit only if at least break-even+buffer
        if age >= max_hold:
            if price >= (self.entry * (1.0 + self._fee_buf)):
                return "TIME"
        return None
