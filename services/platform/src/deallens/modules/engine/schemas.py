"""Validation models for engine JSONB payloads (03 §26). `AssumptionSet` is the versioned
default set snapshotted into every `analyses.assumption_set`; `EngineOutputBlock` is the
full output stored in `analyses.outputs` / `scenarios.outputs` (§26.9 traceability). Money
is `Decimal` end-to-end (§20) — never float, so no rounding drift in stored finance.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AssumptionSet(BaseModel):
    """System assumption set v1 (§26.2/§26.4/§26.6), user-overridable at global/buy-box/
    property level (FR-013). All rates are fractions (0.02 = 2%). Market-table overrides are
    merged in before a run; the merged result is what gets snapshotted.
    """

    model_config = ConfigDict(extra="allow")  # strategy-specific knobs extend this

    version: str = "v1"
    closing_pct_buy: Decimal = Decimal("0.02")
    closing_pct_sell: Decimal = Decimal("0.01")
    agent_commission_pct: Decimal = Decimal("0.055")
    concessions_pct: Decimal = Decimal("0.01")
    rehab_contingency_pct: Decimal = Decimal("0.15")
    rehab_contingency_pct_flagged: Decimal = Decimal("0.20")
    holding_months: int = 6
    vacancy_pct: Decimal = Decimal("0.08")
    management_pct: Decimal = Decimal("0.09")
    maintenance_pct: Decimal = Decimal("0.08")
    capex_reserve_pct: Decimal = Decimal("0.07")
    appreciation_pct: Decimal = Decimal("0.03")
    refi_seasoning_months: int = 6
    refi_ltv: Decimal = Decimal("0.75")
    list_to_effective_rent_adj: Decimal = Decimal("-0.03")


class Interval(BaseModel):
    """A point estimate with its P10/P50/P90 band (§26.6 — show the band, no false
    precision). Ranking uses the conservative quantile (§25.8), the UI shows p50 + band."""

    model_config = ConfigDict(extra="forbid")

    point: Decimal
    p10: Decimal | None = None
    p50: Decimal | None = None
    p90: Decimal | None = None


class FinancingScenario(BaseModel):
    """One of the ≥3 financing scenarios computed per property (§26.3)."""

    model_config = ConfigDict(extra="forbid")

    label: str  # conventional | dscr | hard_money | cash
    down_pct: Decimal
    rate: Decimal
    term_years: int | None = None
    points: Decimal = Decimal("0")
    interest_only: bool = False
    monthly_payment: Decimal | None = None
    loan_amount: Decimal | None = None


class ReturnMetrics(BaseModel):
    """§26.5 return metrics + strategy-specific outputs (flip margin, BRRRR capital-left)."""

    model_config = ConfigDict(extra="allow")

    cap_rate: Decimal | None = None
    cash_flow_monthly: Decimal | None = None
    coc: Decimal | None = None
    dscr: Decimal | None = None
    grm: Decimal | None = None
    breakeven_occupancy: Decimal | None = None
    net_profit: Decimal | None = None
    flip_margin: Decimal | None = None
    capital_left_in: Decimal | None = None


class CompAdjustment(BaseModel):
    """A line-item ±$ adjustment on a comp (§26.1), stored in `comp_members.adjustments`."""

    model_config = ConfigDict(extra="forbid")

    feature: str  # sqft | beds | baths | garage | lot | condition | pool
    amount: Decimal
    rationale: str | None = None


class EngineOutputBlock(BaseModel):
    """The full stored engine output per (property × strategy) (§26.9). `all_in`, the
    estimators, financing scenarios, pro-forma, and return metrics — plus the engine version
    and comp-set ids so any number renders its own derivation ("show the math", NFR-08).
    """

    model_config = ConfigDict(extra="allow")  # per-strategy sections extend this

    engine_version: str
    strategy: str
    arv: Interval | None = None
    as_is: Interval | None = None
    rent_ltr: Interval | None = None
    all_in: Decimal | None = None
    financing: list[FinancingScenario] = Field(default_factory=list)
    return_metrics: ReturnMetrics = Field(default_factory=ReturnMetrics)
    comp_set_ids: list[str] = Field(default_factory=list)
    rule_70_check: bool | None = None
