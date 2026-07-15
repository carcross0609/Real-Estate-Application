"""Report generation contracts (§11.3, FR-060, §13.3). The `ReportContextPack` is the **grounded**
structured input the narrative generator sees — every number in the prose must trace to a field
here, so a report is reproducible and no unsourced figure can appear (§13.3). It is a bundle of the
other engines' typed outputs (valuation, score, condition, per-strategy analyses, market), stored
verbatim in `reports.context_pack`.

The narrative is a list of `ReportSection`s (section id → grounded markdown), stored in
`reports.narrative`; `render.py` turns the pack + sections into the exportable HTML/Markdown
document.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from deallens.modules.engine.schemas import EngineOutputBlock, PropertyValuationOut
from deallens.modules.markets.schemas import MarketReportOut
from deallens.modules.scoring.schemas import ScoreResult
from deallens.modules.vision.schemas import PropertyConditionOut


class PropertyFacts(BaseModel):
    """The headline physical facts for the report header (from the canonical property/listing)."""

    model_config = ConfigDict(extra="forbid")

    property_id: str
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    property_type: str | None = None
    beds: int | None = None
    baths: float | None = None
    sqft: int | None = None
    year_built: int | None = None
    list_price: str | None = None  # formatted for display; the Decimal lives in analyses/valuation


class ReportContextPack(BaseModel):
    """The complete grounded input set for one property's report (§13.3). Bundles the engine
    outputs the narrative describes; stored so the report renders identically on re-view and an
    auditor sees exactly what the generator saw."""

    model_config = ConfigDict(extra="forbid")

    facts: PropertyFacts
    valuation: PropertyValuationOut | None = None
    score: ScoreResult | None = None
    condition: PropertyConditionOut | None = None
    analyses: dict[str, EngineOutputBlock] = Field(default_factory=dict)  # strategy → block
    market: MarketReportOut | None = None
    engine_version: str = "v1"
    generated_at: datetime | None = None


class ReportSection(BaseModel):
    """One section of the report: a stable id, a display title, and grounded markdown body."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    body_markdown: str


class ReportOut(BaseModel):
    """A generated report served to the UI / export surfaces (FR-060). `sections` is the narrative;
    `markdown`/`html` are the rendered, exportable document (professionally formatted)."""

    model_config = ConfigDict(extra="forbid")

    report_id: str | None = None
    property_id: str
    status: str = "ready"
    sections: list[ReportSection] = Field(default_factory=list)
    markdown: str | None = None
    html: str | None = None
    generated_at: datetime | None = None


__all__ = [
    "PropertyFacts",
    "ReportContextPack",
    "ReportOut",
    "ReportSection",
]
