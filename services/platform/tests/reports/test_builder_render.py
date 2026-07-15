"""Report builder + renderer unit tests (no DB) — the narrative is grounded (every number traces
to the pack, §13.3), comprehensive (all eight sections when data exists), honest (sections skip
when inputs are absent), and the HTML renderer escapes content (§15 XSS/PDF-injection defense).
"""

from datetime import UTC, datetime
from decimal import Decimal

from deallens.core.enums import Strategy
from deallens.modules.engine.schemas import (
    AcquisitionCosts,
    EngineOutputBlock,
    FiveYearProjection,
    Interval,
    OperatingProForma,
    ReturnMetrics,
)
from deallens.modules.engine.schemas import ValuationOut as EngineValuationOut
from deallens.modules.reports import builder, render
from deallens.modules.reports.schemas import (
    PropertyFacts,
    ReportContextPack,
    ReportSection,
)
from deallens.modules.scoring.models import Recommendation
from deallens.modules.scoring.schemas import (
    CategoryScores,
    ScoreExplanation,
    ScoreResult,
    StrategyScore,
)
from deallens.modules.vision.schemas import (
    PropertyConditionOut,
    RehabBreakdown,
    RehabLineItem,
)

_D = Decimal


def _pack() -> ReportContextPack:
    from deallens.modules.engine.schemas import AppreciationOut, PropertyValuationOut, SelectedComp
    pid = "00000000-0000-0000-0000-0000000000aa"
    facts = PropertyFacts(property_id=pid, address_line1="123 Main St", city="Austin",
                          state="TX", zip="78701", property_type="sfr", beds=3, baths=2.0,
                          sqft=1800, year_built=2000, list_price="$300,000")
    comp = SelectedComp(
        property_id="00000000-0000-0000-0000-000000000001", similarity=_D("0.9"),
        observed_value=_D("335000"), adjusted_value=_D("338000"), distance_m=_D("300"),
    )
    val = PropertyValuationOut(
        property_id=pid,
        arv=EngineValuationOut(kind="arv", point=_D("340000"), low=_D("330000"),
                               high=_D("350000"), confidence=_D("0.85"), method="comp-based",
                               comp_count=4, comps=[comp]),
        as_is=EngineValuationOut(kind="as_is", point=_D("300000")),
        rent_ltr=EngineValuationOut(kind="rent_ltr", point=_D("2600")),
        appreciation=AppreciationOut(annual_pct=_D("0.05"), geo_level="zip"),
    )
    score = ScoreResult(
        property_id="p1", scoring_version="v1", overall_score=_D("78.5"), grade="B+",
        winning_strategy=Strategy.LTR, risk_score=_D("35"), confidence_score=_D("72"),
        recommendation=Recommendation.BUY,
        category_scores=CategoryScores(profitability=_D("75"), risk=_D("35"), location=_D("70"),
                                       condition=_D("65"), appreciation=_D("72"),
                                       rental_strength=_D("78"), renovation_complexity=_D("25"),
                                       financing_difficulty=_D("30")),
        strategy_scores=[
            StrategyScore(strategy=Strategy.LTR, score=_D("78.5"), raw_score=_D("78.5")),
            StrategyScore(strategy=Strategy.FLIP, score=_D("62"), raw_score=_D("62")),
        ],
        explanation=ScoreExplanation(positives=["strong cash-on-cash 11%"],
                                     negatives=["crime index elevated"], caps=[]),
    )
    condition = PropertyConditionOut(
        property_id="p1", pipeline_version="heuristic-v0", kitchen_grade=2, renovation_difficulty=3,
        rehab=RehabBreakdown(cosmetic_low=_D("20000"), cosmetic_high=_D("30000"),
                             major_low=_D("10000"), major_high=_D("18000"),
                             contingency_pct=_D("0.15"), total_low=_D("34500"),
                             total_mid=_D("44000"), total_high=_D("55200"), red_flags_present=False,
                             line_items=[RehabLineItem(system="kitchen", category="major",
                                                       low=_D("20000"), mid=_D("25000"),
                                                       high=_D("30000"))]),
    )
    ltr = EngineOutputBlock(
        engine_version="v1", strategy="ltr", arv=Interval(point=_D("340000")),
        all_in=_D("346000"),
        acquisition=AcquisitionCosts(price=_D("300000"), closing_costs_buy=_D("6000"),
                                     rehab_base=_D("40000"), rehab_contingency=_D("6000"),
                                     rehab_total=_D("46000"), holding_costs=_D("2000"),
                                     all_in=_D("354000")),
        proforma=OperatingProForma(gross_potential_rent=_D("31200"), vacancy=_D("2496"),
                                   other_income=_D("0"), effective_gross_income=_D("28704"),
                                   taxes=_D("6000"), insurance=_D("1500"), management=_D("2583"),
                                   maintenance=_D("2496"), capex_reserve=_D("2184"), hoa=_D("0"),
                                   utilities=_D("0"), other_opex=_D("0"),
                                   operating_expenses=_D("12579"), noi=_D("16125"),
                                   noi_after_reserves=_D("13941")),
        return_metrics=ReturnMetrics(cap_rate=_D("0.054"), coc=_D("0.11"), dscr=_D("1.5"),
                                     cash_flow_monthly=_D("350")),
        five_year=FiveYearProjection(hold_years=5, appreciation_pct=_D("0.05"),
                                     exit_value=_D("434000"), equity_at_exit=_D("150000"),
                                     total_cash_flow=_D("21000"), equity_multiple=_D("2.4"),
                                     irr=_D("0.18")),
    )
    flip = EngineOutputBlock(
        engine_version="v1", strategy="flip", arv=Interval(point=_D("340000")),
        return_metrics=ReturnMetrics(net_profit=_D("35000"), flip_margin=_D("0.18")),
    )
    return ReportContextPack(facts=facts, valuation=val, score=score, condition=condition,
                             analyses={"ltr": ltr, "flip": flip}, generated_at=datetime.now(UTC))


