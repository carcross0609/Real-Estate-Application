"""Appreciation selection + recompute-policy unit tests (no DB). Appreciation must pick the
*finest reliable* geography and label it honestly (03 §28.2); recompute must fire on a new comp,
a version bump, or age, and accumulate every reason for the drift audit (03 §9.5, PRD §4.3).
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from deallens.modules.engine import appreciation
from deallens.modules.engine.appreciation import METRIC_3YR, METRIC_YOY, AppreciationReading
from deallens.modules.engine.recompute import RecomputeReason, needs_recompute
from deallens.modules.markets.models import GeoLevel


def _reading(level: GeoLevel, metric: str, value: str, period: date) -> AppreciationReading:
    return AppreciationReading(
        geo_level=level, geo_id=f"{level.value}-1", metric=metric,
        value=Decimal(value), period=period, source="tierA",
    )


# --- Appreciation selection -------------------------------------------------------------


def test_finest_geography_wins() -> None:
    readings = [
        _reading(GeoLevel.METRO, METRIC_3YR, "0.03", date(2026, 1, 1)),
        _reading(GeoLevel.ZIP, METRIC_3YR, "0.06", date(2026, 1, 1)),
    ]
    chosen = appreciation.select_appreciation(readings)
    assert chosen.geo_level is GeoLevel.ZIP
    assert chosen.annual_pct == Decimal("0.06")


def test_prefers_3yr_over_yoy_at_same_level() -> None:
    readings = [
        _reading(GeoLevel.ZIP, METRIC_YOY, "0.08", date(2026, 1, 1)),
        _reading(GeoLevel.ZIP, METRIC_3YR, "0.05", date(2026, 1, 1)),
    ]
    chosen = appreciation.select_appreciation(readings)
    assert chosen.annual_pct == Decimal("0.05")
    assert chosen.period_years == 3


def test_newest_period_wins_within_level_and_metric() -> None:
    readings = [
        _reading(GeoLevel.ZIP, METRIC_3YR, "0.04", date(2025, 1, 1)),
        _reading(GeoLevel.ZIP, METRIC_3YR, "0.07", date(2026, 1, 1)),
    ]
    chosen = appreciation.select_appreciation(readings)
    assert chosen.annual_pct == Decimal("0.07")
    assert chosen.as_of == date(2026, 1, 1)


def test_level_labeled_honestly_with_confidence() -> None:
    # A metro rate carried to a property is labeled metro (not faked to ZIP precision) and gets
    # the coarser-geography confidence.
    chosen = appreciation.select_appreciation(
        [_reading(GeoLevel.METRO, METRIC_3YR, "0.03", date(2026, 1, 1))]
    )
    assert chosen.geo_level is GeoLevel.METRO
    assert chosen.confidence is not None and chosen.confidence < Decimal("0.7")


def test_no_appreciation_metric_returns_empty_not_zero() -> None:
    chosen = appreciation.select_appreciation(
        [_reading(GeoLevel.ZIP, "median_ppsf", "175", date(2026, 1, 1))]
    )
    assert chosen.annual_pct is None  # honest gap, not a fabricated 0%
    assert chosen.geo_level is None


# --- Recompute policy -------------------------------------------------------------------

_NOW = datetime(2026, 7, 1, 12, 0, 0)


def test_no_valuation_always_recomputes() -> None:
    d = needs_recompute(
        latest_computed_at=None, latest_model_version=None, current_model_version="v1",
        newest_comp_event_at=None, now=_NOW,
    )
    assert d and d.reasons == (RecomputeReason.NO_VALUATION,)


def test_version_bump_triggers() -> None:
    d = needs_recompute(
        latest_computed_at=_NOW - timedelta(days=1), latest_model_version="v1",
        current_model_version="v2", newest_comp_event_at=None, now=_NOW,
    )
    assert RecomputeReason.MODEL_VERSION_CHANGE in d.reasons


def test_new_comp_after_last_computation_triggers() -> None:
    d = needs_recompute(
        latest_computed_at=_NOW - timedelta(days=1), latest_model_version="v1",
        current_model_version="v1", newest_comp_event_at=_NOW - timedelta(hours=1), now=_NOW,
    )
    assert RecomputeReason.NEW_COMP in d.reasons


def test_comp_event_equal_to_last_computation_is_idempotent() -> None:
    at = _NOW - timedelta(days=1)
    d = needs_recompute(
        latest_computed_at=at, latest_model_version="v1", current_model_version="v1",
        newest_comp_event_at=at, now=_NOW,
    )
    assert RecomputeReason.NEW_COMP not in d.reasons  # `>` not `>=` — no self-retrigger loop


def test_age_triggers_and_reasons_accumulate() -> None:
    d = needs_recompute(
        latest_computed_at=_NOW - timedelta(days=40), latest_model_version="v1",
        current_model_version="v2", newest_comp_event_at=_NOW - timedelta(days=1), now=_NOW,
    )
    assert d.should_recompute
    assert RecomputeReason.STALE_AGE in d.reasons
    assert RecomputeReason.NEW_COMP in d.reasons
    assert RecomputeReason.MODEL_VERSION_CHANGE in d.reasons


def test_fresh_estimate_no_change_does_not_recompute() -> None:
    d = needs_recompute(
        latest_computed_at=_NOW - timedelta(days=2), latest_model_version="v1",
        current_model_version="v1", newest_comp_event_at=_NOW - timedelta(days=5), now=_NOW,
    )
    assert not d.should_recompute and d.reasons == ()
