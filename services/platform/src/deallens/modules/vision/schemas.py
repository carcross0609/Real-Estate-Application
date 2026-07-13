"""Validation models for vision JSONB payloads (03 §27.2). Every LLM vision step returns
structured output validated against these before it is written to `photo_analyses` /
`property_conditions` (§13.2: schema-validation retry ≤2, then dead-letter). Grades are
1-5; confidences are 0-1.
"""

import enum

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
