"""Detect morning-to-evening directional reversals in a fixed OD corridor."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union


Count = Optional[int]

MIN_TOTAL_RIDES = 10
MIN_DIRECTION_DOMINANCE = 2 / 3


class Direction(str, Enum):
    """A direction along a corridor whose endpoint IDs remain fixed."""

    A_TO_B = "A_TO_B"
    B_TO_A = "B_TO_A"


class DirectionStatus(str, Enum):
    VALID = "VALID"
    LOW_TRAFFIC = "LOW_TRAFFIC"
    NO_CLEAR_DIRECTION = "NO_CLEAR_DIRECTION"
    MISSING_DATA = "MISSING_DATA"


class ReversalStatus(str, Enum):
    REVERSAL = "REVERSAL"
    NO_REVERSAL = "NO_REVERSAL"
    UNDETERMINED = "UNDETERMINED"


@dataclass(frozen=True)
class DirectionResult:
    direction: Optional[Direction]
    dominance: Optional[float]
    status: DirectionStatus


@dataclass(frozen=True)
class ReversalResult:
    am_direction: Optional[Direction]
    pm_direction: Optional[Direction]
    am_dominance: Optional[float]
    pm_dominance: Optional[float]
    reversal_status: ReversalStatus
    reversal_reason: str
    am_status: DirectionStatus
    pm_status: DirectionStatus


def _validate_count(name: str, value: Count) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError(f"{name} must be a non-negative integer or None")


def _validate_thresholds(min_total_rides: int, min_dominance: float) -> None:
    if isinstance(min_total_rides, bool) or not isinstance(min_total_rides, int) or min_total_rides < 0:
        raise ValueError("min_total_rides must be a non-negative integer")
    if isinstance(min_dominance, bool) or not isinstance(min_dominance, (int, float)) or not 0 <= min_dominance <= 1:
        raise ValueError("min_dominance must be between 0 and 1")


def get_main_direction(
    ab_count: Count,
    ba_count: Count,
    min_total_rides: int = MIN_TOTAL_RIDES,
    min_dominance: float = MIN_DIRECTION_DOMINANCE,
) -> DirectionResult:
    """Classify one time window's dominant direction for fixed areas A and B.

    ``None`` means missing data; zero means complete observation with no rides.
    Dominance is unavailable when either count is missing or total rides are zero.
    """
    _validate_count("ab_count", ab_count)
    _validate_count("ba_count", ba_count)
    _validate_thresholds(min_total_rides, min_dominance)
    if ab_count is None or ba_count is None:
        return DirectionResult(None, None, DirectionStatus.MISSING_DATA)

    total = ab_count + ba_count
    if total < min_total_rides:
        dominance = max(ab_count, ba_count) / total if total else None
        return DirectionResult(None, dominance, DirectionStatus.LOW_TRAFFIC)

    dominance = max(ab_count, ba_count) / total
    if ab_count == ba_count or dominance < min_dominance:
        return DirectionResult(None, dominance, DirectionStatus.NO_CLEAR_DIRECTION)
    direction = Direction.A_TO_B if ab_count > ba_count else Direction.B_TO_A
    return DirectionResult(direction, dominance, DirectionStatus.VALID)


def detect_reversal(
    am_ab_count: Count,
    am_ba_count: Count,
    pm_ab_count: Count,
    pm_ba_count: Count,
    min_total_rides: int = MIN_TOTAL_RIDES,
    min_dominance: float = MIN_DIRECTION_DOMINANCE,
) -> ReversalResult:
    """Compare AM and PM directions and return an explicit, reusable result."""
    am = get_main_direction(am_ab_count, am_ba_count, min_total_rides, min_dominance)
    pm = get_main_direction(pm_ab_count, pm_ba_count, min_total_rides, min_dominance)
    if am.status is DirectionStatus.MISSING_DATA or pm.status is DirectionStatus.MISSING_DATA:
        status = ReversalStatus.UNDETERMINED
        reason = "MISSING_DATA: at least one time window has incomplete counts"
    elif am.status is not DirectionStatus.VALID or pm.status is not DirectionStatus.VALID:
        status = ReversalStatus.UNDETERMINED
        reason = "UNDETERMINED: both time windows need a valid dominant direction"
    elif am.direction is not pm.direction:
        status = ReversalStatus.REVERSAL
        reason = "AM and PM have opposite valid dominant directions"
    else:
        status = ReversalStatus.NO_REVERSAL
        reason = "AM and PM have the same valid dominant direction"
    return ReversalResult(am.direction, pm.direction, am.dominance, pm.dominance,
                          status, reason, am.status, pm.status)


if __name__ == "__main__":
    examples = [
        ("AM A→B, PM B→A", (18, 6, 5, 15)),
        ("AM B→A, PM A→B", (4, 12, 14, 3)),
        ("same direction", (12, 4, 15, 5)),
        ("missing AM", (None, 0, 5, 15)),
    ]
    for label, counts in examples:
        print(f"{label}: {detect_reversal(*counts)}")
