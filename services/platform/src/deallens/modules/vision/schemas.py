"""Validation models for vision JSONB payloads (03 §27.2). Every LLM vision step returns
structured output validated against these before it is written to `photo_analyses` /
`property_conditions` (§13.2: schema-validation retry ≤2, then dead-letter). Grades are
1-5; confidences are 0-1.

The lower half (`PhotoPerception`, the rehab-cost config/output, `PropertyConditionOut`) is the
boundary for the vision *system* (03 §27): the pluggable model provider hands back a
`PhotoPerception` per photo; the deterministic aggregation rolls those into a property condition
+ a `RehabBreakdown` (the money output, §27.4). The perception schema is model-agnostic on
purpose (§13.4) — a newer vision model is a new provider producing the same `PhotoPerception`,
so nothing downstream changes when the model is upgraded.
"""

import enum
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RedFlagType(enum.StrEnum):
    """§27.2 structural/condition red-flag taxonomy — also feeds the Risk score (§25.5)."""

    FOUNDATION_CRACK = "foundation_crack"
    WATER_STAIN = "water_stain"
    MOLD_SUSPECT = "mold_suspect"
    ROOF_WEAR = "roof_wear"
    OUTDATED_PANEL = "outdated_panel"
    SAGGING_LINE = "sagging_line"
    PEST_DAMAGE = "pest_damage"
    HAZARD_OTHER = "hazard_other"


class RedFlagSeverity(enum.StrEnum):
    POSSIBLE = "possible"
    LIKELY = "likely"
    SEVERE = "severe"


class RedFlag(BaseModel):
    """One flagged condition finding (§27.2). `photo_evidence` is a bbox or short locator
    string — we estimate exposure, we don't diagnose (§27.4)."""

    model_config = ConfigDict(extra="forbid")

    type: RedFlagType
    severity: RedFlagSeverity
    photo_evidence: str | None = None


class PhotoQuality(BaseModel):
    """§27.2 quality block. `staged_or_virtual` is critical: virtually-staged photos
    systematically hide condition, so they are down-weighted in aggregation (§27.3)."""

    model_config = ConfigDict(extra="forbid")

    photo_usable: bool = True
    staged_or_virtual: bool = False
    wide_angle_distortion: bool = False


class PhotoFindings(BaseModel):
    """Per-photo structured observations (§27.2). Kept permissive (`extra="allow"`) because
    the observation set is room-type-specific (kitchen cabinets/counters/appliances; bath
    fixtures/tile; exterior siding/windows/roof) and versioned in `vision/prompts/`."""

    model_config = ConfigDict(extra="allow")

    room_confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    notes: str | None = None


class ConditionCoverage(BaseModel):
    """Which room classes had usable photos (§27.3). Drives property AI confidence and the
    degraded-state banner below 0.4 (§27.5)."""

    model_config = ConfigDict(extra="allow")

    photographed_classes: list[str] = Field(default_factory=list)
    missing_classes: list[str] = Field(default_factory=list)
    coverage_ratio: float = Field(ge=0.0, le=1.0, default=0.0)


# --- Model provider output (§27.2) ------------------------------------------------------


class PhotoPerception(BaseModel):
    """The model-agnostic per-photo result a vision provider returns (§27.2). This is the seam
    that makes model upgrades free (§13.4): whether the perception came from Claude, a future
    model, or the deterministic bootstrap provider, it is the *same* schema — the aggregation and
    rehab model downstream never know which model produced it. `model_id`/`pipeline_version`
    record provenance so `photo_analyses` can diff versions on the eval set before a swap ships.
    """

    model_config = ConfigDict(extra="forbid")

    room_type: str | None = None  # RoomType value; None = model couldn't classify
    condition_grade: int | None = Field(default=None, ge=1, le=5)
    findings: PhotoFindings = Field(default_factory=PhotoFindings)
    red_flags: list[RedFlag] = Field(default_factory=list)
    quality: PhotoQuality = Field(default_factory=PhotoQuality)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    model_id: str
    pipeline_version: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: Decimal | None = None


# --- Rehab cost model (§27.4) -----------------------------------------------------------


class CostBasis(enum.StrEnum):
    """How a system's cost scales (§27.4 `scale(sqft | count | lump)`)."""

    LUMP = "lump"  # a flat per-system figure (kitchen, HVAC)
    PER_SQFT = "per_sqft"  # scales with living area (flooring, paint)
    PER_BATH = "per_bath"  # scales with bath count


