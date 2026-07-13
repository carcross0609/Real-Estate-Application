"""Clerk session JWT verification (local, via cached JWKS) + FastAPI auth dependencies.

We verify Clerk's session token ourselves against its published JWKS rather than calling
Clerk on every request (§16.1: latency + availability isolation — the API must keep
answering even if Clerk has an outage, as long as already-issued sessions are still valid).

Claim shape: Clerk's session token carries `sub` (Clerk user id) and, when an organization
is active in the client session, an `o` object `{id, rol, slg}` (org id, role, slug). The
role in the token is only a UX hint; every authorization decision re-reads the canonical
role from our own `org_members` shadow table (synced via webhook — see
modules/identity/webhooks.py) so a role change or removal takes effect immediately rather
than waiting for the browser to refresh its Clerk session token.
"""

import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from fastapi import Depends, Header
from jose import jwt
from jose.exceptions import JWTError
from sqlalchemy import select

from deallens.core.config import get_settings
from deallens.core.db import request_scoped_session, system_session
from deallens.core.errors import ForbiddenError, UnauthenticatedError
from deallens.modules.identity.models import OrgMember, User

settings = get_settings()

_JWKS_TTL_SECONDS = 3600
_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}


async def _get_jwks() -> dict[str, Any]:
    now = time.time()
    if _jwks_cache["keys"] is None or now - _jwks_cache["fetched_at"] > _JWKS_TTL_SECONDS:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(settings.clerk_jwks_url)
            resp.raise_for_status()
            _jwks_cache["keys"] = resp.json()
            _jwks_cache["fetched_at"] = now
    return _jwks_cache["keys"]  # type: ignore[no-any-return]


async def _find_key(kid: str | None) -> dict[str, Any] | None:
    jwks = await _get_jwks()
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        # Possible key rotation: force one refetch before giving up.
        _jwks_cache["fetched_at"] = 0.0
        jwks = await _get_jwks()
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    return key


async def verify_session_token(token: str) -> dict[str, Any]:
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise UnauthenticatedError("Malformed session token") from exc

    key = await _find_key(unverified_header.get("kid"))
    if key is None:
        raise UnauthenticatedError("Unknown signing key")

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer_url,
            options={
                "verify_aud": False,
                "leeway": settings.session_issuer_leeway_seconds,
            },
        )
    except JWTError as exc:
        raise UnauthenticatedError("Invalid or expired session token") from exc

    return claims


@dataclass(frozen=True)
class Actor:
    """The authenticated caller, with org context resolved from our own shadow tables."""

    user_id: UUID
    clerk_user_id: str
    email: str
    org_id: UUID | None
    org_role: str | None
    is_staff: bool = False

    def require_org(self) -> UUID:
        if self.org_id is None:
            raise ForbiddenError("This endpoint requires an active organization context")
        return self.org_id


async def get_bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthenticatedError("Missing bearer token")
    return authorization.split(" ", 1)[1]


async def get_current_actor(
    token: str = Depends(get_bearer_token),
    x_org_id: UUID | None = Header(default=None, alias="X-Org-Id"),
) -> Actor:
    """Resolve the caller from a verified Clerk session token plus our shadow tables.

    `X-Org-Id` lets a multi-org user select which org they're acting on (mirrors Clerk's
    client-side "active organization"); it only ever narrows access — the membership row
    still has to exist for `org_id`/`org_role` to be populated, so passing an arbitrary org
    id you don't belong to raises 403, not a silent cross-tenant view.

    The `clerk_user_id -> internal id` lookup runs on `system_session()` (RLS bypass):
    at that point we don't have an internal `user_id` to satisfy the `users` table's
    self-only RLS policy yet — the JWT signature is the trust boundary for this one read,
    not a GUC. Every subsequent lookup uses `request_scoped_session()` and is subject to
    RLS like any other request.
    """
    claims = await verify_session_token(token)
    clerk_user_id: str = claims["sub"]

    async with system_session() as sys_db:
        result = await sys_db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
        user = result.scalar_one_or_none()
    if user is None:
        # Webhook lag: the user just signed up and `user.created` hasn't synced yet.
        raise UnauthenticatedError("Account not yet provisioned; retry shortly")

    org_id = x_org_id
    org_role: str | None = None
    if org_id is not None:
        async with request_scoped_session(user_id=user.id) as db:
            member_result = await db.execute(
                select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == user.id)
            )
            member = member_result.scalar_one_or_none()
        if member is None:
            raise ForbiddenError("Not a member of the requested organization")
        org_role = member.role

    return Actor(
        user_id=user.id,
        clerk_user_id=clerk_user_id,
        email=user.email,
        org_id=org_id,
        org_role=org_role,
        is_staff=user.is_staff,
    )


async def get_actor_db(actor: Actor = Depends(get_current_actor)) -> AsyncGenerator[Any]:
    """The standard per-request DB dependency for module routers: a `request_scoped_session`
    pre-populated with the resolved actor's `user_id`/`org_id` GUCs. Pair with
    `Depends(get_current_actor)` for the actor itself when a handler needs both.
    """
    async with request_scoped_session(user_id=actor.user_id, org_id=actor.org_id) as db:
        yield db
