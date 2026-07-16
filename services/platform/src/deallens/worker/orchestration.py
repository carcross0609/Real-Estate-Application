"""Cross-module automation chains (03 §9.4 the "→" between enrich→vision→engine→score→match).

Like `ingestion.pipeline` for the ingest side, this is orchestration, not a bounded context:
it composes the engine, scoring, and alerts *services* into the reactions the worker runs when
a listing changes or a valuation ages out. It lives in `worker/` (which may depend on every
module) so no module has to depend on another to react to its events — the module graph stays
acyclic (§9.3).

Each function is idempotent by construction (every underlying write is versioned/upsert), so a
duplicate or out-of-order event just recomputes to the same state (§9.5). Failures are the
task's to retry; these functions let exceptions propagate rather than half-swallowing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.enums import Strategy
from deallens.core.logging import get_logger
from deallens.modules.alerts import service as alerts_service
from deallens.modules.alerts.channels import NotificationDispatcher
from deallens.modules.engine import service as engine_service
from deallens.modules.scoring import service as scoring_service
from deallens.modules.scoring.models import Score

_log = get_logger("worker.orchestration")

# A watched property's score has to move by at least this many calibrated points before we
# interrupt the watcher — smaller wobble is noise from a comp refresh, not news (§25.1 #4).
SCORE_CHANGE_MIN_DELTA = Decimal("3")


@dataclass(slots=True)
class AnalyzeResult:
    property_id: UUID
    scored: bool = False
    prior_score: Decimal | None = None
    new_score: Decimal | None = None
    boxes_matched: int = 0
    notifications: int = 0


async def _latest_overall(db: AsyncSession, property_id: UUID) -> Score | None:
    return await scoring_service.get_score(db, property_id=property_id, strategy=Strategy.OVERALL)


async def analyze_and_score(
    db: AsyncSession,
    *,
    property_id: UUID,
    notify_watchers_on_score_change: bool = False,
    dispatcher: NotificationDispatcher | None = None,
) -> AnalyzeResult:
    """The full opportunity pass for one property: (re)value → underwrite strategies → score →
    match buy boxes. This is what a new or updated listing triggers — it's how a "new investment
    opportunity" gets found and surfaced. Optionally notifies watchers when the recompute moved
    the score materially (used on a listing *update*, not a first analysis where there's no
    prior score to compare)."""
    result = AnalyzeResult(property_id=property_id)

    prior = await _latest_overall(db, property_id) if notify_watchers_on_score_change else None
    result.prior_score = prior.score if prior else None

    # Deterministic engine owns every dollar figure (valuation → per-strategy underwriting).
    await engine_service.value_property(db, property_id=property_id)
    await engine_service.analyze_strategies(db, property_id=property_id)

    score_result = await scoring_service.score_property(db, property_id=property_id)
    result.scored = True
    result.new_score = score_result.overall_score

    if (
        notify_watchers_on_score_change
        and result.prior_score is not None
        and abs(result.new_score - result.prior_score) >= SCORE_CHANGE_MIN_DELTA
    ):
        await alerts_service.notify_watchers(
            db,
            property_id=property_id,
            change="score_change",
            detail={"from": str(result.prior_score), "to": str(result.new_score)},
            dispatcher=dispatcher,
        )

    match = await alerts_service.match_property(
        db, property_id=property_id, dispatcher=dispatcher
    )
    result.boxes_matched = match.boxes_matched
    result.notifications = match.notifications_created
    _log.info(
        "analyze_and_score",
        property_id=str(property_id),
        score=str(result.new_score),
        boxes_matched=result.boxes_matched,
        notifications=result.notifications,
    )
    return result


async def notify_listing_change(
    db: AsyncSession,
    *,
    property_id: UUID,
    event_types: list[str],
    dispatcher: NotificationDispatcher | None = None,
) -> int:
    """Fan a listing's typed changes out to its watchers (FR-041). Price and status changes are
    the ones an investor tracking a property wants instantly; other change types (remarks,
    photos) don't warrant an interrupt on their own."""
    total = 0
    if "price_change" in event_types:
        total += await alerts_service.notify_watchers(
            db, property_id=property_id, change="price_change", dispatcher=dispatcher
        )
    if "status_change" in event_types:
        total += await alerts_service.notify_watchers(
            db, property_id=property_id, change="status_change", dispatcher=dispatcher
        )
    return total


async def refresh_if_stale(
    db: AsyncSession, *, property_id: UUID, dispatcher: NotificationDispatcher | None = None
) -> AnalyzeResult | None:
    """The staleness backstop (03 §26.1): recompute a property's valuation only if the policy
    fires (new comp, aged estimate, engine-version bump). When it does recompute, re-score and
    re-match so the refreshed number propagates to rankings and alerts. No-op (returns None)
    when nothing was stale — the common case for the daily sweep."""
    fresh = await engine_service.recompute_if_stale(db, property_id=property_id)
    if fresh is None:
        return None
    result = AnalyzeResult(property_id=property_id)
    prior = await _latest_overall(db, property_id)
    result.prior_score = prior.score if prior else None
    score_result = await scoring_service.score_property(db, property_id=property_id)
    result.scored = True
    result.new_score = score_result.overall_score
    if (
        result.prior_score is not None
        and abs(result.new_score - result.prior_score) >= SCORE_CHANGE_MIN_DELTA
    ):
        await alerts_service.notify_watchers(
            db, property_id=property_id, change="score_change",
            detail={"from": str(result.prior_score), "to": str(result.new_score)},
            dispatcher=dispatcher,
        )
    match = await alerts_service.match_property(db, property_id=property_id, dispatcher=dispatcher)
    result.boxes_matched = match.boxes_matched
    result.notifications = match.notifications_created
    return result


__all__ = [
    "AnalyzeResult",
    "SCORE_CHANGE_MIN_DELTA",
    "analyze_and_score",
    "notify_listing_change",
    "refresh_if_stale",
]