class UnitCost(BaseModel):
    """The per-system unit-cost row of the §27.4 table (versioned admin data — "unit_cost tables
    are market-versioned admin data", not a hardcode). `cost_per_grade_step` is the cosmetic cost
    to lift the system one 1–5 grade; `gut_cost` is the major cost when a system is grade ≤2 (a
    gut, not a refresh). `uncertainty` widens the low/high band as model confidence drops (§27.4).
    """

    model_config = ConfigDict(extra="forbid")

    basis: CostBasis
    cost_per_grade_step: Decimal  # scaled by basis (× sqft / × baths / ×1 for lump)
    gut_cost: Decimal = Decimal("0")  # major line when grade ≤2 (structural/system replacement)
    uncertainty: Decimal = Decimal("0.30")  # ± band at low confidence (§27.4)


class RehabCostConfig(BaseModel):
    """The §27.4 unit-cost table + contingency policy, config-versioned and market-overridable.
    Seeded from published cost indices; recalibrated from user actuals via the feedback flywheel
    (02 §13.5). Keys are system names (kitchen, bath, flooring, exterior, roof, landscaping)."""

    model_config = ConfigDict(extra="allow")

    version: str = "v1"
    systems: dict[str, UnitCost] = Field(default_factory=dict)
    contingency_pct: Decimal = Decimal("0.15")
    contingency_pct_flagged: Decimal = Decimal("0.20")
    # Major-item costs keyed by red-flag type (§27.4 — inspection-contingent ranges, labeled).
    red_flag_costs: dict[str, Decimal] = Field(default_factory=dict)


class RehabLineItem(BaseModel):
    """One system's rehab cost (§27.4), cosmetic or major, as a low/mid/high range. Red-flag
    items are inspection-contingent and flagged as such — we estimate exposure, we don't diagnose
    (§27.4)."""

    model_config = ConfigDict(extra="forbid")

    system: str
    category: str  # cosmetic | major
    from_grade: int | None = None
    to_grade: int | None = None
    low: Decimal
    mid: Decimal
    high: Decimal
    inspection_contingent: bool = False
    note: str | None = None


class RehabBreakdown(BaseModel):
    """The full §27.4 rehab estimate: cosmetic vs. major split, low/mid/high totals *including*
    contingency, and the per-system line items so "show the math" renders the derivation. This is
    the money output that feeds the financial engine's `RehabEstimate` (03 §26.2)."""

    model_config = ConfigDict(extra="forbid")

    cosmetic_low: Decimal
    cosmetic_high: Decimal
    major_low: Decimal
    major_high: Decimal
    contingency_pct: Decimal
    total_low: Decimal
    total_mid: Decimal
    total_high: Decimal
    red_flags_present: bool
    line_items: list[RehabLineItem] = Field(default_factory=list)


class ConditionFactors(BaseModel):
    """The Group-C scoring factors vision hands to the investment score (03 §25.3). Computed from
    the condition aggregate + rehab model + the comps engine's ARV, this is the contract the
    scoring engine reads — the seam that lets "what condition is it in?" flow into "how good a
    deal is it?" `condition_arbitrage` is the flip-goldmine factor: the equity available by curing
    condition (ARV − rehab − price), as a fraction of ARV."""

    model_config = ConfigDict(extra="forbid")

    condition_arbitrage: Decimal | None = None  # (ARV − rehab − price) / ARV; higher = better
    renovation_difficulty: int | None = None  # 1 (turnkey) … 5 (gut); inverse-scored for flip
    red_flag_severity: Decimal = Decimal("0")  # 0 (none) … 1 (severe structural)
    rehab_to_arv_ratio: Decimal | None = None  # execution risk (§25.5 Risk input)
    confidence: Decimal | None = None  # carried so the score's confidence inherits it (§27.5)


class PropertyConditionOut(BaseModel):
    """The property-level condition + rehab bundle served to the analyzer and read by scoring
    (§27.3). Grades are per-system medians (NULL = no usable photo, never a faked average);
    `confidence` below 0.4 drives the "estimate from limited photos" banner (§27.5)."""

    model_config = ConfigDict(extra="forbid")

    property_id: str
    pipeline_version: str
    kitchen_grade: int | None = None
    bath_grade: int | None = None
    flooring_grade: int | None = None
    exterior_grade: int | None = None
    roof_grade: int | None = None
    landscaping_grade: int | None = None
    renovation_difficulty: int | None = None
    red_flags: list[RedFlag] = Field(default_factory=list)
    coverage: ConditionCoverage = Field(default_factory=ConditionCoverage)
    confidence: Decimal | None = None
    rehab: RehabBreakdown | None = None
