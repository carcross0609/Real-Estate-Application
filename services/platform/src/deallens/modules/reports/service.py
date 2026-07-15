"""Reports module public interface (§9.3/§19, FR-060). Assembles the grounded `ReportContextPack`
from every engine's public interface (valuation, per-strategy analyses, condition, score, market),
composes the narrative, renders the exportable document, and persists a `reports` row.

Reports are org-scoped, RLS, soft-deleted (USER-WORK group, §11.2) — so generation runs on the
actor's request-scoped session: it *reads* shared engine data and *writes* the tenant's report.
The narrative generator and renderer are pure (`builder`/`render`); this file is the assembly +
persistence seam. `context_pack` is stored verbatim so a report re-renders identically and an
auditor sees exactly the numbers the prose was grounded in (§13.3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.errors import NotFoundError
from deallens.modules.engine import service as engine_service
from deallens.modules.engine.schemas import EngineOutputBlock
from deallens.modules.ingestion.models import Listing, Property
from deallens.modules.markets import service as markets_service
from deallens.modules.reports import builder, render
from deallens.modules.reports.models import Report, ReportStatus
from deallens.modules.reports.schemas import (
    PropertyFacts,
    ReportContextPack,
    ReportOut,
    ReportSection,
)
from deallens.modules.scoring import service as scoring_service
from deallens.modules.scoring import weights as scoring_weights
from deallens.modules.vision import service as vision_service

_MODEL_ID = "deterministic-v1"
_PROMPT_VERSION = "v1"


async def _facts(db: AsyncSession, property_id: UUID) -> PropertyFacts:
    prop = await db.get(Property, property_id)
    if prop is None:
        raise NotFoundError("Property not found")
    listing = await db.execute(
        select(Listing).where(Listing.property_id == property_id)
        .order_by(Listing.list_date.desc().nullslast()).limit(1)
    )
    lst = listing.scalars().first()
    addr = prop.address_norm or {}
    return PropertyFacts(
        property_id=str(property_id),
        address_line1=addr.get("line1"), city=addr.get("city"), state=addr.get("state"),
        zip=addr.get("zip"),
        property_type=prop.property_type.value if prop.property_type else None,
        beds=prop.beds, baths=float(prop.baths) if prop.baths is not None else None,
        sqft=prop.sqft, year_built=prop.year_built,
        list_price=(f"${lst.list_price:,.0f}" if lst and lst.list_price is not None else None),
    )


async def assemble_context_pack(db: AsyncSession, *, property_id: UUID) -> ReportContextPack:
    """Gather every engine output for a property into one grounded pack (§13.3). Each piece is
    best-effort: a property not yet analyzed for a strategy simply omits that analysis, and the
    narrative skips the sections it can't ground."""
    facts = await _facts(db, property_id)
    valuation = await engine_service.get_property_valuation(db, property_id=property_id)
    condition = await vision_service.get_property_condition(db, property_id=property_id)
    score = await scoring_service.score_property(db, property_id=property_id, persist=False)

    analyses: dict[str, EngineOutputBlock] = {}
    for strat in scoring_weights.scorable_strategies():
        block = await engine_service.get_analysis(db, property_id=property_id, strategy=strat)
        if block is not None:
            analyses[strat.value] = block

    prop = await db.get(Property, property_id)
    market = None
    if prop is not None and prop.market_id is not None:
        try:
            market = await markets_service.get_market_report(db, market_id=prop.market_id)
        except NotFoundError:
            market = None

    return ReportContextPack(
        facts=facts, valuation=valuation, score=score, condition=condition,
        analyses=analyses, market=market, generated_at=datetime.now(UTC),
    )


async def generate_report(
    db: AsyncSession,
    *,
    property_id: UUID,
    org_id: UUID,
    user_id: UUID | None = None,
    persist: bool = True,
) -> ReportOut:
    """Generate a full investment report for a property (FR-060): assemble the pack, compose the
    grounded narrative, render Markdown + HTML, and persist a `reports` row. Returns the rendered,
    exportable document."""
    pack = await assemble_context_pack(db, property_id=property_id)
    sections = builder.compose_narrative(pack)
    markdown = render.to_markdown(pack, sections)
    html = render.to_html(pack, sections)

    report_id: str | None = None
    if persist:
        report = Report(
            org_id=org_id, user_id=user_id, property_id=property_id, kind="property",
            status=ReportStatus.READY,
            context_pack=pack.model_dump(mode="json"),
            narrative={"sections": [s.model_dump() for s in sections]},
            model_id=_MODEL_ID, prompt_version=_PROMPT_VERSION,
            generated_at=pack.generated_at,
        )
        db.add(report)
        await db.flush()
        report_id = str(report.id)

    return ReportOut(
        report_id=report_id, property_id=str(property_id), status="ready", sections=sections,
        markdown=markdown, html=html, generated_at=pack.generated_at,
    )


async def get_report(db: AsyncSession, *, report_id: UUID) -> ReportOut:
    """Load a persisted report and re-render it from its stored context pack (§13.3 — identical on
    re-view). Raises `NotFoundError` (→ 404) for an unknown/deleted report."""
    report = await db.get(Report, report_id)
    if report is None or report.deleted_at is not None:
        raise NotFoundError("Report not found")
    sections = [
        ReportSection.model_validate(s) for s in report.narrative.get("sections", [])
    ]
    pack = ReportContextPack.model_validate(report.context_pack)
    return ReportOut(
        report_id=str(report.id), property_id=str(report.property_id), status=report.status.value,
        sections=sections, markdown=render.to_markdown(pack, sections),
        html=render.to_html(pack, sections), generated_at=report.generated_at,
    )


async def list_reports(db: AsyncSession, *, property_id: UUID) -> list[Report]:
    """The org's reports for a property (RLS-scoped by the session), newest first."""
    rows = await db.execute(
        select(Report).where(Report.property_id == property_id, Report.deleted_at.is_(None))
        .order_by(Report.created_at.desc())
    )
    return list(rows.scalars().all())


__all__ = [
    "assemble_context_pack",
    "generate_report",
    "get_report",
    "list_reports",
]
