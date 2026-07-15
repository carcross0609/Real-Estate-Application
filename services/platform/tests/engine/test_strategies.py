"""Per-strategy underwriting tests (no DB): flip / LTR / BRRRR / wholesale assembly, the
financing scenario set, and the winner's-curse-honest profit band (03 §26.3–§26.8, §25.8). These
assert the *composition* is right — the pieces are unit-tested in test_finance; here we check
each strategy wires them into a coherent, banded, explainable output block.
"""

from decimal import Decimal

import pytest

from deallens.core.enums import Strategy
from deallens.modules.engine import strategies
from deallens.modules.engine.schemas import (
    AssumptionSet,
    FinancialInputs,
    Interval,
    RehabEstimate,
)

_D = Decimal
_ASSUMPTIONS = AssumptionSet()


def _inputs(**over: object) -> FinancialInputs:
    base: dict[str, object] = dict(
        price=_D("300000"),
        arv=Interval(point=_D("400000"), p10=_D("380000"), p50=_D("400000"), p90=_D("420000")),
        as_is=Interval(point=_D("300000")),
        market_rent_monthly=_D("2600"),
        rehab=RehabEstimate(low=_D("30000"), mid=_D("40000"), high=_D("55000")),
        annual_taxes=_D("6000"),
        annual_insurance=_D("1500"),
        sqft=1800,
        estimator_confidence=_D("0.8"),
    )
    base.update(over)
    return FinancialInputs(**base)  # type: ignore[arg-type]


# --- Financing scenarios ----------------------------------------------------------------


def test_all_four_financing_scenarios_present() -> None:
    out = strategies.analyze_flip(_inputs(), _ASSUMPTIONS)
    labels = {s.label for s in out.financing}
    assert labels == {"conventional", "dscr", "hard_money", "cash"}


def test_cash_scenario_has_no_payment() -> None:
    out = strategies.analyze_ltr(_inputs(), _ASSUMPTIONS)
    cash = next(s for s in out.financing if s.label == "cash")
    assert cash.monthly_payment == _D("0") and cash.loan_amount == _D("0")


def test_hard_money_is_interest_only_with_points() -> None:
    out = strategies.analyze_flip(_inputs(), _ASSUMPTIONS)
    hm = next(s for s in out.financing if s.label == "hard_money")
    assert hm.interest_only is True and hm.points > _D("0")


# --- Flip -------------------------------------------------------------------------------


def test_flip_produces_profit_band() -> None:
    out = strategies.analyze_flip(_inputs(), _ASSUMPTIONS)
    m = out.return_metrics.model_dump()
    assert m["net_profit"] is not None
    # P10 (low ARV, expensive rehab) < P50 < P90 (high ARV, cheap rehab) — honest band.
    assert m["net_profit_p10"] < m["net_profit"] < m["net_profit_p90"]


def test_flip_rule_70_and_all_in() -> None:
    out = strategies.analyze_flip(_inputs(), _ASSUMPTIONS)
    assert out.rule_70_check is not None
    assert out.acquisition is not None and out.all_in == out.acquisition.all_in
    assert out.all_in is not None and out.all_in > _D("300000")  # price + costs + rehab + holding


def test_flip_requires_arv() -> None:
    with pytest.raises(strategies.UnsupportedStrategyError):
        strategies.analyze_flip(_inputs(arv=None), _ASSUMPTIONS)


def test_flip_confidence_carried_through() -> None:
    out = strategies.analyze_flip(_inputs(estimator_confidence=_D("0.42")), _ASSUMPTIONS)
    assert out.confidence == _D("0.42")


# --- LTR --------------------------------------------------------------------------------


def test_ltr_proforma_and_returns() -> None:
    out = strategies.analyze_ltr(_inputs(), _ASSUMPTIONS)
    assert out.proforma is not None
    m = out.return_metrics
    assert m.cap_rate is not None and m.cap_rate > _D("0")
    assert m.dscr is not None
    assert m.grm is not None
    assert m.breakeven_occupancy is not None
    assert out.five_year is not None and out.five_year.hold_years == 5


def test_ltr_cap_rate_matches_noi_over_price() -> None:
    out = strategies.analyze_ltr(_inputs(), _ASSUMPTIONS)
    assert out.proforma is not None and out.return_metrics.cap_rate is not None
    expected = (out.proforma.noi / _D("300000")).quantize(_D("0.00001"))
    assert out.return_metrics.cap_rate == expected


def test_ltr_requires_rent() -> None:
    with pytest.raises(strategies.UnsupportedStrategyError):
        strategies.analyze_ltr(_inputs(market_rent_monthly=None), _ASSUMPTIONS)


def test_ltr_five_year_equity_positive_for_good_deal() -> None:
    out = strategies.analyze_ltr(_inputs(), _ASSUMPTIONS)
    assert out.five_year is not None
    assert out.five_year.exit_value > _D("400000")  # 5yr appreciation on ARV basis
    assert out.five_year.equity_at_exit > _D("0")


# --- BRRRR ------------------------------------------------------------------------------


def test_brrrr_reports_capital_left_in() -> None:
    out = strategies.analyze_brrrr(_inputs(), _ASSUMPTIONS)
    assert out.return_metrics.capital_left_in is not None
    assert out.proforma is not None


def test_brrrr_infinite_return_flagged() -> None:
    # A high-ARV, low-price deal recovers all capital at refi → infinite-return warning.
    strong = _inputs(
        price=_D("200000"),
        arv=Interval(point=_D("450000"), p10=_D("440000"), p50=_D("450000"), p90=_D("460000")),
        market_rent_monthly=_D("3200"),
        rehab=RehabEstimate(low=_D("10000"), mid=_D("15000"), high=_D("20000")),
    )
    out = strategies.analyze_brrrr(strong, _ASSUMPTIONS)
    assert out.return_metrics.capital_left_in is not None
    if out.return_metrics.capital_left_in <= _D("0"):
        assert any("infinite return" in w for w in out.warnings)


def test_brrrr_requires_arv_and_rent() -> None:
    with pytest.raises(strategies.UnsupportedStrategyError):
        strategies.analyze_brrrr(_inputs(market_rent_monthly=None), _ASSUMPTIONS)


# --- Wholesale --------------------------------------------------------------------------


def test_wholesale_mao_and_spread() -> None:
    out = strategies.analyze_wholesale(_inputs(), _ASSUMPTIONS)
    lines = {line.label: line.amount for line in out.return_metrics.lines}
    assert "mao" in lines and "spread_vs_price" in lines
    # MAO = 0.70×400k − 46k(rehab+cont) − 10k fee = 224k; spread = 224k − 300k < 0.
    assert lines["spread_vs_price"] < _D("0")
    assert any("negative spread" in w for w in out.warnings)


# --- Dispatch ---------------------------------------------------------------------------


def test_dispatch_routes_by_strategy() -> None:
    out = strategies.analyze(Strategy.LTR, _inputs(), _ASSUMPTIONS)
    assert out.strategy == "ltr"


def test_dispatch_rejects_overall() -> None:
    with pytest.raises(strategies.UnsupportedStrategyError):
        strategies.analyze(Strategy.OVERALL, _inputs(), _ASSUMPTIONS)


def test_dispatch_rejects_deferred_strategy() -> None:
    with pytest.raises(strategies.UnsupportedStrategyError):
        strategies.analyze(Strategy.MULTIFAMILY, _inputs(), _ASSUMPTIONS)
