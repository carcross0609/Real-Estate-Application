"""Scoring I/O contracts (03 §25). `ScoringInputs` is the raw per-property signal bundle the
service assembles from the engine (valuations, per-strategy financials, appreciation), vision
(condition factors), and the market layers (L/M); the pure scoring pipeline turns it into a
`ScoreResult`. The explanation ledger (`FactorLedger`) is produced by the same code path as the
number — it *is* the derivation, so it cannot drift from the score (§25.1 #3).

Every factor value is optional: a thin-data property is scored on what's present with lowered
*confidence*, never silently downgraded (§25.1 #4). Money is Decimal (§20)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from deallens.core.enums import Strategy
from deallens.modules.scoring.models import Recommendation


class StrategyMetrics(BaseModel):
    """The per-strategy financial outputs scoring reads off a stored `analyses.return_metrics`
    (03 §26.5 → §25.3 Group F). All optional — a strategy that couldn't be underwritten (no ARV)
    contributes no F factors and is simply not scored."""

    model_config = ConfigDict(extra="forbid")

    coc: Decimal | None = None
    dscr: Decimal | None = None
    cap_rate: Decimal | None = None
    flip_margin: Decimal | None = None
    roi_annualized: Decimal | None = None
    capital_left_in: Decimal | None = None
    cash_invested: Decimal | None = None
    net_profit: Decimal | None = None
    breakeven_occupancy: Decimal | None = None
    all_in: Decimal | None = None


class ScoringInputs(BaseModel):
    """Everything the pure scoring pipeline needs about one property (§25.3). Assembled by the
    service from the other engines' public interfaces; kept a flat, optional-everywhere bundle so
    scoring is unit-tested with no database and degrades honestly on missing data."""

    model_config = ConfigDict(extra="forbid")

    property_id: str
    market_id: str | None = None
    property_class: str | None = None  # market×class cohort key (§25.2)
    enabled_strategies: list[Strategy] = Field(default_factory=list)

    # Deal / listing dynamics (Group D)
    list_price: Decimal | None = None
    dom: int | None = None
    market_median_dom: int | None = None
    price_cut_count: int = 0
    price_cut_pct: Decimal | None = None
    remarks: str | None = None
    relist_count: int = 0

    # Estimators (engine)
    as_is_value: Decimal | None = None
    arv_value: Decimal | None = None
    arv_p10: Decimal | None = None
    arv_p90: Decimal | None = None
    arv_confidence: Decimal | None = None
    rent_monthly: Decimal | None = None
    rent_confidence: Decimal | None = None

    # Per-strategy financials (Group F)
    strategy_metrics: dict[str, StrategyMetrics] = Field(default_factory=dict)

    # Condition (Group C, vision)
    condition_arbitrage: Decimal | None = None
    renovation_difficulty: int | None = None
    red_flag_severity: Decimal | None = None
    rehab_to_arv_ratio: Decimal | None = None
    condition_confidence: Decimal | None = None

    # Location micro (Group L)
    school_percentile: Decimal | None = None  # 0–100
    crime_index: Decimal | None = None  # lower better (0–100)
    walkability: Decimal | None = None  # 0–100
    flood_zone: str | None = None  # FEMA zone code
    insurance_quotable: bool = True

    # Market macro (Group M)
    appreciation_pct: Decimal | None = None
    appreciation_confidence: Decimal | None = None
    rent_growth_3yr: Decimal | None = None
    inventory_months: Decimal | None = None
    market_liquidity: Decimal | None = None  # 0–100 (sale velocity for the class)

    # Risk / confidence inputs
    comp_count: int = 0
    photo_coverage_ratio: Decimal | None = None
    records_matched: bool = True
    str_regulation_flag: bool = False


class FactorLedger(BaseModel):
    """One row of the explanation ledger (§25.3): a named factor's raw value, its normalized 0–100
    score, its weight, and the signed contribution to the strategy score. `capped_by` names the
    hard gate (§25.6) when one is the binding constraint — trust requires showing the cap."""

    model_config = ConfigDict(extra="forbid")

    factor_key: str
    group: str  # F | D | C | L | M
    raw_value: Decimal | None = None
    normalized: Decimal  # 0–100
    weight: Decimal
    contribution: Decimal
    rationale: str | None = None
    capped_by: str | None = None


class StrategyScore(BaseModel):
    """A single strategy's score + its full factor ledger (§25.4). `raw_score` is the arithmetic
    weighted sum; `score` is after hard gates (§25.6). `capped_by` names the gate if one bit."""

    model_config = ConfigDict(extra="forbid")

    strategy: Strategy
    score: Decimal
    raw_score: Decimal
    capped_by: str | None = None
    factors: list[FactorLedger] = Field(default_factory=list)


class CategoryScores(BaseModel):
    """The user-facing category scores (0–100) — the ten dimensions the product surfaces
    alongside the headline number (§25 UI decomposition). Derived from the same normalized factors
    that drive the strategy scores, so they reconcile with the overall by construction."""

    model_config = ConfigDict(extra="forbid")

    profitability: Decimal | None = None
    risk: Decimal | None = None  # 0 (safe) – 100 (risky); mirrors risk_score
    location: Decimal | None = None
    condition: Decimal | None = None
    appreciation: Decimal | None = None
    rental_strength: Decimal | None = None
    liquidity: Decimal | None = None
    renovation_complexity: Decimal | None = None  # 0 (turnkey) – 100 (gut)
    financing_difficulty: Decimal | None = None  # 0 (easy) – 100 (hard)
    market_conditions: Decimal | None = None


class ScoreExplanation(BaseModel):
    """The stated rationale (§25.5): the top positive and negative ledger factors behind the
    recommendation, in plain language for the property card."""

    model_config = ConfigDict(extra="forbid")

    positives: list[str] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)
    caps: list[str] = Field(default_factory=list)  # any hard gates that bound the score


class ScoreResult(BaseModel):
    """The complete scoring output for one property (§25.5): overall + winning strategy, the
    per-strategy and per-category scores, risk/confidence, recommendation, and the explanation.
    `overall_score` is the max across enabled strategies (a property brilliant for one strategy
    *is* a great deal, §25.4) — never a blurry average."""

    model_config = ConfigDict(extra="forbid")

    property_id: str
    scoring_version: str
    overall_score: Decimal
    grade: str
    winning_strategy: Strategy | None = None
    risk_score: Decimal
    confidence_score: Decimal
    recommendation: Recommendation
    category_scores: CategoryScores
    strategy_scores: list[StrategyScore] = Field(default_factory=list)
    explanation: ScoreExplanation = Field(default_factory=ScoreExplanation)


__all__ = [
    "CategoryScores",
    "FactorLedger",
    "ScoreExplanation",
    "ScoreResult",
    "ScoringInputs",
    "StrategyMetrics",
    "StrategyScore",
]
