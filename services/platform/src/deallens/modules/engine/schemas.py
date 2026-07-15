"""Validation models for engine JSONB payloads (03 §26). `AssumptionSet` is the versioned
default set snapshotted into every `analyses.assumption_set`; `EngineOutputBlock` is the
full output stored in `analyses.outputs` / `scenarios.outputs` (§26.9 traceability). Money
is `Decimal` end-to-end (§20) — never float, so no rounding drift in stored finance.

The comparable-analysis block at the bottom (`CompSelectionParams`, `AdjustmentConfig`, and
the `*Out` response shapes) is the boundary for the comps & valuation engine (03 §26.1,
§28): what the ARV / as-is / market-rent estimators consume as config and hand back as
point-plus-interval estimates. `CompSelectionParams`/`AdjustmentConfig` are versioned config
defaults (never hardcodes — 03 preamble), snapshotted into `comp_sets.params` and the
`valuations.method` trail so any number renders its own derivation (§26.9).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    stabilization_months: int = 2  # rental lease-up carry before cash flow starts (§26.5)
    hold_years: int = 5  # horizon for the equity/IRR view (§26.5)

    # --- Financing scenario table (§26.3) ---
    # The standard ≥3-scenario set. Rates are FRED/OBMMI-ingested daily in production (a stale
    # manual table is a silent accuracy bug across every analysis — §26.3); these are the
    # versioned config defaults the ingested values overwrite, never hardcodes.
    conventional_rate: Decimal = Decimal("0.07")
    conventional_down_pct: Decimal = Decimal("0.20")
    conventional_term_years: int = 30
    dscr_rate: Decimal = Decimal("0.0775")
    dscr_down_pct: Decimal = Decimal("0.25")
    dscr_term_years: int = 30
    dscr_min: Decimal = Decimal("1.20")  # min DSCR the refi/investor loan must clear (§26.7)
    hard_money_rate: Decimal = Decimal("0.115")
    hard_money_down_pct: Decimal = Decimal("0.10")
    hard_money_points: Decimal = Decimal("0.02")
    hard_money_term_months: int = 12


class Interval(BaseModel):
    """A point estimate with its P10/P50/P90 band (§26.6 — show the band, no false
    precision). Ranking uses the conservative quantile (§25.8), the UI shows p50 + band."""

    model_config = ConfigDict(extra="forbid")

    point: Decimal
    p10: Decimal | None = None
    p50: Decimal | None = None
    p90: Decimal | None = None


class CalcLine(BaseModel):
    """One line in a calculation's derivation ledger — the atom of "show the math" (§26.9,
    NFR-08). `amount` is a signed contribution (an expense is negative where it nets against an
    income line); `note` carries the formula/basis so the UI renders the derivation, not just
    the number. Ledgers are attached per section (acquisition, pro-forma, returns) rather than
    one flat trace, so every figure points at exactly the lines that produced it.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    amount: Decimal
    note: str | None = None


class FinancingScenario(BaseModel):
    """One of the ≥3 financing scenarios computed per property (§26.3). Carries the loan sizing,
    the amortizing payment, and the cash/loan costs so the analyzer can compare scenarios on
    both monthly payment *and* cash-to-close — the two questions an investor actually asks.
    """

    model_config = ConfigDict(extra="forbid")

    label: str  # conventional | dscr | hard_money | cash
    down_pct: Decimal
    rate: Decimal
    term_years: int | None = None
    points: Decimal = Decimal("0")
    interest_only: bool = False
    monthly_payment: Decimal | None = None
    loan_amount: Decimal | None = None
    down_payment: Decimal | None = None
    loan_costs: Decimal | None = None  # points + origination + fixed (§26.3)
    lines: list[CalcLine] = Field(default_factory=list)


