"""Registry, event-emitter, and scheduler unit tests (no DB). These cover the seams that make
the pipeline modular (adapter registry, NFR-12), observable (emitters), and periodic
(cadence) — the parts that must behave predictably for a new source to plug in cleanly.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from deallens.core.errors import NotFoundError
from deallens.modules.ingestion.adapters.base import AdapterConfig, FixtureTransport
from deallens.modules.ingestion.adapters.registry import (
    available_sources,
    build_adapter,
    is_registered,
    register_adapter,
)
from deallens.modules.ingestion.events import (
    CollectingEmitter,
    CompositeEmitter,
    ListingUpserted,
)
from deallens.modules.ingestion.models import DataSourceTier
from deallens.modules.ingestion.scheduler import cadence_for, next_run_due

# --- registry --------------------------------------------------------------------------


def test_builtin_adapters_registered() -> None:
    assert {"mls_grid", "trestle", "bridge", "attom"} <= set(available_sources())


def test_build_unknown_source_raises_notfound() -> None:
    cfg = AdapterConfig(source_id=uuid4(), transport=FixtureTransport({}))
    with pytest.raises(NotFoundError):
        build_adapter("no_such_source", cfg)


def test_duplicate_registration_rejected() -> None:
    with pytest.raises(ValueError, match="already registered"):
        # Registration raises before the factory is ever called; reuse an existing builder so
        # the decorated callable satisfies the AdapterFactory type.
        register_adapter("mls_grid")(lambda config: build_adapter("attom", config))


def test_is_registered() -> None:
    assert is_registered("attom") is True
    assert is_registered("zzz") is False


# --- events ----------------------------------------------------------------------------


class _BoomEmitter:
    async def emit(self, event: object) -> None:
        raise RuntimeError("downstream is down")


@pytest.mark.asyncio
async def test_collecting_emitter_records() -> None:
    emitter = CollectingEmitter()
    ev = ListingUpserted(
        listing_id=uuid4(), property_id=uuid4(), source_key="mls_grid", is_new=True
    )
    await emitter.emit(ev)
    assert emitter.events == [ev]


@pytest.mark.asyncio
async def test_composite_emitter_isolates_failures() -> None:
    good = CollectingEmitter()
    composite = CompositeEmitter(_BoomEmitter(), good)
    ev = ListingUpserted(
        listing_id=uuid4(), property_id=uuid4(), source_key="mls_grid", is_new=True
    )
    # One emitter throwing must not stop the others or bubble up into ingestion.
    await composite.emit(ev)
    assert good.events == [ev]


# --- scheduler -------------------------------------------------------------------------


def test_cadence_defaults_and_override() -> None:
    assert cadence_for(DataSourceTier.MLS) == 300
    assert cadence_for(DataSourceTier.PROPERTY_DATA) == 86_400
    assert cadence_for(DataSourceTier.MLS, override_seconds=900) == 900


def test_next_run_due() -> None:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    assert next_run_due(300, None, now) is True  # never run
    assert next_run_due(300, now - timedelta(seconds=301), now) is True
    assert next_run_due(300, now - timedelta(seconds=120), now) is False
