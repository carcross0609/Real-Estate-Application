"""Scoring drift audit — "retrain the scoring model where appropriate", done honestly (§25.1
#5, PRD §4.3).

The scorer is a deterministic weighted engine over versioned config curves, not a trained
model, so this task does not fit a new model online. It assembles realized outcomes, measures
how the calibrated score tracked them (`scoring.calibration`), and — when drift is past
tolerance — *records a recalibration recommendation* for the scoring maintainers. It never
mutates live weights: weight/curve changes ship like code, eval-gated and version-bumped. An
online loop silently rewriting the weights is exactly what that rule prevents.

Realized-outcome proxy (v1): until a first-class outcomes table exists, the audit derives a
0–100 realized-quality score for each SOLD listing from how the sale actually went — sold
at/above ask and quickly ⇒ high; deep price cut and long on market ⇒ low — and compares it to
the property's overall score. It's an imperfect stand-in (it isn't the investor's realized
return), and it's labeled as such in the log; the calibration math it feeds is exact and tested.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from deallens.core.enums import Strategy
from deallens.core.logging import get_logger
from deallens.modules.ingestion.models import Listing, ListingStatus
from deallens.modules.scoring import calibration
from deallens.modules.scoring.models import Score
from deallens.worker.app import celery_app
from deallens.worker.runtime import run_async, task_session

_log = get_logger("worker.tasks.scoring")

_SAMPLE_LIMIT = 2000


def _realized_score(
    close_price: Decimal | None, list_price: Decimal | None, dom: int | None
) -> Decimal | None:
    """A bounded 0–100 realized-quality proxy from sale outcome. Centered at 50; a sale premium
    over ask pushes up, a discount pushes down (±30), and time-on-market adjusts ±20 around a
    ~45-day norm. Returns None when the sale lacks the prices to judge it."""
    if close_price is None or list_price is None or list_price == 0:
        return None
    premium = (close_price / list_price) - Decimal(1)
    premium_points = max(Decimal("-30"), min(Decimal("30"), premium * Decimal("300")))
    dom_points = Decimal(0)
    if dom is not None:
        # Faster than ~45 days is a good sign; slower is a drag. Clamped to ±20.
        raw = (Decimal(45) - Decimal(dom)) / Decimal(3)
        dom_points = max(Decimal("-20"), min(Decimal("20"), raw))
    return max(Decimal(0), min(Decimal(100), Decimal(50) + premium_points + dom_points))


async def _collect_samples(db: Any) -> list[calibration.OutcomeSample]:
    """Pair each SOLD listing's realized proxy with its property's overall predicted score."""
    rows = await db.execute(
        select(Listing.property_id, Listing.close_price, Listing.list_price, Listing.dom_current)
        .where(Listing.status == ListingStatus.SOLD, Listing.close_price.is_not(None))
        .limit(_SAMPLE_LIMIT)
    )
    samples: list[calibration.OutcomeSample] = []
    for property_id, close_price, list_price, dom in rows.all():
        realized = _realized_score(close_price, list_price, dom)
        if realized is None:
            continue
        score = (
            await db.execute(
                select(Score.score)
                .where(Score.property_id == property_id, Score.strategy == Strategy.OVERALL)
                .order_by(Score.computed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if score is None:
            continue
        samples.append(calibration.OutcomeSample(predicted=score, realized=realized))
    return samples


@celery_app.task(name="deallens.scoring.drift_audit")
def drift_audit() -> dict[str, object]:
    """Weekly: measure score-vs-outcome drift and record a recalibration recommendation when
    it's past tolerance. Logs the decision; the maintainers act on it (weights ship like code)."""
    async def _run() -> dict[str, object]:
        async with task_session() as db:
            samples = await _collect_samples(db)
        report = calibration.drift_report(samples)
        decision = calibration.should_recalibrate(report)
        _log.info(
            "scoring_drift_audit",
            n=report.n,
            mean_error=str(report.mean_error),
            mae=str(report.mae),
            concordance=str(report.concordance),
            severity=decision.severity.value,
            should_recalibrate=decision.should_recalibrate,
            reasons=list(decision.reasons),
            proxy="v1_sale_outcome",  # realized score is a proxy — see module docstring
        )
        return {
            "samples": report.n,
            "severity": decision.severity.value,
            "should_recalibrate": decision.should_recalibrate,
            "mean_error": str(report.mean_error),
            "concordance": str(report.concordance),
            "reasons": list(decision.reasons),
        }

    return run_async(_run())
