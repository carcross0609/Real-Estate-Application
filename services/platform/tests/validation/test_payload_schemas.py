"""Unit tests for the JSONB payload validation models (no DB). These are the shapes that
must never be written as loose dicts (§20) — so their accept/reject behavior is contract.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from deallens.modules.alerts.schemas import BuyBoxFilters
from deallens.modules.engine.schemas import AssumptionSet, EngineOutputBlock, Interval
from deallens.modules.ingestion.models import PropertyType
from deallens.modules.ingestion.schemas import AddressNorm
from deallens.modules.vision.schemas import RedFlag, RedFlagSeverity, RedFlagType


def test_address_norm_accepts_valid() -> None:
    a = AddressNorm(line1="123 Main St", city="Austin", state="TX", zip="78701")
    assert a.state == "TX" and a.plus4 is None


def test_address_norm_rejects_bad_zip_and_state() -> None:
    with pytest.raises(ValidationError):
        AddressNorm(line1="1 A St", city="Austin", state="TX", zip="ABCDE")
    with pytest.raises(ValidationError):
        AddressNorm(line1="1 A St", city="Austin", state="Texas", zip="78701")


def test_address_norm_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AddressNorm(line1="1 A St", city="Austin", state="TX", zip="78701", county="Travis")


def test_red_flag_roundtrips_enums() -> None:
    rf = RedFlag(type=RedFlagType.FOUNDATION_CRACK, severity=RedFlagSeverity.SEVERE)
    assert rf.type == "foundation_crack" and rf.severity == "severe"


def test_assumption_set_defaults_are_decimal() -> None:
    a = AssumptionSet()
    assert isinstance(a.vacancy_pct, Decimal) and a.vacancy_pct == Decimal("0.08")
    assert a.holding_months == 6


def test_engine_output_block_requires_version_and_strategy() -> None:
    with pytest.raises(ValidationError):
        EngineOutputBlock()  # type: ignore[call-arg]  — both required
    block = EngineOutputBlock(
        engine_version="v1", strategy="flip", arv=Interval(point=Decimal("320000"))
    )
    assert block.arv is not None and block.arv.point == Decimal("320000")


def test_buy_box_filters_bounds_and_types() -> None:
    f = BuyBoxFilters(
        price_max=Decimal("400000"),
        property_types=[PropertyType.SFR, PropertyType.TOWNHOME],
        min_score=Decimal("70"),
    )
    assert f.property_types == [PropertyType.SFR, PropertyType.TOWNHOME]
    with pytest.raises(ValidationError):
        BuyBoxFilters(min_score=Decimal("150"))  # > 100
    with pytest.raises(ValidationError):
        BuyBoxFilters(beds_min=-1)
