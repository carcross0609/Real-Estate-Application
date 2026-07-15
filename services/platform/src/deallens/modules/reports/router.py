"""Reports HTTP surface — the property report + export (S12/S15, FR-060). All endpoints run on the
actor's RLS session: reports are org-scoped (§11.2), so tenant isolation is Postgres's job.
Generation reads shared engine data and writes the tenant's `reports` row; export re-renders the
stored, grounded report to a downloadable Markdown/HTML file.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.auth import Actor, get_actor_db, get_current_actor
from deallens.modules.reports import service
from deallens.modules.reports.schemas import ReportOut

router = APIRouter(tags=["reports"])


@router.post("/properties/{property_id}/report", response_model=ReportOut)
async def generate_report(
    property_id: UUID,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> ReportOut:
    """Generate a comprehensive investment report for a property (FR-060): opportunity, supporting
    calculations, comparables, risks, renovation estimate, financial projections, recommended
    strategies, and exit scenarios — grounded in the engine outputs (§13.3) and exportable."""
    return await service.generate_report(
        db, property_id=property_id, org_id=actor.require_org(), user_id=actor.user_id
    )


@router.get("/reports/{report_id}", response_model=ReportOut)
async def get_report(
    report_id: UUID,
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> ReportOut:
    """Fetch a persisted report, re-rendered from its stored context pack (identical on re-view)."""
    return await service.get_report(db, report_id=report_id)


@router.get("/reports/{report_id}/export", response_model=None)
async def export_report(
    report_id: UUID,
    format: str = Query(default="html", pattern="^(html|markdown)$"),
    _actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> HTMLResponse | PlainTextResponse:
    """Download the report as a professionally-formatted, self-contained HTML file or as Markdown
    (FR-060 export). The HTML is PDF-ready (a headless renderer turns it into the cached PDF)."""
    report = await service.get_report(db, report_id=report_id)
    if format == "markdown":
        return PlainTextResponse(
            report.markdown or "",
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="report-{report_id}.md"'},
        )
    return HTMLResponse(
        report.html or "",
        headers={"Content-Disposition": f'inline; filename="report-{report_id}.html"'},
    )
