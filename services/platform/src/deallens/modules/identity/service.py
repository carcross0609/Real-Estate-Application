"""Identity module public interface (§19 "modules/* import each other only via
service.py"). Owns the three-layer authorization decision (§16.2), org/member/invite/API
key lifecycle, and profile management. No inline permission checks belong in router.py or
any other module — everything routes through `authorize()`.
"""

import enum
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.auth import Actor
from deallens.core.config import get_settings
from deallens.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from deallens.modules.billing.service import recompute_entitlements
from deallens.modules.identity.models import (
    API_KEY_PREFIX,
    ApiKey,
    ApiScope,
    AuditLog,
    Entitlement,
    InviteStatus,
    Org,
    OrgInvite,
    OrgMember,
    OrgRole,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    User,
)

settings = get_settings()

INVITE_TTL_DAYS = 14


# --- Layer 2: entitlements (plan-based) — §16.2 --------------------------------------
#
# `recompute_entitlements` (plan → Entitlement values) lives in `billing.service` — §9.3
# assigns "plans, entitlement computation" to `billing`, not `identity`. It's imported
# above and re-used verbatim here for the org-creation bootstrap path; identity's own job
# is only to *consume* the computed values via `check_entitlement` below.


async def check_entitlement(db: AsyncSession, *, org_id: UUID, key: str) -> None:
    """Raise if the org's current plan doesn't include `key` (e.g. "api_access",
    "exports_enabled"). This is the layer-2 check from §16.2; call it explicitly in any
    endpoint gated by plan, alongside — not instead of — `authorize()`.
    """
    result = await db.execute(select(Entitlement).where(Entitlement.org_id == org_id))
    entitlement = result.scalar_one_or_none()
    if entitlement is None or not getattr(entitlement, key, False):
        raise ForbiddenError(f"Current plan does not include '{key}'")


# --- Layer 1: RBAC — §16.2 -------------------------------------------------------------


class Action(enum.StrEnum):
    ORG_VIEW = "org.view"
    ORG_EDIT = "org.edit"
    ORG_DELETE = "org.delete"
    ORG_LOCK_ASSUMPTIONS = "org.lock_assumptions"
    MEMBER_INVITE = "member.invite"
    MEMBER_ROLE_CHANGE = "member.role_change"
    MEMBER_REMOVE = "member.remove"
    BILLING_MANAGE = "billing.manage"
    API_KEY_CREATE = "api_key.create"
    API_KEY_REVOKE = "api_key.revoke"
    API_KEY_LIST = "api_key.list"


_VIEWER_ACTIONS = {Action.ORG_VIEW, Action.API_KEY_LIST}
_ANALYST_ACTIONS = _VIEWER_ACTIONS
_ADMIN_ACTIONS = _ANALYST_ACTIONS | {
    Action.ORG_EDIT,
    Action.MEMBER_INVITE,
    Action.MEMBER_ROLE_CHANGE,
    Action.MEMBER_REMOVE,
    Action.API_KEY_CREATE,
    Action.API_KEY_REVOKE,
}
_OWNER_ACTIONS = _ADMIN_ACTIONS | {
    Action.ORG_DELETE,
    Action.ORG_LOCK_ASSUMPTIONS,
    Action.BILLING_MANAGE,
}

ROLE_PERMISSIONS: dict[OrgRole, frozenset[Action]] = {
    OrgRole.VIEWER: frozenset(_VIEWER_ACTIONS),
    OrgRole.ANALYST: frozenset(_ANALYST_ACTIONS),
    OrgRole.ADMIN: frozenset(_ADMIN_ACTIONS),
    OrgRole.OWNER: frozenset(_OWNER_ACTIONS),
}


