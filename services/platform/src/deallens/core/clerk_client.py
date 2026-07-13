"""Minimal Clerk Backend API client — the only place that calls out to Clerk for writes.

Scope is deliberately narrow: organization + membership mutations that our own API
triggers (org creation gated by seat/entitlement checks, role changes). Everything else
(sign-up, sign-in, password reset, MFA, session issuance) is handled by Clerk's own
frontend components — that's the point of buying Clerk (§10) — and reaches us only as a
webhook (see modules/identity/webhooks.py).

Setup dependency: this assumes custom Clerk organization roles `org:owner`, `org:admin`,
`org:analyst`, `org:viewer` are defined in the Clerk dashboard (Organizations → Roles) to
mirror `OrgRole`. Phase-0 setup task, not something this client can do for you.
"""

from typing import Any

import httpx

from deallens.core.config import get_settings
from deallens.core.errors import UpstreamServiceError
from deallens.modules.identity.models import OrgRole

_BASE_URL = "https://api.clerk.com/v1"


def _role_key(role: OrgRole) -> str:
    return f"org:{role.value}"


class ClerkClient:
    def __init__(self, secret_key: str | None = None) -> None:
        self._secret_key = secret_key or get_settings().clerk_secret_key

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=10.0) as client:
            try:
                resp = await client.request(
                    method, path, headers={"Authorization": f"Bearer {self._secret_key}"}, **kwargs
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise UpstreamServiceError(f"Clerk API call failed: {method} {path}") from exc
        return resp.json() if resp.content else {}

    async def create_organization(
        self, *, name: str, slug: str, created_by_clerk_user_id: str
    ) -> str:
        """Returns the new Clerk organization id (`org_...`)."""
        body = await self._request(
            "POST",
            "/organizations",
            json={"name": name, "slug": slug, "created_by": created_by_clerk_user_id},
        )
        return str(body["id"])

    async def delete_organization(self, *, clerk_org_id: str) -> None:
        await self._request("DELETE", f"/organizations/{clerk_org_id}")

    async def create_organization_membership(
        self, *, clerk_org_id: str, clerk_user_id: str, role: OrgRole
    ) -> None:
        await self._request(
            "POST",
            f"/organizations/{clerk_org_id}/memberships",
            json={"user_id": clerk_user_id, "role": _role_key(role)},
        )

    async def update_organization_membership_role(
        self, *, clerk_org_id: str, clerk_user_id: str, role: OrgRole
    ) -> None:
        await self._request(
            "PATCH",
            f"/organizations/{clerk_org_id}/memberships/{clerk_user_id}",
            json={"role": _role_key(role)},
        )

    async def delete_organization_membership(
        self, *, clerk_org_id: str, clerk_user_id: str
    ) -> None:
        await self._request("DELETE", f"/organizations/{clerk_org_id}/memberships/{clerk_user_id}")


def get_clerk_client() -> ClerkClient:
    return ClerkClient()
