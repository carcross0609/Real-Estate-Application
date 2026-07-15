"""Property-level condition aggregation — pure, deterministic, no I/O (03 §27.3).

Vision models *perceive* per photo; this module *rolls up* those perceptions into one property
condition with the discipline §27.3 demands:

- **Median grade per system**, not mean — one bad photo shouldn't tank a system, one glamour shot
  shouldn't save it. A system with no usable photo is `None` (unknown), never a faked "average"
  (§27.1): coverage gaps are explicit and flow into confidence.
- **Max-pool red-flag severity.** If any photo shows a *severe* foundation crack, the property has
  a severe foundation flag — you don't average a safety finding away.
- **Virtual-staging exclusion.** Virtually-staged photos systematically hide condition (§27.2), so
  they're dropped from grading (and from coverage) — counting them would launder a staged kitchen
  into "renovated."
- **Confidence separate from grade** (§27.5): thin coverage or low-quality photos lower confidence
  without moving the grades; below 0.4 the analyzer shows the degraded-state banner.

The deterministic half of §27.1 ("code prices what was perceived") continues in `rehab.py`, which
consumes this aggregate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from deallens.modules.vision.models import RoomType
from deallens.modules.vision.schemas import (
    ConditionCoverage,
    PhotoPerception,
    RedFlag,
    RedFlagSeverity,
)

# Which room classes grade which system (§27.3). Flooring reads from the living areas/bedrooms
# where floors are visible; exterior from either elevation. Classes not here (garage, utility,
# floorplan) inform red flags but no graded system.
_SYSTEM_ROOMS: dict[str, set[RoomType]] = {
    "kitchen": {RoomType.KITCHEN},
    "bath": {RoomType.BATH},
    "flooring": {RoomType.LIVING, RoomType.BEDROOM},
    "exterior": {RoomType.EXTERIOR_FRONT, RoomType.EXTERIOR_REAR},
    "roof": {RoomType.ROOF},
    "landscaping": {RoomType.YARD},
}
_EXPECTED_SYSTEMS = tuple(_SYSTEM_ROOMS.keys())

# Red-flag types that make a rehab *structurally* hard, not just cosmetic (§27.3 difficulty).
_STRUCTURAL_FLAGS = {"foundation_crack", "sagging_line", "roof_wear", "mold_suspect"}

_SEVERITY_RANK: dict[RedFlagSeverity, int] = {
    RedFlagSeverity.POSSIBLE: 1,
    RedFlagSeverity.LIKELY: 2,
    RedFlagSeverity.SEVERE: 3,
}
_ZERO = Decimal("0")
_ONE = Decimal("1")
_CONF_Q = Decimal("0.001")

# Confidence blend (§27.3/§27.5): coverage matters a bit more than per-photo quality — a crisp
# photo of the kitchen tells you nothing about the roof you never saw.
_W_COVERAGE = Decimal("0.6")
_W_QUALITY = Decimal("0.4")
DEGRADED_CONFIDENCE = Decimal("0.4")  # below this → "estimate from limited photos" banner (§27.5)


@dataclass(frozen=True, slots=True)
class ConditionAggregate:
    """The deterministic property-level roll-up (§27.3). Grades are 1–5 or None (unknown);
    `renovation_difficulty` is 1 (turnkey) … 5 (gut); `confidence` is 0–1."""

    grades: dict[str, int | None]
    red_flags: list[RedFlag]
    renovation_difficulty: int | None
    confidence: Decimal
    coverage: ConditionCoverage
    graded_room_count: int = field(default=0)


def _usable_for_grading(p: PhotoPerception) -> bool:
    """A photo counts toward a grade only if it's usable and not virtually staged (§27.2/§27.3)."""
    return p.quality.photo_usable and not p.quality.staged_or_virtual


def _median_int(values: Sequence[int]) -> int | None:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return ordered[mid]
    # Even count: round the two-middle average to the nearer grade (ties → the higher, optimistic
    # but bounded; the interval/confidence carry the uncertainty, not the point).
    return round((ordered[mid - 1] + ordered[mid]) / 2)


def _system_grades(perceptions: Sequence[PhotoPerception]) -> dict[str, int | None]:
    """Median condition grade per system across its usable, non-staged photos (§27.3)."""
    grades: dict[str, int | None] = {}
    for system, rooms in _SYSTEM_ROOMS.items():
        room_values = [
            p.condition_grade
            for p in perceptions
            if _usable_for_grading(p)
            and p.condition_grade is not None
            and p.room_type is not None
            and p.room_type in {r.value for r in rooms}
        ]
        grades[system] = _median_int([g for g in room_values if g is not None])
    return grades


