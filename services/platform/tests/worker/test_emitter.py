"""CeleryEmitter routing tests (no broker) — each ingestion domain event maps to the right
consumer task with a JSON-safe payload (§9.4 outbox seam). A fake app records dispatches so the
routing table is asserted without Redis.
"""

from typing import Any
from uuid import uuid4

from deallens.modules.ingestion.events import (
    ListingChanged,
    ListingUpserted,
    PhotosChanged,
    PropertyResolved,
)
from deallens.worker.emitter import CeleryEmitter


class _FakeApp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def send_task(self, name: str, kwargs: dict[str, Any] | None = None) -> None:
        self.sent.append((name, kwargs or {}))


async def test_listing_upserted_routes_and_stringifies_ids() -> None:
    app = _FakeApp()
    emitter = CeleryEmitter(app)  # type: ignore[arg-type]
    pid, lid = uuid4(), uuid4()
    await emitter.emit(
        ListingUpserted(listing_id=lid, property_id=pid, source_key="reso", is_new=True)
    )
    assert len(app.sent) == 1
    name, kwargs = app.sent[0]
    assert name == "deallens.pipeline.on_listing_upserted"
    assert kwargs == {"property_id": str(pid), "listing_id": str(lid), "is_new": True}


async def test_listing_changed_carries_event_types() -> None:
    app = _FakeApp()
    emitter = CeleryEmitter(app)  # type: ignore[arg-type]
    pid, lid = uuid4(), uuid4()
    await emitter.emit(
        ListingChanged(
            listing_id=lid, property_id=pid, event_types=["price_change", "status_change"]
        )
    )
    name, kwargs = app.sent[0]
    assert name == "deallens.pipeline.on_listing_changed"
    assert kwargs["event_types"] == ["price_change", "status_change"]


async def test_photos_changed_routes() -> None:
    app = _FakeApp()
    emitter = CeleryEmitter(app)  # type: ignore[arg-type]
    lid = uuid4()
    await emitter.emit(PhotosChanged(listing_id=lid, added=3, removed=1))
    name, kwargs = app.sent[0]
    assert name == "deallens.pipeline.on_photos_changed"
    assert kwargs == {"listing_id": str(lid), "added": 3, "removed": 1}


async def test_new_property_routes_but_reresolve_is_silent() -> None:
    app = _FakeApp()
    emitter = CeleryEmitter(app)  # type: ignore[arg-type]
    pid = uuid4()
    await emitter.emit(
        PropertyResolved(property_id=pid, address_key="k", confidence=0.9, is_new=True)
    )
    await emitter.emit(
        PropertyResolved(property_id=pid, address_key="k", confidence=0.9, is_new=False)
    )
    assert [n for n, _ in app.sent] == ["deallens.pipeline.on_property_resolved"]


async def test_broker_failure_is_swallowed() -> None:
    class _Boom:
        def send_task(self, name: str, kwargs: dict[str, Any] | None = None) -> None:
            raise RuntimeError("broker down")

    emitter = CeleryEmitter(_Boom())  # type: ignore[arg-type]
    # A broker blip must not raise into the ingestion cycle that produced the event.
    await emitter.emit(PhotosChanged(listing_id=uuid4(), added=1, removed=0))
