"""Scoring pipeline unit tests (no DB) — the §25 intellectual claims: overall = max across
strategies (not average), the winning strategy named, hard gates that cap and show the cap,
missing factors that reweight rather than zero a group, confidence separate from the score, the
recommendation rule layer, category scores, and the explanation ledger.
"""

from decimal import Decimal

from deallens.core.enums import Strategy
from deallens.modules.scoring import score as scoring
from deallens.modules.scoring.models import Recommendation
from deallens.modules.scoring.schemas import ScoringInputs, StrategyMetrics

_D = Decimal


def _strong_ltr() -> ScoringInputs:
    """A strong long-term rental: good CoC/DSCR/cap, decent condition, appreciating market."""
    return ScoringInputs(
        property_id="p1",
        enabled_strategies=[Strategy.LTR, Strategy.FLIP],
        list_price=_D("280000"),
        as_is_value=_D("300000"),
        arv_value=_D("340000"), arv_p10=_D("330000"), arv_p90=_D("350000"),
        arv_confidence=_D("0.85"),
        rent_monthly=_D("2600"), rent_confidence=_D("0.8"),
        strategy_metrics={
            "ltr": StrategyMetrics(coc=_D("0.11"), dscr=_D("1.6"), cap_rate=_D("0.075"),
                                   breakeven_occupancy=_D("0.7"), cash_invested=_D("70000")),
            "flip": StrategyMetrics(flip_margin=_D("0.15"), all_in=_D("330000")),
        },
        condition_arbitrage=_D("0.12"), renovation_difficulty=2, red_flag_severity=_D("0.1"),
        rehab_to_arv_ratio=_D("0.08"), condition_confidence=_D("0.7"),
        school_percentile=_D("80"), crime_index=_D("20"), walkability=_D("70"),
        appreciation_pct=_D("0.06"), appreciation_confidence=_D("0.8"),
        market_liquidity=_D("75"), comp_count=8, photo_coverage_ratio=_D("0.9"),
    )


# --- Overall = max, winning strategy ----------------------------------------------------


def test_overall_is_max_across_strategies() -> None:
    result = scoring.score_property(_strong_ltr())
    strat_scores = {s.strategy: s.score for s in result.strategy_scores}
    assert result.overall_score == max(strat_scores.values())
    assert result.winning_strategy == max(strat_scores, key=lambda k: strat_scores[k])


def test_strong_deal_scores_well_and_recommends_buy() -> None:
    result = scoring.score_property(_strong_ltr())
    assert result.overall_score >= _D("65")
    assert result.recommendation in {Recommendation.BUY, Recommendation.STRONG_BUY}
    assert result.grade[0] in {"A", "B"}


# --- Hard gates (§25.6) -----------------------------------------------------------------


def test_dscr_below_1_caps_rental_score() -> None:
    inp = _strong_ltr()
    inp.strategy_metrics["ltr"] = StrategyMetrics(
        coc=_D("0.11"), dscr=_D("0.85"), cap_rate=_D("0.075"), cash_invested=_D("70000")
    )
    inp.enabled_strategies = [Strategy.LTR]
    result = scoring.score_property(inp)
    ltr = next(s for s in result.strategy_scores if s.strategy is Strategy.LTR)
    assert ltr.score <= _D("65")
    assert ltr.capped_by == "dscr_below_1"
    # The cap is shown, not hidden — it appears in the ledger and the explanation.
    assert "dscr_below_1" in result.explanation.caps


def test_severe_red_flag_caps_score() -> None:
    inp = _strong_ltr()
    inp.red_flag_severity = _D("1.0")  # severe structural
    inp.enabled_strategies = [Strategy.LTR]
    result = scoring.score_property(inp)
    assert result.overall_score <= _D("60")
    assert result.winning_strategy is Strategy.LTR


def test_uninsurable_flood_caps_score() -> None:
    inp = _strong_ltr()
    inp.flood_zone = "AE"
    inp.insurance_quotable = False
    result = scoring.score_property(inp)
    ltr = next(s for s in result.strategy_scores if s.strategy is Strategy.LTR)
    assert ltr.capped_by == "flood_ae_no_insurance"


# --- Missing factors reweight, don't zero -----------------------------------------------


def test_missing_market_factors_do_not_tank_score() -> None:
    full = scoring.score_property(_strong_ltr())
    thin = _strong_ltr()
    thin.appreciation_pct = None
    thin.market_liquidity = None
    thin.school_percentile = None
    thin.crime_index = None
    thin.walkability = None
    thin_result = scoring.score_property(thin)
    # Dropping L/M factors reweights within the remaining groups — the score stays in a
    # comparable band (not collapsed toward 0), while confidence drops.
    assert abs(thin_result.overall_score - full.overall_score) < _D("20")
    assert thin_result.confidence_score < full.confidence_score


def test_confidence_separate_from_score() -> None:
    # Same strong financials, but thin data (few comps, no photos, no estimator confidence):
    # confidence should fall without the score collapsing (§25.1 #4).
    thin = _strong_ltr()
    thin.comp_count = 1
    thin.photo_coverage_ratio = None
    thin.arv_confidence = None
    thin.rent_confidence = None
    thin.condition_confidence = None
    result = scoring.score_property(thin)
    assert result.confidence_score < _D("60")
    assert result.overall_score >= _D("55")  # score itself is not dragged down


# --- Categories & explanation -----------------------------------------------------------


def test_category_scores_populated() -> None:
    result = scoring.score_property(_strong_ltr())
    cats = result.category_scores
    assert cats.profitability is not None
    assert cats.location is not None
    assert cats.condition is not None
    assert cats.appreciation is not None
    assert cats.rental_strength is not None
    assert cats.risk == result.risk_score
    assert cats.renovation_complexity is not None  # reno difficulty 2 → low complexity


def test_explanation_has_positive_and_negative_factors() -> None:
    inp = _strong_ltr()
    inp.crime_index = _D("90")  # a clear negative
    result = scoring.score_property(inp)
    assert result.explanation.positives  # strong CoC/DSCR etc.
    assert result.explanation.negatives  # the crime factor


def test_ledger_contributions_reconstruct_raw_score() -> None:
    result = scoring.score_property(_strong_ltr())
    ltr = next(s for s in result.strategy_scores if s.strategy is Strategy.LTR)
    total = sum((f.contribution for f in ltr.factors), Decimal("0"))
    # Contributions sum to the raw (pre-gate) score, within rounding — the ledger IS the math.
    assert abs(total - ltr.raw_score) < _D("0.5")


# --- Edge cases -------------------------------------------------------------------------


def test_no_scorable_data_is_floor_not_crash() -> None:
    result = scoring.score_property(ScoringInputs(property_id="empty"))
    assert result.overall_score == _D("0")
    assert result.grade == "F"
    assert result.recommendation is Recommendation.PASS
    assert result.winning_strategy is None


def test_risk_higher_with_flags_and_dispersion() -> None:
    safe = scoring.score_property(_strong_ltr())
    risky = _strong_ltr()
    risky.red_flag_severity = _D("0.8")
    risky.rehab_to_arv_ratio = _D("0.4")
    risky.arv_p10 = _D("280000")
    risky.arv_p90 = _D("400000")  # wide comp dispersion
    risky.market_liquidity = _D("20")
    risky_result = scoring.score_property(risky)
    assert risky_result.risk_score > safe.risk_score
