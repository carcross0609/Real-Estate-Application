"""Data-quality rule + coverage-watchdog unit tests (no DB). Rules must *flag, not drop*
(§14.4): an implausible value produces a violation with a severity, and the record still
flows — so these assert the rule fires and picks the right severity, not that anything is
rejected.
"""

from decimal import Decimal

from deallens.modules.admin.models import DqSeverity
from deallens.modules.ingestion.models import ListingStatus
from deallens.modules.ingestion.quality import (
    check_listing,
    check_property,
    coverage_report,
    run_dq_checks,
)
from deallens.modules.ingestion.schemas import (
    AddressNorm,
    ListingDelta,
    PropertyDelta,
)


def _prop(**over: object) -> PropertyDelta:
    base: dict[str, object] = dict(
        address=AddressNorm(line1="1 A ST", city="X", state="TX", zip="78701"),
        latitude=30.2,
        longitude=-97.7,
    )
    base.update(over)
    return PropertyDelta(**base)  # type: ignore[arg-type]


def test_property_clean_passes() -> None:
    assert check_property(_prop(sqft=1800, year_built=1999, beds=3), reference_year=2026) == []


def test_year_built_out_of_range_flags() -> None:
    rules = {v.rule for v in check_property(_prop(year_built=3050), reference_year=2026)}
    assert "year_built_out_of_range" in rules


def test_living_area_exceeds_lot_flags() -> None:
    v = check_property(_prop(sqft=9000, lot_sqft=4000, stories=1), reference_year=2026)
    assert any(x.rule == "living_area_exceeds_lot" for x in v)


def test_missing_geocode_flags() -> None:
    v = check_property(_prop(latitude=None, longitude=None), reference_year=2026)
    assert any(x.rule == "missing_geocode" for x in v)


def test_sold_without_close_price_suppresses() -> None:
    listing = ListingDelta(
        source_listing_key="K",
        property=_prop(),
        status=ListingStatus.SOLD,
        close_price=None,
    )
    violations = check_listing(listing)
    match = next(v for v in violations if v.rule == "sold_without_close_price")
    assert match.severity == DqSeverity.SUPPRESS


def test_run_dq_checks_covers_listing_and_property() -> None:
    listing = ListingDelta(
        source_listing_key="K",
        property=_prop(year_built=3050),
        status=ListingStatus.ACTIVE,
        list_price=Decimal("10"),  # implausibly low
    )
    rules = {v.rule for v in run_dq_checks(listing, reference_year=2026)}
    assert {"list_price_out_of_range", "year_built_out_of_range"} <= rules


def test_coverage_watchdog_breach() -> None:
    assert coverage_report(stored=970, source_reported=1000).breached is True  # 3% gap
    assert coverage_report(stored=990, source_reported=1000).breached is False  # 1% gap
    assert coverage_report(stored=5, source_reported=None).breached is False  # unknown total
