"""Continuous-improvement policy — pure (03 §26.1 fallback ladder, §9.5 event-driven refresh).

A valuation is never "done": as new comparable sales close, as market stats roll to a new
period, and as the estimator itself is versioned up, a property's ARV / as-is / rent estimate
should be recomputed against the better information. This module is the *decision* — should we
recompute, and why — kept pure so the trigger policy is testable without a database. The
service performs the recompute; the ingestion/enrichment event stream (a nearby listing going
SOLD, a `market_stats` upsert) is what calls in.

The "why" is preserved: `RecomputeDecision.reasons` is what the service logs and what a future
drift audit reads to distinguish "refreshed because a real new comp landed" from "refreshed
because it aged out" — the two mean very different things for accuracy tracking (PRD §4.3).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

# A comp-based estimate goes stale even with no new comp: the market moves, the recency window
# slides, so a set that was "≤6 months old" silently becomes "≤7 months old". Refresh monthly.
DEFAULT_MAX_AGE_DAYS = 30


class RecomputeReason(enum.StrEnum):
    NO_VALUATION = "no_valuation"  # never valued — always compute
    MODEL_VERSION_CHANGE = "model_version_change"  # estimator shipped a new version
    NEW_COMP = "new_comp"  # a newer comparable event landed since we last valued
    STALE_AGE = "stale_age"  # the estimate has simply aged past the refresh window


@dataclass(frozen=True, slots=True)
class RecomputeDecision:
    should_recompute: bool
    reasons: tuple[RecomputeReason, ...]

    def __bool__(self) -> bool:
        return self.should_recompute


def needs_recompute(
    *,
    latest_computed_at: datetime | None,
    latest_model_version: str | None,
    current_model_version: str,
    newest_comp_event_at: datetime | None,
    now: datetime,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> RecomputeDecision:
    """Decide whether a property's valuation should be recomputed, and collect every reason.

    Fires when: there is no prior valuation; the estimator version changed under it; a
    comparable event (a nearby sale closing, a stats period rolling) is newer than the last
    computation; or the estimate has aged past `max_age_days`. Reasons accumulate — a stale
    estimate that *also* has a fresh comp records both, so the log tells the whole story.

    Any comp event with a timestamp exactly equal to the last computation is treated as already
    incorporated (`>` not `>=`) — so an idempotent re-trigger on the same event doesn't loop.
    """
    reasons: list[RecomputeReason] = []

    if latest_computed_at is None:
        return RecomputeDecision(True, (RecomputeReason.NO_VALUATION,))

    if latest_model_version != current_model_version:
        reasons.append(RecomputeReason.MODEL_VERSION_CHANGE)

    if newest_comp_event_at is not None and newest_comp_event_at > latest_computed_at:
        reasons.append(RecomputeReason.NEW_COMP)

    if now - latest_computed_at >= timedelta(days=max_age_days):
        reasons.append(RecomputeReason.STALE_AGE)

    return RecomputeDecision(bool(reasons), tuple(reasons))


__all__ = [
    "DEFAULT_MAX_AGE_DAYS",
    "RecomputeDecision",
    "RecomputeReason",
    "needs_recompute",
]
