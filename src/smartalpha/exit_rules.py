"""One exit policy shared by live execution and historical proof."""

from __future__ import annotations

from dataclasses import dataclass

from smartalpha.config import Settings


@dataclass(frozen=True)
class ExitPolicy:
    stop_loss_pct: float = 20.0
    tp1_pct: float = 50.0
    tp2_pct: float = 100.0
    tp1_fraction: float = 0.5
    tp2_fraction: float = 0.3
    trail_activate_pct: float = 50.0
    trail_drawdown_pct: float = 30.0
    max_hold_sec: int = 3600


def exit_policy(settings: Settings | None = None) -> ExitPolicy:
    s = settings or Settings()
    return ExitPolicy(
        stop_loss_pct=s.execution_stop_loss_pct,
        tp1_pct=s.execution_tp1_pct,
        tp2_pct=s.execution_tp2_pct,
        tp1_fraction=s.execution_tp1_fraction,
        tp2_fraction=s.execution_tp2_fraction,
        trail_activate_pct=s.execution_trail_activate_pct,
        trail_drawdown_pct=s.execution_trail_drawdown_pct,
        max_hold_sec=s.execution_max_hold_sec,
    )


def exit_action(
    return_pct: float,
    peak_pct: float,
    age_sec: int,
    *,
    tp1_done: bool,
    tp2_done: bool,
    policy: ExitPolicy,
) -> tuple[str, float] | None:
    """Return the next action using the same ordering as live monitoring."""
    if policy.max_hold_sec > 0 and age_sec >= policy.max_hold_sec:
        return "max_hold", 1.0
    if return_pct <= -abs(policy.stop_loss_pct):
        return "stop_loss", 1.0
    if not tp1_done and return_pct >= policy.tp1_pct:
        return "tp1", _fraction(policy.tp1_fraction)
    if not tp2_done and return_pct >= policy.tp2_pct:
        return "tp2", _fraction(policy.tp2_fraction)
    if tp1_done and peak_pct >= policy.trail_activate_pct:
        if return_pct <= peak_pct - policy.trail_drawdown_pct:
            return "trail", 1.0
    return None


def simulate_exit(
    gains: dict[str, float | None],
    position: float,
    slip: float,
    *,
    policy: ExitPolicy | None = None,
) -> tuple[float | None, str | None]:
    """Replay the live policy over the available h1/h6/h24 snapshots.

    The historical feed has no sub-hour path, so h1 is the first available
    observation and also the configured max-hold boundary when enabled.
    """
    if position <= 0:
        return None, None
    p = policy or ExitPolicy()
    entry = position * (1 + slip)
    remaining = position
    realized = 0.0
    peak = 0.0
    tp1_done = False
    tp2_done = False
    last_gain: float | None = None

    for key, age_sec in (("h1", 3600), ("h6", 21600), ("h24", 86400)):
        gain = gains.get(key)
        if gain is None:
            continue
        last_gain = float(gain)
        peak = max(peak, last_gain)
        action = exit_action(
            last_gain,
            peak,
            age_sec,
            tp1_done=tp1_done,
            tp2_done=tp2_done,
            policy=p,
        )
        if action is None:
            continue
        stage, fraction = action
        sold = min(remaining, position * fraction)
        if sold <= 0:
            continue
        realized += sold * (1 + last_gain / 100.0) * (1 - slip)
        remaining -= sold
        if stage == "tp1":
            tp1_done = True
        elif stage == "tp2":
            tp2_done = True
        if remaining <= 1e-12 or fraction >= 1.0:
            return realized - entry, f"{stage}@{key}"

    if last_gain is None:
        return None, None
    realized += remaining * (1 + last_gain / 100.0) * (1 - slip)
    return realized - entry, "h24"


def _fraction(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)