class AcquisitionCosts(BaseModel):
    """The §26.2 acquisition + rehab + holding stack that rolls up to `all_in`. Every component
    is surfaced (not just the total) with its ledger, because "what's my all-in?" is the single
    most-disputed number in a deal and a hidden closing/holding assumption is an accuracy bug.
    """

    model_config = ConfigDict(extra="forbid")

    price: Decimal
    closing_costs_buy: Decimal
    rehab_base: Decimal
    rehab_contingency: Decimal
    rehab_total: Decimal
    holding_costs: Decimal
    all_in: Decimal
    lines: list[CalcLine] = Field(default_factory=list)


class OperatingProForma(BaseModel):
    """The §26.4 rental operating statement. Surfaces both `noi` (standard definition, excludes
    the capex reserve) and `noi_after_reserves` — labeled, because investors argue about which
    is "real" NOI and we refuse to pick a side silently (§26.4).
    """

    model_config = ConfigDict(extra="forbid")

    gross_potential_rent: Decimal
    vacancy: Decimal
    other_income: Decimal
    effective_gross_income: Decimal
    taxes: Decimal
    insurance: Decimal
    management: Decimal
    maintenance: Decimal
    capex_reserve: Decimal
    hoa: Decimal
    utilities: Decimal
    other_opex: Decimal
    operating_expenses: Decimal  # excludes capex reserve & debt (standard NOI basis)
    noi: Decimal
    noi_after_reserves: Decimal
    lines: list[CalcLine] = Field(default_factory=list)


class FiveYearProjection(BaseModel):
    """The §26.5 5-year equity/IRR view (Phase 2 in the roadmap; the model lands now against the
    appreciation the comps engine already produces). Simple, labeled, and honest: appreciation is
    user-cappable and the IRR is a plain annualized equity IRR, not a levered-fund fiction.
    """

    model_config = ConfigDict(extra="forbid")

    hold_years: int
    appreciation_pct: Decimal
    exit_value: Decimal
    equity_at_exit: Decimal
    total_cash_flow: Decimal
    equity_multiple: Decimal | None = None
    irr: Decimal | None = None
    lines: list[CalcLine] = Field(default_factory=list)


class RehabEstimate(BaseModel):
    """The rehab cost band the financial engine prices against (§26.2). Sourced from the vision
    rehab model (03 §27.4) or a user override; the low/mid/high propagate into the P10/P50/P90
    of every downstream profit number so the band is never fake-precise (§26.6).
    """

    model_config = ConfigDict(extra="forbid")

    low: Decimal = Decimal("0")
    mid: Decimal = Decimal("0")
    high: Decimal = Decimal("0")
    red_flags_present: bool = False  # → 20% contingency instead of 15% (§26.2)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not (self.low <= self.mid <= self.high):
            raise ValueError("require low <= mid <= high")
        return self


class FinancialInputs(BaseModel):
    """The property-specific facts a deterministic analysis run consumes (§26), decoupled from
    the ORM so the whole financial engine is unit-tested without a database. The service
    assembles this from the listing (price), the comps engine (ARV / as-is / rent), the vision
    rehab model (`rehab`), the tax/enrichment records (`annual_taxes`, `reassessed_on_sale`), and
    the market appreciation reading. Money is Decimal end-to-end (§20).
    """

    model_config = ConfigDict(extra="forbid")

    price: Decimal  # contract/list price the analysis underwrites against
    arv: Interval | None = None
    as_is: Interval | None = None
    market_rent_monthly: Decimal | None = None  # effective (post list-to-effective) monthly rent
    rehab: RehabEstimate = Field(default_factory=RehabEstimate)
    annual_taxes: Decimal | None = None
    reassessed_on_sale: bool = False  # tax reassessed to purchase price (§26.4 underwriting trap)
    annual_insurance: Decimal | None = None
    hoa_monthly: Decimal = Decimal("0")
    utilities_monthly: Decimal = Decimal("0")  # owner-paid, if any
    other_monthly_income: Decimal = Decimal("0")
    sqft: int | None = None
    appreciation_pct: Decimal | None = None  # market rate; falls back to the assumption default
    estimator_confidence: Decimal | None = None  # min band confidence, carried to the output


