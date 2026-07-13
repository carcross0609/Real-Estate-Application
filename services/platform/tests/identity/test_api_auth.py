"""§15/§16.1 programmatic (API-key) authentication — the *pure* half.

The machine-facing auth path terminates at the same `Actor` as the human session path, but
authorizes via **scopes** (`require_scopes`) rather than **roles** (`authorize`). Everything
here is a pure function of its inputs — no DB, no request — so it runs in every environment.
The DB-backed half (key hashing/lookup, revocation, expiry, invite-email binding) lives in
`tests/integration/test_api_auth_db.py`.
"""

from uuid import uuid4

import pytest

from deallens.core.auth import (
    Actor,
    get_api_actor,
    missing_scopes,
    require_scopes,
)
from deallens.core.errors import ForbiddenError, UnauthenticatedError, ValidationError
from deallens.modules.identity.models import API_KEY_PREFIX, ApiScope
from deallens.modules.identity.service import emails_match, validate_scopes


def make_key_actor(*scopes: str) -> Actor:
    """An API-key `Actor`: no `org_role` (keys never drive RBAC), scopes carry the grants."""
    return Actor(
        user_id=uuid4(),
        clerk_user_id="",
        email="",
        org_id=uuid4(),
        org_role=None,
        auth_method="api_key",
        api_key_scopes=frozenset(scopes),
        api_key_id=uuid4(),
    )


# --- scope set arithmetic -----------------------------------------------------------------


def test_missing_scopes_returns_the_gap() -> None:
    actor = make_key_actor(ApiScope.PROPERTIES_READ)
    assert missing_scopes(actor, (ApiScope.PROPERTIES_READ,)) == set()
    assert missing_scopes(actor, (ApiScope.ANALYSES_WRITE,)) == {ApiScope.ANALYSES_WRITE}


def test_missing_scopes_reports_every_absent_scope() -> None:
    actor = make_key_actor(ApiScope.PROPERTIES_READ)
    assert missing_scopes(
        actor, (ApiScope.ANALYSES_READ, ApiScope.ANALYSES_WRITE)
    ) == {ApiScope.ANALYSES_READ, ApiScope.ANALYSES_WRITE}


# --- require_scopes dependency ------------------------------------------------------------


async def test_require_scopes_passes_when_key_holds_all() -> None:
    dep = require_scopes(ApiScope.PROPERTIES_READ, ApiScope.ANALYSES_READ)
    actor = make_key_actor(ApiScope.PROPERTIES_READ, ApiScope.ANALYSES_READ)
    assert await dep(actor) is actor  # returns the actor unchanged


async def test_require_scopes_rejects_when_any_scope_absent() -> None:
    dep = require_scopes(ApiScope.PROPERTIES_READ, ApiScope.ANALYSES_WRITE)
    actor = make_key_actor(ApiScope.PROPERTIES_READ)  # missing ANALYSES_WRITE
    with pytest.raises(ForbiddenError) as exc:
        await dep(actor)
    assert ApiScope.ANALYSES_WRITE in str(exc.value)


async def test_require_no_scopes_is_a_bare_authenticated_check() -> None:
    """Passing no scopes asserts only that a valid key was presented (a 'whoami')."""
    dep = require_scopes()
    actor = make_key_actor()  # a key with zero scopes still authenticates
    assert await dep(actor) is actor


# --- get_api_actor prefix gate (pre-DB, so unit-testable) ---------------------------------


async def test_get_api_actor_rejects_non_key_token_before_touching_db() -> None:
    """A session JWT (or any non-`dlk_` bearer) hitting an API-key-only endpoint fails 401
    without a DB round-trip — the prefix check short-circuits."""
    with pytest.raises(UnauthenticatedError):
        await get_api_actor(token="eyJhbGciOiJSUzI1NiJ9.session.jwt")
    with pytest.raises(UnauthenticatedError):
        await get_api_actor(token=f"{API_KEY_PREFIX}xned_missing_underscore")


# --- scope-vocabulary validation (mint-time guard) ----------------------------------------


def test_validate_scopes_accepts_the_known_vocabulary() -> None:
    validate_scopes([s.value for s in ApiScope])  # must not raise


def test_validate_scopes_rejects_unknown_scope() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_scopes([ApiScope.PROPERTIES_READ, "properties:delete"])
    assert "properties:delete" in str(exc.value)


def test_validate_scopes_empty_is_allowed() -> None:
    validate_scopes([])  # a zero-scope key is valid (see whoami test above)


# --- invite-email binding (the forwarding-vuln fix) ---------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("alice@example.com", "alice@example.com"),
        ("Alice@Example.com", "alice@example.com"),
        (" alice@example.com ", "alice@example.com"),
        ("ALICE@EXAMPLE.COM", "  alice@example.com"),
    ],
)
def test_emails_match_is_case_and_whitespace_insensitive(a: str, b: str) -> None:
    assert emails_match(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("alice@example.com", "bob@example.com"),
        ("alice@example.com", "alice@evil.com"),
        ("alice@example.com", ""),
    ],
)
def test_emails_match_rejects_different_recipients(a: str, b: str) -> None:
    assert not emails_match(a, b)
