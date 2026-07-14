"""Change-detection unit tests (no DB): the pure diff that produces the price/status history
(FR-003) and the photo set-diff (§14.4). Constructs bare ORM instances as value holders —
`diff_listing` only reads attributes, never touches a session.
"""

from decimal import Decimal

from deallens.modules.ingestion.models import Listing, ListingEventType, ListingStatus
from deallens.modules.ingestion.schemas import (
    AddressNorm,
    ListingDelta,
    PhotoDelta,
    PropertyDelta,
)
from deallens.modules.ingestion.writer import diff_listing, diff_photos


def _delta(**over: object) -> ListingDelta:
    base: dict[str, object] = dict(
        source_listing_key="K1",
        property=PropertyDelta(
            address=AddressNorm(line1="1 A ST", city="X", state="TX", zip="78701")
        ),
        status=ListingStatus.ACTIVE,
        list_price=Decimal("300000"),
    )
    base.update(over)
    return ListingDelta(**base)  # type: ignore[arg-type]


def test_price_change_detected() -> None:
    old = Listing(status=ListingStatus.ACTIVE, list_price=Decimal("320000"), remarks="x")
    changes = diff_listing(old, _delta(list_price=Decimal("300000"), remarks="x"))
    assert [c.event_type for c in changes] == [ListingEventType.PRICE_CHANGE]
    assert changes[0].old == {"list_price": "320000"}
    assert changes[0].new == {"list_price": "300000"}


def test_no_change_is_empty() -> None:
    old = Listing(status=ListingStatus.ACTIVE, list_price=Decimal("300000"), remarks="x")
    assert diff_listing(old, _delta(remarks="x")) == []


def test_status_change_and_back_on_market() -> None:
    old = Listing(status=ListingStatus.WITHDRAWN, list_price=Decimal("300000"), remarks="x")
    changes = diff_listing(old, _delta(status=ListingStatus.ACTIVE, remarks="x"))
    types = [c.event_type for c in changes]
    assert ListingEventType.STATUS_CHANGE in types
    assert ListingEventType.BACK_ON_MARKET in types


def test_status_change_without_back_on_market() -> None:
    old = Listing(status=ListingStatus.ACTIVE, list_price=Decimal("300000"), remarks="x")
    changes = diff_listing(old, _delta(status=ListingStatus.PENDING, remarks="x"))
    types = [c.event_type for c in changes]
    assert types == [ListingEventType.STATUS_CHANGE]


def test_remarks_change_detected() -> None:
    old = Listing(status=ListingStatus.ACTIVE, list_price=Decimal("300000"), remarks="old text")
    changes = diff_listing(old, _delta(remarks="new text"))
    assert [c.event_type for c in changes] == [ListingEventType.REMARKS_CHANGE]


def test_diff_photos_set_semantics() -> None:
    existing = {b"a", b"b"}
    incoming = [
        PhotoDelta(content_hash=b"b"),
        PhotoDelta(content_hash=b"c"),
    ]
    added, removed = diff_photos(existing, incoming)
    assert [p.content_hash for p in added] == [b"c"]
    assert removed == {b"a"}