class ReturnMetrics(BaseModel):
    """§26.5 return metrics + strategy-specific outputs (flip margin, BRRRR capital-left)."""

    model_config = ConfigDict(extra="allow")

    cap_rate: Decimal | None = None
    cap_rate_arv: Decimal | None = None
    cash_flow_monthly: Decimal | None = None
    coc: Decimal | None = None
    dscr: Decimal | None = None
    grm: Decimal | None = None
    breakeven_occupancy: Decimal | None = None
    cash_invested: Decimal | None = None
    net_profit: Decimal | None = None
    flip_margin: Decimal | None = None
    roi_annualized: Decimal | None = None
    capital_left_in: Decimal | None = None
    lines: list[CalcLine] = Field(default_factory=list)


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
    acquisition: AcquisitionCosts | None = None
    financing: list[FinancingScenario] = Field(default_factory=list)
    proforma: OperatingProForma | None = None
    return_metrics: ReturnMetrics = Field(default_factory=ReturnMetrics)
    five_year: FiveYearProjection | None = None
    comp_set_ids: list[str] = Field(default_factory=list)
    rule_70_check: bool | None = None
    # Estimator confidence (min across the ARV/rent bands the analysis stood on) — a full
    # pro-forma built on a model-prior ARV is arithmetically exact but epistemically shaky, and
    # scoring's confidence factor (§25.5) must see that (never a false-precision green light).
    confidence: Decimal | None = None
    warnings: list[str] = Field(default_factory=list)


# --- Comparable sales & rental analysis (03 §26.1, §28) ---------------------------------


class CompSelectionParams(BaseModel):
    """The comp-discovery filter set (03 §26.1), config-versioned. Snapshotted verbatim into
    `comp_sets.params` so a set is reproducible and a user can see exactly which net was cast.

    Distances are miles (urban vs. rural radius, resolved per market before the query);
    `sqft_tolerance` is a fraction (0.25 = ±25%). The `min_comps` floor is what trips the
    fallback ladder (widen recency → widen radius → model-prior), and `target_comps` is the
    ideal set size that both caps selection and normalizes the confidence count-factor.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = "v1"
    radius_mi: Decimal = Field(default=Decimal("0.75"), gt=0)  # urban default (§26.1)
    radius_mi_rural: Decimal = Field(default=Decimal("3.0"), gt=0)
    recency_months: int = Field(default=6, ge=1)
    sqft_tolerance: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    beds_tolerance: int = Field(default=1, ge=0)
    same_property_type: bool = True
    min_comps: int = Field(default=3, ge=1)
    target_comps: int = Field(default=8, ge=1)
    max_comps: int = Field(default=15, ge=1)
    # Fallback-ladder ceilings — how far the widening steps may stretch before giving up on
    # comps and dropping to a model-based $/sqft prior (03 §26.1, confidence ↓ at each step).
    max_recency_months: int = Field(default=12, ge=1)
    max_radius_mi: Decimal = Field(default=Decimal("5.0"), gt=0)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.min_comps > self.target_comps or self.target_comps > self.max_comps:
            raise ValueError("require min_comps <= target_comps <= max_comps")
        if self.max_recency_months < self.recency_months:
            raise ValueError("max_recency_months must be >= recency_months")
        if self.max_radius_mi < self.radius_mi:
            raise ValueError("max_radius_mi must be >= radius_mi")
        return self


class AdjustmentConfig(BaseModel):
    """Line-item comp-adjustment values (03 §26.1 adjustment grid), versioned admin data —
    "every constant is a versioned config default, not a hardcode" (03 preamble). Market
    overrides (`markets.config["adjustments"]`) merge over these before a run.

    `per_sqft` is usually left None so the engine derives the marginal $/sqft from the comp
    set's own median (a Cleveland adjustment shouldn't use an Austin dollar figure), damped by
    `sqft_damping` because a marginal square foot is worth less than the average one — a 20%
    larger house is not worth 20% more. The rest are flat per-unit dollar deltas.
    """

    model_config = ConfigDict(extra="allow")  # market-specific line items extend this

    version: str = "v1"
    per_sqft: Decimal | None = None  # None → derive from comp-set median $/sqft × damping
    sqft_damping: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    bed_value: Decimal = Decimal("8000")
    bath_value: Decimal = Decimal("12000")
    garage_space_value: Decimal = Decimal("7000")
    per_lot_sqft: Decimal = Decimal("2.0")
    pool_value: Decimal = Decimal("20000")
    condition_grade_value: Decimal = Decimal("15000")  # per 1–5 grade step (as-is / ARV)
    # Grade a renovated ARV comp set is normalized *to* (03 §26.1 "renovated-condition comps").
    arv_target_grade: int = Field(default=4, ge=1, le=5)


class SelectedComp(BaseModel):
    """One comparable within a computed set, as served to the UI and mirrored into a
    `comp_members` row. `adjusted_value` is the comp's sale price (or asking rent) after the
    line-item grid moves it toward the subject; the weighted spread of these is the estimate.
    """

    model_config = ConfigDict(extra="forbid")

    property_id: UUID
    listing_id: UUID | None = None
    distance_m: Decimal | None = None
    similarity: Decimal
    observed_value: Decimal  # comp close price (sale) or asking rent (rental), pre-adjustment
    observed_date: date | None = None
    adjusted_value: Decimal
    adjustments: list[CompAdjustment] = Field(default_factory=list)
    included: bool = True
    excluded_reason: str | None = None


class ValuationOut(BaseModel):
    """A computed estimate served to the UI / stored as a `valuations` row (03 §26.1). Carries
    the full P10/P50/P90 band (never just the point — 03 §21.5/§25.8), the confidence, the
    method trail (comp-based | widened | model-prior), and the comp set behind it.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str  # arv | as_is | rent_ltr
    point: Decimal | None = None
    low: Decimal | None = None  # P10
    high: Decimal | None = None  # P90
    p50: Decimal | None = None
    confidence: Decimal | None = None
    method: str | None = None
    model_version: str = "v1"
    comp_set_id: UUID | None = None
    comp_count: int = 0
    comps: list[SelectedComp] = Field(default_factory=list)
    computed_at: datetime | None = None


