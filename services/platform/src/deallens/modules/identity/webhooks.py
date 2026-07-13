"""Clerk webhook handler — syncs Clerk-owned identity objects (users, orgs, org
memberships) into our shadow tables (§16.1: we never depend on Clerk being up to render
data we already have). Every handler is idempotent: Clerk retries on any non-2xx response,
and an object created through our own API (e.g. `POST /orgs`) may already exist locally by
the time its webhook arrives.

Signature verification uses `svix` because Clerk webhooks are Svix-delivered — the
`clerk_webhook_signing_secret` is the *webhook endpoint's* signing secret from the Clerk
dashboard, not `clerk_secret_key`.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from svix.webhooks import Webhook, WebhookVerificationError

from deallens.core.config import get_settings
from deallens.core.db import system_session
from deallens.core.errors import ValidationError
from deallens.modules.billing.service import recompute_entitlements
from deallens.modules.identity import service
from deallens.modules.identity.models import (
    Org,
    OrgMember,
    OrgRole,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    User,
)

router = APIRouter(tags=["webhooks"])
settings = get_settings()


def _role_from_clerk(clerk_role: str) -> OrgRole:
    """Clerk custom org roles are configured as `org:owner`/`org:admin`/... (see
    core/clerk_client.py docstring). Falls back to the least-privileged role for anything
    unrecognized rather than granting excess access.
    """
    key = clerk_role.removeprefix("org:")
    try:
        return OrgRole(key)
    except ValueError:
        return OrgRole.VIEWER


async def _upsert_user(db: Any, data: dict[str, Any]) -> None:
    clerk_user_id = data["id"]
    emails = data.get("email_addresses", [])
    primary = next(
        (e for e in emails if e["id"] == data.get("primary_email_address_id")),
        emails[0] if emails else None,
    )
    if primary is None:
        return  # OAuth account mid-setup with no email yet; the next event will carry it.

    email = primary["email_address"]
    email_verified = primary.get("verification", {}).get("status") == "verified"
    full_name = " ".join(filter(None, [data.get("first_name"), data.get("last_name")])) or None

    result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        db.add(
            User(
                clerk_user_id=clerk_user_id,
                email=email,
                email_verified=email_verified,
                full_name=full_name,
                avatar_url=data.get("image_url"),
            )
        )
    else:
        user.email = email
        user.email_verified = email_verified
        user.full_name = full_name
        user.avatar_url = data.get("image_url")
    await db.flush()


async def _mark_user_deleted(db: Any, data: dict[str, Any]) -> None:
    result = await db.execute(select(User).where(User.clerk_user_id == data["id"]))
    user = result.scalar_one_or_none()
    if user is not None:
        user.deleted_at = datetime.now(UTC)
        await db.flush()


async def _upsert_org(db: Any, data: dict[str, Any]) -> None:
    clerk_org_id = data["id"]
    result = await db.execute(select(Org).where(Org.clerk_org_id == clerk_org_id))
    org = result.scalar_one_or_none()
    if org is None:
        org = Org(clerk_org_id=clerk_org_id, name=data["name"], slug=data["slug"])
        db.add(org)
        await db.flush()
        # An org created via Clerk's own UI (bypassing our `POST /orgs`) still needs a
        # subscription + entitlements row to function — mirror service.create_org here.
        # The owner membership follows separately via `organizationMembership.created`.
        db.add(
            Subscription(
                org_id=org.id, plan=SubscriptionPlan.BASIC, status=SubscriptionStatus.TRIALING
            )
        )
        await recompute_entitlements(db, org_id=org.id, plan=SubscriptionPlan.BASIC)
    else:
        org.name = data["name"]
        org.slug = data["slug"]
    await db.flush()


async def _delete_org(db: Any, data: dict[str, Any]) -> None:
    result = await db.execute(select(Org).where(Org.clerk_org_id == data["id"]))
    org = result.scalar_one_or_none()
    if org is not None:
        await service.write_audit_log(
            db, action="org.deleted", org_id=org.id, metadata={"source": "clerk_webhook"}
        )
        await db.delete(org)
        await db.flush()


async def _resolve_org_and_user(db: Any, data: dict[str, Any]) -> tuple[Org | None, User | None]:
    org_result = await db.execute(select(Org).where(Org.clerk_org_id == data["organization"]["id"]))
    user_result = await db.execute(
        select(User).where(User.clerk_user_id == data["public_user_data"]["user_id"])
    )
    return org_result.scalar_one_or_none(), user_result.scalar_one_or_none()


async def _upsert_membership(db: Any, data: dict[str, Any]) -> None:
    org, user = await _resolve_org_and_user(db, data)
    if org is None or user is None:
        return  # Out-of-order delivery; Clerk retries on non-2xx, but this returns 204 —
        # acceptable because the org/user webhook that unblocks this one arrives moments
        # later and this membership isn't security-load-bearing until then.

    role = _role_from_clerk(data["role"])
    member_result = await db.execute(
        select(OrgMember).where(OrgMember.org_id == org.id, OrgMember.user_id == user.id)
    )
    member = member_result.scalar_one_or_none()
    if member is None:
        db.add(OrgMember(org_id=org.id, user_id=user.id, role=role))
    else:
        member.role = role
    await db.flush()


async def _delete_membership(db: Any, data: dict[str, Any]) -> None:
    org, user = await _resolve_org_and_user(db, data)
    if org is None or user is None:
        return
    member_result = await db.execute(
        select(OrgMember).where(OrgMember.org_id == org.id, OrgMember.user_id == user.id)
    )
    member = member_result.scalar_one_or_none()
    if member is not None:
        await db.delete(member)
        await db.flush()


_HANDLERS = {
    "user.created": _upsert_user,
    "user.updated": _upsert_user,
    "user.deleted": _mark_user_deleted,
    "organization.created": _upsert_org,
    "organization.updated": _upsert_org,
    "organization.deleted": _delete_org,
    "organizationMembership.created": _upsert_membership,
    "organizationMembership.updated": _upsert_membership,
    "organizationMembership.deleted": _delete_membership,
}


@router.post("/clerk", status_code=204)
async def handle_clerk_webhook(request: Request) -> None:
    payload = await request.body()
    try:
        event = Webhook(settings.clerk_webhook_signing_secret).verify(
            payload, dict(request.headers)
        )
    except WebhookVerificationError as exc:
        raise ValidationError("Invalid webhook signature") from exc

    handler = _HANDLERS.get(event["type"])
    if handler is None:
        # Clerk sends many more event types (sessions, email addresses, ...) than this
        # slice needs — ignored, not an error.
        return

    async with system_session() as db:
        await handler(db, event["data"])
