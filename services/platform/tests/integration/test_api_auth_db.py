"""DB-backed half of the programmatic-auth + invite work — exercises the real `service`
functions against a live Postgres (seeded via the RLS-exempt owner engine).

Covers what the pure unit tests in `tests/identity/test_api_auth.py` cannot: the key is
stored only as a hash and round-trips by plaintext; revoked/expired/unknown keys all
authenticate to `None`; a bad scope aborts minting; and the invite-email binding actually
rejects a forwarded token at the persistence layer. Skips wholesale when the DB is down.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deallens.core.errors import ForbiddenError, ValidationError
from deallens.modules.identity import service
from deallens.modules.identity.models import ApiScope, InviteStatus, OrgRole

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return str(uuid.uuid4())


async def _seed_org(engine: AsyncEngine, *, slug: str) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Create an org + a user via the owner role. Returns (org_id, user_id, user_email)."""
    org_id, user_id = _uid(), _uid()
    email = f"{slug}@example.com"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO orgs (id, clerk_org_id, name, slug) "
                "VALUES (:id, :cid, :name, :slug)"
            ),
            {"id": org_id, "cid": f"clerk_{slug}", "name": slug, "slug": slug},
        )
        await conn.execute(
            text("INSERT INTO users (id, clerk_user_id, email) VALUES (:id, :cid, :email)"),
            {"id": user_id, "cid": f"user_{slug}", "email": email},
        )
    return uuid.UUID(org_id), uuid.UUID(user_id), email


@pytest.fixture
def session_factory(system_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions on the owner engine — RLS-exempt, so service calls that touch multiple orgs'
    rows (audit log, cross-checks) behave like the trusted service layer they model."""
    return async_sessionmaker(system_engine, expire_on_commit=False)


# --- API key lifecycle --------------------------------------------------------------------


async def test_create_key_returns_prefixed_plaintext_and_stores_only_hash(
    system_engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    org_id, user_id, _ = await _seed_org(system_engine, slug=f"k-{_uid()[:8]}")
    async with session_factory() as db:
        record, secret = await service.create_api_key(
            db, org_id=org_id, created_by_user_id=user_id, name="ci", scopes=[ApiScope.ORG_READ]
        )
        await db.commit()
        key_hash = record.key_hash

    assert secret.startswith("dlk_")
    assert key_hash != secret  # plaintext is never what's persisted
    assert secret not in key_hash

    # The stored hash round-trips by plaintext, and last_used_at gets stamped on auth.
    async with session_factory() as db:
        authed = await service.authenticate_api_key(db, plaintext=secret)
        await db.commit()
    assert authed is not None
    assert authed.id == record.id
    assert authed.last_used_at is not None


async def test_unknown_key_authenticates_to_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        assert await service.authenticate_api_key(db, plaintext="dlk_not_a_real_key") is None


async def test_revoked_key_authenticates_to_none(
    system_engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    org_id, user_id, _ = await _seed_org(system_engine, slug=f"r-{_uid()[:8]}")
    async with session_factory() as db:
        record, secret = await service.create_api_key(
            db, org_id=org_id, created_by_user_id=user_id, name="rev", scopes=[]
        )
        await db.commit()
        await service.revoke_api_key(
            db, org_id=org_id, api_key_id=record.id, revoked_by_user_id=user_id
        )
        await db.commit()

    async with session_factory() as db:
        assert await service.authenticate_api_key(db, plaintext=secret) is None


async def test_expired_key_authenticates_to_none(
    system_engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    org_id, user_id, _ = await _seed_org(system_engine, slug=f"e-{_uid()[:8]}")
    async with session_factory() as db:
        record, secret = await service.create_api_key(
            db, org_id=org_id, created_by_user_id=user_id, name="exp", scopes=[]
        )
        record.expires_at = datetime.now(UTC) - timedelta(days=1)  # already past
        await db.commit()

    async with session_factory() as db:
        assert await service.authenticate_api_key(db, plaintext=secret) is None


async def test_create_key_with_unknown_scope_is_rejected_and_persists_nothing(
    system_engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    org_id, user_id, _ = await _seed_org(system_engine, slug=f"bad-{_uid()[:8]}")
    async with session_factory() as db:
        with pytest.raises(ValidationError):
            await service.create_api_key(
                db,
                org_id=org_id,
                created_by_user_id=user_id,
                name="bad",
                scopes=["properties:delete"],  # not in the ApiScope vocabulary
            )
        await db.rollback()

    async with system_engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT count(*) FROM api_keys WHERE org_id = :o"), {"o": str(org_id)}
        )
    assert count == 0


# --- invite-email binding (the forwarding-vuln fix) ---------------------------------------


async def test_accept_invite_rejects_mismatched_email(
    system_engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    org_id, inviter_id, _ = await _seed_org(system_engine, slug=f"inv-{_uid()[:8]}")
    # A different person receives the forwarded link.
    _, attacker_id, attacker_email = await _seed_org(system_engine, slug=f"atk-{_uid()[:8]}")

    async with session_factory() as db:
        invite = await service.invite_member(
            db,
            org_id=org_id,
            invited_by_user_id=inviter_id,
            email="invited@example.com",
            role=OrgRole.ANALYST,
        )
        await db.commit()
        token = invite.token

    async with session_factory() as db:
        with pytest.raises(ForbiddenError):
            await service.accept_invite(
                db, token=token, user_id=attacker_id, accepting_email=attacker_email
            )
        await db.rollback()

    # The invite is still PENDING — a rejected attempt must not consume it.
    async with system_engine.connect() as conn:
        status = await conn.scalar(
            text("SELECT status FROM org_invites WHERE token = :t"), {"t": token}
        )
    assert status == InviteStatus.PENDING.value


async def test_accept_invite_succeeds_for_the_intended_recipient(
    system_engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    org_id, inviter_id, _ = await _seed_org(system_engine, slug=f"ok-{_uid()[:8]}")
    _, recipient_id, _ = await _seed_org(system_engine, slug=f"rcp-{_uid()[:8]}")
    invited_email = "Recipient.Person@Example.com"  # mixed case → must still match

    async with session_factory() as db:
        invite = await service.invite_member(
            db,
            org_id=org_id,
            invited_by_user_id=inviter_id,
            email=invited_email,
            role=OrgRole.VIEWER,
        )
        await db.commit()
        token = invite.token

    async with session_factory() as db:
        member = await service.accept_invite(
            db, token=token, user_id=recipient_id, accepting_email="  recipient.person@example.com "
        )
        await db.commit()

    assert member.org_id == org_id
    assert member.user_id == recipient_id
    assert member.role == OrgRole.VIEWER

    async with system_engine.connect() as conn:
        status = await conn.scalar(
            text("SELECT status FROM org_invites WHERE token = :t"), {"t": token}
        )
    assert status == InviteStatus.ACCEPTED.value