# --- Builder ----------------------------------------------------------------------------


def test_all_sections_present_for_full_pack() -> None:
    sections = builder.compose_narrative(_pack())
    ids = {s.id for s in sections}
    assert ids == {"opportunity", "strategies", "calculations", "comparables", "renovation",
                   "projections", "risks", "exits"}


def test_opportunity_grounded_in_score() -> None:
    section = next(s for s in builder.compose_narrative(_pack()) if s.id == "opportunity")
    assert "78.5/100" in section.body_markdown
    assert "grade B+" in section.body_markdown
    assert "Buy" in section.body_markdown
    assert "Long-Term Rental" in section.body_markdown


def test_calculations_cite_actual_numbers() -> None:
    section = next(s for s in builder.compose_narrative(_pack()) if s.id == "calculations")
    assert "$354,000" in section.body_markdown  # all-in from acquisition
    assert "11.0%" in section.body_markdown      # coc


def test_comparables_lists_comps() -> None:
    section = next(s for s in builder.compose_narrative(_pack()) if s.id == "comparables")
    assert "$340,000" in section.body_markdown  # ARV
    assert "$335,000" in section.body_markdown  # comp observed value


def test_exit_scenarios_cover_flip_and_hold() -> None:
    section = next(s for s in builder.compose_narrative(_pack()) if s.id == "exits")
    assert "flip" in section.body_markdown.lower()
    assert "$35,000" in section.body_markdown  # flip net profit


def test_sections_skipped_when_data_absent() -> None:
    bare = ReportContextPack(facts=PropertyFacts(property_id="p2"))
    sections = builder.compose_narrative(bare)
    assert sections == []  # nothing to ground → no fabricated sections


# --- Renderer ---------------------------------------------------------------------------


def test_markdown_export_has_title_and_sections() -> None:
    pack = _pack()
    md = render.to_markdown(pack, builder.compose_narrative(pack))
    assert md.startswith("# Investment Analysis")
    assert "## The Opportunity" in md
    assert "## Exit Scenarios" in md


def test_html_export_is_self_contained() -> None:
    pack = _pack()
    doc = render.to_html(pack, builder.compose_narrative(pack))
    assert doc.startswith("<!doctype html>")
    assert "<style>" in doc  # inline CSS, no external assets
    assert "http://" not in doc and "https://" not in doc  # no external references
    assert "<table>" in doc  # the strategy/comp tables rendered


def test_html_escapes_content_xss_defense() -> None:
    # A section body carrying markup must be escaped, never emitted as live HTML (§15).
    pack = _pack()
    malicious = [ReportSection(id="x", title="X", body_markdown="<script>alert(1)</script>")]
    doc = render.to_html(pack, malicious)
    assert "<script>alert(1)</script>" not in doc
    assert "&lt;script&gt;" in doc
