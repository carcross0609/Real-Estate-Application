"""The recurring-task timetable (Celery beat). Kept as plain data so the cadence policy is
reviewable in one place and unit-testable without a running beat.

Cadences map to the §9.5 continuous loop and each source tier's license limits (§14.2):
- `poll-due-sources` ticks every minute, but only *enqueues* the sources whose own cadence is
  due (the per-tier interval lives in `ingestion.scheduler.DEFAULT_CADENCES`). One cheap tick
  drives every feed at its licensed rate without a beat entry per source.
- `refresh-stale-valuations` sweeps daily — most staleness is event-driven (a comp closing
  re-triggers a property immediately via the fan-out); this is the backstop for estimates
  that simply aged out with no new event (03 §26.1 STALE_AGE).
- digests roll queued matches up hourly / daily for boxes whose latency isn't INSTANT.
- the scoring drift audit runs weekly (calibration is a slow signal; §25.1 #5).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

# Beat entries. `schedule` is seconds (timedelta) or a crontab; tasks take no args — each
# discovers its own work from the DB. Names must match the `@task(name=...)` registrations.
BEAT_SCHEDULE: dict[str, dict[str, Any]] = {
    "poll-due-sources": {
        "task": "deallens.ingestion.poll_due_sources",
        "schedule": timedelta(minutes=1),
    },
    "refresh-stale-valuations": {
        "task": "deallens.analysis.refresh_stale_valuations",
        "schedule": timedelta(days=1),
    },
    "run-hourly-digests": {
        "task": "deallens.alerts.run_digests",
        "schedule": timedelta(hours=1),
        "kwargs": {"latency": "hourly"},
    },
    "run-daily-digests": {
        "task": "deallens.alerts.run_digests",
        "schedule": timedelta(days=1),
        "kwargs": {"latency": "daily"},
    },
    "dispatch-pending-notifications": {
        "task": "deallens.alerts.dispatch_pending",
        "schedule": timedelta(minutes=2),
    },
    "scoring-drift-audit": {
        "task": "deallens.scoring.drift_audit",
        "schedule": timedelta(days=7),
    },
}

__all__ = ["BEAT_SCHEDULE"]