def authorize(
    actor: Actor,
    action: Action,
    *,
    resource_org_id: UUID | None = None,
    target_role: OrgRole | None = None,
) -> None:
    """The single choke point for every authorization decision (§16.2). Raises
    `ForbiddenError` on any failure — callers never get a bool they could forget to check.

    Evaluates, in order: (1) resource ownership — the actor's active org must match the
    resource's org; (2) role — the actor's org role must include `action` in its permission
    set; (3) a privilege-escalation guard on role changes: only an owner may grant or revoke
    the owner role.
    """
    org_id = actor.require_org()
    if resource_org_id is not None and resource_org_id != org_id:
        raise ForbiddenError("Resource does not belong to the actor's active organization")

    if actor.org_role is None:
        raise ForbiddenError("Not a member of this organization")
    role = OrgRole(actor.org_role)

    if action not in ROLE_PERMISSIONS[role]:
        raise ForbiddenError(f"Role '{role.value}' cannot perform '{action.value}'")

    if (
        action == Action.MEMBER_ROLE_CHANGE
        and target_role == OrgRole.OWNER
        and role != OrgRole.OWNER
    ):
        raise ForbiddenError("Only an owner can grant the owner role")


# --- Audit -------------------------------------------------------------------------------


async def write_audit_log(
    db: AsyncSession,
    *,
    action: str,
    org_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    db.add(
        AuditLog(
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            event_metadata=metadata or {},
            ip_address=ip_address,
        )
    )
    await db.flush()


# --- Orgs ----------------------------------------------------------------------------


async def create_org(
    db: AsyncSession, *, owner_user_id: UUID, clerk_org_id: str, name: str, slug: str
) -> Org:
    """Creates the org, its owner membership, a trial subscription, and starter
    entitlements atomically — `orgs`' RLS policy permits the insert for any authenticated
    caller and the owner `org_members` row satisfies its own policy via `user_id` match, so
    this all happens inside one `request_scoped_session(user_id=owner_user_id)` (no
    `org_id` yet — the org doesn't exist until this transaction commits).
    """
    org = Org(clerk_org_id=clerk_org_id, name=name, slug=slug)
    db.add(org)
    await db.flush()

    db.add(OrgMember(org_id=org.id, user_id=owner_user_id, role=OrgRole.OWNER))
    db.add(
        Subscription(org_id=org.id, plan=SubscriptionPlan.BASIC, status=SubscriptionStatus.TRIALING)
    )
    await recompute_entitlements(db, org_id=org.id, plan=SubscriptionPlan.BASIC)
    await write_audit_log(
        db,
        action="org.created",
        org_id=org.id,
        actor_user_id=owner_user_id,
        target_type="org",
        target_id=str(org.id),
    )
    return org


async def list_user_orgs(db: AsyncSession, *, user_id: UUID) -> list[Org]:
    result = await db.execute(
        select(Org).join(OrgMember, OrgMember.org_id == Org.id).where(OrgMember.user_id == user_id)
    )
    return list(result.scalars().all())


async def update_org(
    db: AsyncSession,
    *,
    org_id: UUID,
    name: str | None = None,
    locked_assumption_set: dict[str, Any] | None = None,
) -> Org:
    org = await db.get(Org, org_id)
    if org is None:
        raise NotFoundError("Organization not found")
    if name is not None:
        org.name = name
    if locked_assumption_set is not None:
        org.locked_assumption_set = locked_assumption_set
    await db.flush()
    return org


# --- Members & invites -----------------------------------------------------------------


async def list_members(db: AsyncSession, *, org_id: UUID) -> list[OrgMember]:
    result = await db.execute(select(OrgMember).where(OrgMember.org_id == org_id))
    return list(result.scalars().all())


async def invite_member(
    db: AsyncSession,
    *,
    org_id: UUID,
    invited_by_user_id: UUID,
    email: str,
    role: OrgRole,
) -> OrgInvite:
    existing_member = await db.execute(
        select(OrgMember)
        .join(User, User.id == OrgMember.user_id)
        .where(OrgMember.org_id == org_id, User.email == email)
    )
    if existing_member.scalar_one_or_none() is not None:
        raise ConflictError("This person is already a member of the organization")

    invite = OrgInvite(
        org_id=org_id,
        email=email,
        role=role,
        invited_by_user_id=invited_by_user_id,
        token=secrets.token_urlsafe(32),
        status=InviteStatus.PENDING,
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    await write_audit_log(
        db,
        action="org.member.invited",
        org_id=org_id,
        actor_user_id=invited_by_user_id,
        target_type="org_invite",
        metadata={"email": email, "role": role.value},
    )
    await db.flush()
    return invite


def emails_match(a: str, b: str) -> bool:
    """Case- and whitespace-insensitive local comparison for invite redemption. Not a full
    RFC-5321 canonicalization (we don't fold Gmail dots etc.) — just enough that "Alice@x.com "
    and "alice@x.com" are the same person, which is all the invite-forwarding guard needs.
    """
    return a.strip().casefold() == b.strip().casefold()


async def accept_invite(
    db: AsyncSession, *, token: str, user_id: UUID, accepting_email: str
) -> OrgMember:
    """Redeem an invite token. The accepting user's email must match the address the invite
    was issued to — otherwise a forwarded invite link would let *anyone* who received it join
    the org (the vuln flagged in router.py's original docstring). The token alone is a
    bearer credential; the email check binds it to the intended recipient.
    """
    result = await db.execute(select(OrgInvite).where(OrgInvite.token == token))
    invite = result.scalar_one_or_none()
    if invite is None or invite.status != InviteStatus.PENDING:
        raise NotFoundError("Invite not found or already used")
    if not emails_match(invite.email, accepting_email):
        raise ForbiddenError("This invite was issued to a different email address")
    if invite.expires_at < datetime.now(UTC):
        invite.status = InviteStatus.EXPIRED
        await db.flush()
        raise ValidationError("Invite has expired")

    invite.status = InviteStatus.ACCEPTED
    member = OrgMember(org_id=invite.org_id, user_id=user_id, role=invite.role)
    db.add(member)
    await write_audit_log(
        db,
        action="org.member.joined",
        org_id=invite.org_id,
        actor_user_id=user_id,
        target_type="org_member",
    )
    await db.flush()
    return member


async def _count_owners(db: AsyncSession, *, org_id: UUID) -> int:
    result = await db.execute(
        select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.role == OrgRole.OWNER)
    )
    return len(result.scalars().all())


async def change_member_role(
    db: AsyncSession,
    *,
    org_id: UUID,
    target_user_id: UUID,
    new_role: OrgRole,
    changed_by_user_id: UUID,
) -> OrgMember:
    result = await db.execute(
        select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == target_user_id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFoundError("Member not found")

    if (
        member.role == OrgRole.OWNER
        and new_role != OrgRole.OWNER
        and await _count_owners(db, org_id=org_id) <= 1
    ):
        raise ValidationError("An organization must always have at least one owner")

    member.role = new_role
    await write_audit_log(
        db,
        action="org.member.role_changed",
        org_id=org_id,
        actor_user_id=changed_by_user_id,
        target_type="org_member",
        target_id=str(member.id),
        metadata={"new_role": new_role.value},
    )
    await db.flush()
    return member


async def remove_member(
    db: AsyncSession, *, org_id: UUID, target_user_id: UUID, removed_by_user_id: UUID
) -> None:
    result = await db.execute(
        select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == target_user_id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFoundError("Member not found")
    if member.role == OrgRole.OWNER and await _count_owners(db, org_id=org_id) <= 1:
        raise ValidationError("An organization must always have at least one owner")

    await db.delete(member)
    await write_audit_log(
        db,
        action="org.member.removed",
        org_id=org_id,
        actor_user_id=removed_by_user_id,
        target_type="org_member",
        target_id=str(target_user_id),
    )
    await db.flush()


# --- API keys ------------------------------------------------------------------------


def _hash_api_key(plaintext: str) -> str:
    # SHA-256 with no salt/stretching is deliberate and correct here: the secret is a
    # 256-bit random token, not a low-entropy human password, so there is nothing for a
    # slow KDF (argon2/bcrypt) to protect against — brute force is already infeasible, and a
    # fast digest keeps per-request key auth cheap. (Passwords are Clerk's problem, §16.1.)
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def validate_scopes(scopes: list[str]) -> None:
    """Reject any scope outside the `ApiScope` vocabulary. Called before minting a key so a
    caller can't persist a typo'd (permanently useless) or speculative (silently over-broad
    once we add that scope) grant.
    """
    valid = {s.value for s in ApiScope}
    unknown = sorted(set(scopes) - valid)
    if unknown:
        raise ValidationError(f"Unknown API scope(s): {', '.join(unknown)}")


async def create_api_key(
    db: AsyncSession, *, org_id: UUID, created_by_user_id: UUID, name: str, scopes: list[str]
) -> tuple[ApiKey, str]:
    """Returns `(record, plaintext_secret)`. The plaintext is shown exactly once — only
    `key_hash` is persisted, so a leaked database dump can't be replayed as a working key.
    """
    validate_scopes(scopes)
    secret = f"{API_KEY_PREFIX}_{secrets.token_urlsafe(32)}"
    api_key = ApiKey(
        org_id=org_id,
        created_by_user_id=created_by_user_id,
        name=name,
        key_prefix=secret[:12],
        key_hash=_hash_api_key(secret),
        scopes=scopes,
    )
    db.add(api_key)
    await write_audit_log(
        db,
        action="api_key.created",
        org_id=org_id,
        actor_user_id=created_by_user_id,
        target_type="api_key",
        metadata={"name": name, "scopes": scopes},
    )
    await db.flush()
    return api_key, secret


async def revoke_api_key(
    db: AsyncSession, *, org_id: UUID, api_key_id: UUID, revoked_by_user_id: UUID
) -> None:
    api_key = await db.get(ApiKey, api_key_id)
    if api_key is None or api_key.org_id != org_id:
        raise NotFoundError("API key not found")
    api_key.revoked_at = datetime.now(UTC)
    await write_audit_log(
        db,
        action="api_key.revoked",
        org_id=org_id,
        actor_user_id=revoked_by_user_id,
        target_type="api_key",
        target_id=str(api_key_id),
    )
    await db.flush()


async def authenticate_api_key(db: AsyncSession, *, plaintext: str) -> ApiKey | None:
    """Used by the (separate) API-key auth dependency for programmatic access — session
    JWT auth and API-key auth are two distinct entry points that both terminate at the same
    `Actor`/`authorize()` layer downstream.
    """
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == _hash_api_key(plaintext)))
    api_key = result.scalar_one_or_none()
    if api_key is None or api_key.revoked_at is not None:
        return None
    if api_key.expires_at is not None and api_key.expires_at < datetime.now(UTC):
        return None
    api_key.last_used_at = datetime.now(UTC)
    await db.flush()
    return api_key


# --- Profile ---------------------------------------------------------------------------


async def update_profile(
    db: AsyncSession,
    *,
    user_id: UUID,
    full_name: str | None = None,
    avatar_url: str | None = None,
    preferences: dict[str, Any] | None = None,
) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    if full_name is not None:
        user.full_name = full_name
    if avatar_url is not None:
        user.avatar_url = avatar_url
    if preferences is not None:
        user.preferences = {**user.preferences, **preferences}
    await db.flush()
    return user


async def request_account_deletion(db: AsyncSession, *, user_id: UUID) -> User:
    """FR-053: account deletion performs data erasure per privacy policy, 30-day grace.
    Sets the timestamp; the actual erasure runs as a scheduled Celery task
    (`modules.identity.tasks.erase_expired_accounts`, Phase-1 follow-up — the task body
    depends on which other modules' rows must cascade, not yet built) that checks
    `deletion_requested_at + ACCOUNT_DELETION_GRACE_DAYS <= now()`.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    user.deletion_requested_at = datetime.now(UTC)
    await write_audit_log(db, action="user.deletion_requested", actor_user_id=user_id)
    await db.flush()
    return user


async def cancel_account_deletion(db: AsyncSession, *, user_id: UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    user.deletion_requested_at = None
    await write_audit_log(db, action="user.deletion_cancelled", actor_user_id=user_id)
    await db.flush()
    return user
