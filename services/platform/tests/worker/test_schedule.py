"""Beat schedule sanity (no broker) — every recurring task the automation promises is present,
well-formed, and named consistently with its registration convention.
"""

from datetime import timedelta

from celery.schedules import crontab

from deallens.worker.schedule import BEAT_SCHEDULE

_EXPECTED = {
    "poll-due-sources",
    "refresh-stale-valuations",
    "run-hourly-digests",
    "run-daily-digests",
    "dispatch-pending-notifications",
    "scoring-drift-audit",
}


def test_all_recurring_jobs_present() -> None:
    assert set(BEAT_SCHEDULE) >= _EXPECTED


def test_entries_are_well_formed() -> None:
    for name, entry in BEAT_SCHEDULE.items():
        assert entry["task"].startswith("deallens."), name
        assert isinstance(entry["schedule"], timedelta | crontab), name


def test_digest_entries_pass_latency_kwarg() -> None:
    assert BEAT_SCHEDULE["run-hourly-digests"]["kwargs"] == {"latency": "hourly"}
    assert BEAT_SCHEDULE["run-daily-digests"]["kwargs"] == {"latency": "daily"}