def _max_pooled_flags(perceptions: Sequence[PhotoPerception]) -> list[RedFlag]:
    """One flag per type at its worst severity seen (§27.3 max-pool). Keeps a representative
    photo-evidence locator (the first at that severity) so the UI can point at it."""
    worst: dict[str, RedFlag] = {}
    for p in perceptions:
        for flag in p.red_flags:
            key = flag.type.value
            current = worst.get(key)
            if current is None or _SEVERITY_RANK[flag.severity] > _SEVERITY_RANK[current.severity]:
                worst[key] = flag
    # Deterministic order: worst severity first, then type name.
    return sorted(
        worst.values(), key=lambda f: (-_SEVERITY_RANK[f.severity], f.type.value)
    )


def _renovation_difficulty(
    grades: dict[str, int | None], red_flags: Sequence[RedFlag]
) -> int | None:
    """1 (near-turnkey) … 5 (gut), from the count of gut-grade systems and the worst structural
    flag (§27.3). None when nothing was graded and no flag fired (unknown, not "easy")."""
    graded = [g for g in grades.values() if g is not None]
    if not graded and not red_flags:
        return None
    gut_systems = sum(1 for g in graded if g <= 2)
    severe_structural = any(
        f.type.value in _STRUCTURAL_FLAGS and f.severity is RedFlagSeverity.SEVERE
        for f in red_flags
    )
    likely_structural = any(
        f.type.value in _STRUCTURAL_FLAGS and f.severity is RedFlagSeverity.LIKELY
        for f in red_flags
    )
    raw = 1 + gut_systems + (2 if severe_structural else 1 if likely_structural else 0)
    return min(5, max(1, raw))


def _coverage(perceptions: Sequence[PhotoPerception]) -> ConditionCoverage:
    """Which systems had a usable, non-staged photo (§27.3). `coverage_ratio` over the expected
    system set drives confidence and the degraded banner (§27.5)."""
    photographed = {
        system
        for system, rooms in _SYSTEM_ROOMS.items()
        if any(
            _usable_for_grading(p) and p.room_type in {r.value for r in rooms}
            for p in perceptions
        )
    }
    missing = [s for s in _EXPECTED_SYSTEMS if s not in photographed]
    ratio = len(photographed) / len(_EXPECTED_SYSTEMS) if _EXPECTED_SYSTEMS else 0.0
    return ConditionCoverage(
        photographed_classes=sorted(photographed),
        missing_classes=missing,
        coverage_ratio=round(ratio, 3),
    )


def _confidence(perceptions: Sequence[PhotoPerception], coverage: ConditionCoverage) -> Decimal:
    """Property AI confidence (§27.3/§27.5): coverage completeness blended with mean per-photo
    self-confidence of the usable photos. A property with three crisp kitchen photos and nothing
    else is high-quality but low-coverage → moderate confidence, correctly."""
    usable = [p for p in perceptions if _usable_for_grading(p)]
    if not usable:
        return _ZERO
    avg_quality = sum((Decimal(str(p.confidence)) for p in usable), _ZERO) / Decimal(len(usable))
    cov = Decimal(str(coverage.coverage_ratio))
    conf = _W_COVERAGE * cov + _W_QUALITY * avg_quality
    return conf.quantize(_CONF_Q)


def aggregate_condition(perceptions: Sequence[PhotoPerception]) -> ConditionAggregate:
    """Roll a listing's per-photo perceptions into one property condition (§27.3). Empty input
    yields an all-unknown, zero-confidence aggregate — an honest "we have no photos to judge," not
    a defaulted "average" property (§27.1)."""
    coverage = _coverage(perceptions)
    grades = _system_grades(perceptions)
    red_flags = _max_pooled_flags(perceptions)
    return ConditionAggregate(
        grades=grades,
        red_flags=red_flags,
        renovation_difficulty=_renovation_difficulty(grades, red_flags),
        confidence=_confidence(perceptions, coverage),
        coverage=coverage,
        graded_room_count=sum(1 for p in perceptions if _usable_for_grading(p)),
    )


__all__ = [
    "DEGRADED_CONFIDENCE",
    "ConditionAggregate",
    "aggregate_condition",
]