class AppreciationOut(BaseModel):
    """Market appreciation for a property's finest available geography (03 §28.3/§28.4).
    `annual_pct` is a fraction (0.05 = 5%/yr); `geo_level` is labeled honestly — a metro-level
    rate carried down to a property is *labeled* metro, never faked to parcel precision (§28.2).
    """

    model_config = ConfigDict(extra="forbid")

    annual_pct: Decimal | None = None
    period_years: int = 1
    geo_level: str | None = None
    geo_id: str | None = None
    source: str | None = None
    confidence: Decimal | None = None
    as_of: date | None = None


class PropertyValuationOut(BaseModel):
    """The full comps-engine result bundle for one property: the three estimators plus market
    appreciation, each self-describing (03 §26.9). This is what the property page and the
    downstream analysis/scoring engines read.
    """

    model_config = ConfigDict(extra="forbid")

    property_id: UUID
    arv: ValuationOut | None = None
    as_is: ValuationOut | None = None
    rent_ltr: ValuationOut | None = None
    appreciation: AppreciationOut | None = None


class CompEditRequest(BaseModel):
    """A user pin/exclude edit over a system comp set (FR-015). Pins force-include a comp the
    engine dropped; excludes remove one with a reason. Applying it clones the system set into an
    org-owned set and recomputes — the shared estimate is never mutated by one tenant.
    """

    model_config = ConfigDict(extra="forbid")

    include_property_ids: list[UUID] = Field(default_factory=list)
    exclude_property_ids: list[UUID] = Field(default_factory=list)
    exclude_reason: str | None = Field(default=None, max_length=280)

    @model_validator(mode="after")
    def _non_empty_and_disjoint(self) -> Self:
        if not self.include_property_ids and not self.exclude_property_ids:
            raise ValueError("at least one pin or exclude is required")
        if set(self.include_property_ids) & set(self.exclude_property_ids):
            raise ValueError("a comp cannot be both pinned and excluded")
        return self
