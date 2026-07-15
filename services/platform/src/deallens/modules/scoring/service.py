"""Scoring module public interface (§9.3/§19). Assembles a `ScoringInputs` from the other
engines' public interfaces — the comps engine (ARV/as-is/rent/appreciation), the financial engine
(per-strategy return metrics), vision (condition factors), and enrichment/markets (location + macro
layers) — runs the pure scoring pipeline, and persists the `scores` + `score_factors` explanation
ledger (§25.3). Reads flow one way (scoring depends on engine/vision/enrichment, never the
reverse), keeping the module graph acyclic (§9.3).

Scoring writes shared reference data (`scores`, `score_factors` — no RLS, §11.1), so it's an
owner-session worker entrypoint; the scheduled path fires off `ScoreUpdated` events downstream.
The pure math is in `score`/`factors`/`weights`/`normalize`; this file is the DB seam over them.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from geoalchemy2 import WKBElement
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.enums import Strategy
from deallens.core.errors import NotFoundError
from deallens.modules.engine import service as engine_service
from deallens.modules.enrichment.models import GeoLayer, GeoLayerKind
from deallens.modules.enrichment.service import layers_for_property
from deallens.modules.ingestion.models import Listing, Property
from deallens.modules.markets.models import MarketStat
from deallens.modules.scoring import score as scoring
from deallens.modules.scoring import weights
from deallens.modules.scoring.models import Recommendation, Score, ScoreFactor
from deallens.modules.scoring.schemas import (
    FactorLedger,
    ScoreResult,
    ScoringInputs,
    StrategyMetrics,
)
from deallens.modules.vision import service as vision_service

# market_stats metric keys read for the M group + median DOM (§28.3 controlled vocab).
_M_RENT_GROWTH = "rent_growth_3yr"
_M_INVENTORY = "inventory_months"
_M_LIQUIDITY = "market_liquidity"
_M_MEDIAN_DOM = "median_dom"


async def _latest_listing(db: AsyncSession, property_id: UUID) -> Listing | None:
    row = await db.execute(
        select(Listing)
        .where(Listing.property_id == property_id)
        .order_by(Listing.list_date.desc().nullslast(), Listing.source_ts.desc().nullslast())
        .limit(1)
    )
    return row.scalars().first()


async def _market_metric(db: AsyncSession, market_id: UUID | None, metric: str) -> Decimal | None:
    """Latest value of a macro market metric for a market (§28.6 — scoring reads the latest closed
    period). Geo-level-agnostic here: the M-group factors are metro-level by nature."""
    if market_id is None:
        return None
    row = await db.execute(
        select(MarketStat.value)
        .where(MarketStat.market_id == market_id, MarketStat.metric == metric)
        .order_by(MarketStat.period.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


def _layer_attrs(layers: list[GeoLayer], kind: GeoLayerKind) -> dict[str, object]:
    for layer in layers:
        if layer.kind is kind:
            return layer.attributes or {}
    return {}


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None


async def _location_inputs(
    db: AsyncSession, geom: WKBElement | None
) -> dict[str, object]:
    """Read the L-group location signals covering a property (§28.3) from the enrichment geo
    layers. Everything is best-effort: a market without a school/crime/flood layer simply omits
    those factors, lowering confidence — never a fabricated value."""
    if geom is None:
        return {}
    layers = await layers_for_property(
        db, geom=geom,
        kinds=[GeoLayerKind.SCHOOL, GeoLayerKind.CRIME, GeoLayerKind.WALKABILITY,
               GeoLayerKind.FLOOD, GeoLayerKind.REGULATORY],
    )
    school = _layer_attrs(layers, GeoLayerKind.SCHOOL)
    crime = _layer_attrs(layers, GeoLayerKind.CRIME)
    walk = _layer_attrs(layers, GeoLayerKind.WALKABILITY)
    flood = _layer_attrs(layers, GeoLayerKind.FLOOD)
    regulatory = _layer_attrs(layers, GeoLayerKind.REGULATORY)
    return {
        "school_percentile": _dec(school.get("percentile") or school.get("rating_pct")),
        "crime_index": _dec(crime.get("index")),
        "walkability": _dec(walk.get("score")),
        "flood_zone": flood.get("zone") if isinstance(flood.get("zone"), str) else None,
        "insurance_quotable": bool(flood.get("insurance_quotable", True)),
        "str_regulation_flag": bool(regulatory.get("str_prohibited", False)),
    }


def _metrics_from_analysis(block: object) -> StrategyMetrics:
    """A stored analysis `EngineOutputBlock` → the flat `StrategyMetrics` scoring reads (§26.5 →
    §25.3)."""
    rm = getattr(block, "return_metrics", None)
    return StrategyMetrics(
        coc=getattr(rm, "coc", None),
        dscr=getattr(rm, "dscr", None),
        cap_rate=getattr(rm, "cap_rate", None),
        flip_margin=getattr(rm, "flip_margin", None),
        roi_annualized=getattr(rm, "roi_annualized", None),
        capital_left_in=getattr(rm, "capital_left_in", None),
        cash_invested=getattr(rm, "cash_invested", None),
        net_profit=getattr(rm, "net_profit", None),
        breakeven_occupancy=getattr(rm, "breakeven_occupancy", None),
        all_in=getattr(block, "all_in", None),
    )


async def build_scoring_inputs(db: AsyncSession, *, property_id: UUID) -> ScoringInputs:
    """Gather every §25.3 signal for a property from the upstream engines. Missing pieces are left
    None so the scorer degrades on confidence, not on the number (§25.1 #4)."""
    prop = await db.get(Property, property_id)
    if prop is None:
        raise NotFoundError("Property not found")

    valuation = await engine_service.get_property_valuation(db, property_id=property_id)
    listing = await _latest_listing(db, property_id)
    list_price = listing.list_price if listing else None
    arv = valuation.arv
    as_is = valuation.as_is
    rent = valuation.rent_ltr
    appreciation = valuation.appreciation

    strategy_metrics: dict[str, StrategyMetrics] = {}
    for strat in weights.scorable_strategies():
        analysis = await engine_service.get_analysis(db, property_id=property_id, strategy=strat)
        if analysis is not None:
            strategy_metrics[strat.value] = _metrics_from_analysis(analysis)

    cond_factors = await vision_service.condition_scoring_factors(
        db, property_id=property_id, arv=arv.point if arv else None, list_price=list_price,
    )
    condition = await vision_service.get_property_condition(db, property_id=property_id)

    loc = await _location_inputs(db, prop.geom)
    market_id = prop.market_id
    median_dom = await _market_metric(db, market_id, _M_MEDIAN_DOM)

    enabled = [s for s in weights.scorable_strategies() if s.value in strategy_metrics]
    return ScoringInputs(
        property_id=str(property_id),
        market_id=str(market_id) if market_id else None,
        property_class=prop.property_type.value if prop.property_type else None,
        enabled_strategies=enabled,
        list_price=list_price,
        dom=listing.dom_current if listing else None,
        market_median_dom=int(median_dom) if median_dom is not None else None,
        remarks=listing.remarks if listing else None,
        as_is_value=as_is.point if as_is else None,
        arv_value=arv.point if arv else None,
        arv_p10=arv.low if arv else None,
        arv_p90=arv.high if arv else None,
        arv_confidence=arv.confidence if arv else None,
        rent_monthly=rent.point if rent else None,
        rent_confidence=rent.confidence if rent else None,
        strategy_metrics=strategy_metrics,
        condition_arbitrage=cond_factors.condition_arbitrage if cond_factors else None,
        renovation_difficulty=cond_factors.renovation_difficulty if cond_factors else None,
        red_flag_severity=cond_factors.red_flag_severity if cond_factors else None,
        rehab_to_arv_ratio=cond_factors.rehab_to_arv_ratio if cond_factors else None,
        condition_confidence=cond_factors.confidence if cond_factors else None,
        photo_coverage_ratio=(
            _dec(condition.coverage.coverage_ratio) if condition and condition.coverage else None
        ),
        appreciation_pct=appreciation.annual_pct if appreciation else None,
        appreciation_confidence=appreciation.confidence if appreciation else None,
        rent_growth_3yr=await _market_metric(db, market_id, _M_RENT_GROWTH),
        inventory_months=await _market_metric(db, market_id, _M_INVENTORY),
        market_liquidity=await _market_metric(db, market_id, _M_LIQUIDITY),
        comp_count=arv.comp_count if arv else 0,
        school_percentile=loc.get("school_percentile"),  # type: ignore[arg-type]
        crime_index=loc.get("crime_index"),  # type: ignore[arg-type]
        walkability=loc.get("walkability"),  # type: ignore[arg-type]
        flood_zone=loc.get("flood_zone"),  # type: ignore[arg-type]
        insurance_quotable=bool(loc.get("insurance_quotable", True)),
        str_regulation_flag=bool(loc.get("str_regulation_flag", False)),
    )


async def score_property(
    db: AsyncSession, *, property_id: UUID, persist: bool = True
) -> ScoreResult:
    """Score a property (§25): assemble inputs, run the pipeline, and persist one `scores` row per
    strategy plus an `overall` row (the max, with the winning strategy named — §25.4), each with
    its `score_factors` explanation ledger. Runs on the owner-role session."""
    inputs = await build_scoring_inputs(db, property_id=property_id)
    result = scoring.score_property(inputs)
    if persist:
        listing = await _latest_listing(db, property_id)
        prop = await db.get(Property, property_id)
        await _persist(db, property_id, listing, prop, result)
        await db.flush()
    return result


async def _persist(
    db: AsyncSession, property_id: UUID, listing: Listing | None, prop: Property | None,
    result: ScoreResult,
) -> None:
    """Append the per-strategy `scores` rows + an `overall` row, each with its factor ledger
    (§11.3/§25.3). Appended (not upserted) like valuations, so score history is preserved."""
    market_id = prop.market_id if prop else None
    listing_id = listing.id if listing else None

    for ss in result.strategy_scores:
        rec = scoring.recommendation_for(ss.score, result.risk_score, result.confidence_score)
        _add_score(
            db, property_id=property_id, listing_id=listing_id, market_id=market_id,
            strategy=ss.strategy, score=ss.score, grade=weights.grade_for(ss.score),
            risk=result.risk_score, confidence=result.confidence_score, recommendation=rec,
            winning_strategy=None, factors=ss.factors,
        )

    # The overall row: the max across strategies, winning strategy named (§25.4).
    winner_factors = next(
        (s.factors for s in result.strategy_scores if s.strategy == result.winning_strategy), []
    )
    _add_score(
        db, property_id=property_id, listing_id=listing_id, market_id=market_id,
        strategy=Strategy.OVERALL, score=result.overall_score, grade=result.grade,
        risk=result.risk_score, confidence=result.confidence_score,
        recommendation=result.recommendation, winning_strategy=result.winning_strategy,
        factors=winner_factors,
    )


def _add_score(
    db: AsyncSession,
    *,
    property_id: UUID,
    listing_id: UUID | None,
    market_id: UUID | None,
    strategy: Strategy,
    score: Decimal,
    grade: str,
    risk: Decimal,
    confidence: Decimal,
    recommendation: Recommendation,
    winning_strategy: Strategy | None,
    factors: list[FactorLedger],
) -> None:
    row = Score(
        property_id=property_id, listing_id=listing_id, market_id=market_id, strategy=strategy,
        score=score, grade=grade, risk_score=risk, confidence_score=confidence,
        recommendation=recommendation, winning_strategy=winning_strategy,
        scoring_version=weights.SCORING_VERSION,
    )
    db.add(row)
    for f in factors:
        db.add(ScoreFactor(
            score=row, factor_key=f.factor_key, factor_group=f.group,
            raw_value=f.raw_value, percentile=f.normalized, weight=f.weight,
            contribution=f.contribution, rationale=f.rationale, capped_by=f.capped_by,
        ))


async def get_score(
    db: AsyncSession, *, property_id: UUID, strategy: Strategy = Strategy.OVERALL
) -> Score | None:
    """The latest persisted score for a property × strategy (the property-page/search read)."""
    row = await db.execute(
        select(Score)
        .where(Score.property_id == property_id, Score.strategy == strategy)
        .order_by(Score.computed_at.desc())
        .limit(1)
    )
    return row.scalars().first()


__all__ = [
    "build_scoring_inputs",
    "get_score",
    "score_property",
]
