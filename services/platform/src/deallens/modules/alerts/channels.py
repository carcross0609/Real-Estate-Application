"""Notification delivery — the channel seam (§10: Resend email / Web Push / Twilio SMS /
in-app SSE feed).

Like the other external-provider boundaries in the platform (the blob store, the vision
provider), delivery is a `Protocol` with a keyless default: `LoggingDispatcher` records the
send and reports success, so the whole alerting path — match → persist notification → dispatch
→ mark sent — runs end to end in dev and in tests without a Resend/Twilio account. The real
per-channel transports slot in behind `NotificationDispatcher` without touching the service.

`in_app` needs no external transport: the notification row *is* the in-app feed item (the
`/v1/notifications` read + SSE), so it is always considered delivered on persist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from deallens.core.logging import get_logger
from deallens.modules.alerts.models import AlertChannel

_log = get_logger("alerts.channels")


@dataclass(frozen=True, slots=True)
class Delivery:
    """The outcome of one send attempt. `provider_id` is the transport's message id (Resend
    id, Twilio SID) for later delivery-status reconciliation; None for in-app."""

    delivered: bool
    provider_id: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """What a dispatcher needs to render/send, independent of the ORM row."""

    channel: AlertChannel
    to: str  # email address, phone, or user id for in-app
    title: str
    body: str | None


@runtime_checkable
class NotificationDispatcher(Protocol):
    async def send(self, message: OutboundMessage) -> Delivery: ...


class LoggingDispatcher:
    """Keyless default: logs and reports delivered. The always-available transport so the
    alerting pipeline is exercised without external credentials (mirrors the vision/blob
    stubs). in-app is a no-op success by construction."""

    async def send(self, message: OutboundMessage) -> Delivery:
        _log.info(
            "notification_dispatch",
            channel=message.channel.value,
            to=message.to,
            title=message.title,
        )
        return Delivery(delivered=True, provider_id=None)


__all__ = [
    "Delivery",
    "LoggingDispatcher",
    "NotificationDispatcher",
    "OutboundMessage",
]
