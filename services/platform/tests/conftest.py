"""Shared test fixtures.

Tests are grouped into two tiers:
- Pure/unit (default): no external services — RBAC, entitlements, JWT verification, webhook
  signatures. These run everywhere, including this sandbox with no Postgres/Redis.
- Integration (`tests/integration`): require a live Postgres reachable at
  `DATABASE_URL_SYSTEM`/`DATABASE_URL` (e.g. `make dev && make api-migrate`). Auto-skipped
  when unreachable rather than failing CI machines that haven't started the compose stack —
  see `integration_engine` below.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt as jose_jwt

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://deallens_app:deallens_app_dev@localhost:5432/deallens"
)
os.environ.setdefault(
    "DATABASE_URL_SYSTEM", "postgresql+asyncpg://deallens:deallens_dev@localhost:5432/deallens"
)
os.environ.setdefault(
    "DATABASE_URL_SYNC", "postgresql+psycopg://deallens:deallens_dev@localhost:5432/deallens"
)
os.environ.setdefault("CLERK_ISSUER_URL", "https://test.clerk.accounts.dev")
os.environ.setdefault("CLERK_JWKS_URL", "https://test.clerk.accounts.dev/.well-known/jwks.json")
os.environ.setdefault("CLERK_SECRET_KEY", "sk_test_dummy")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "whsec_dGVzdF9zZWNyZXRfZm9yX3VuaXRfdGVzdHM=")


@pytest.fixture(scope="session")
def rsa_keypair():
    """A throwaway RSA keypair + its JWK representation, standing in for Clerk's signing
    key — real Clerk sessions are also RS256, so verification logic under test is
    identical.
    """
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jose.backends.cryptography_backend import CryptographyRSAKey

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jose_key = CryptographyRSAKey(private_key, algorithm="RS256")
    public_jwk = jose_key.public_key().to_dict()
    public_jwk["kid"] = "test-key-1"
    public_jwk["use"] = "sig"
    return private_key, {"keys": [public_jwk]}


@pytest.fixture
def make_session_token(rsa_keypair):
    private_key, _jwks = rsa_keypair

    def _make(
        *,
        sub: str = "user_test123",
        issuer: str = "https://test.clerk.accounts.dev",
        expires_in: int = 3600,
    ) -> str:
        now = datetime.now(UTC)
        claims = {
            "sub": sub,
            "iss": issuer,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
        }
        return jose_jwt.encode(
            claims, private_key, algorithm="RS256", headers={"kid": "test-key-1"}
        )

    return _make


@pytest.fixture
def svix_secret() -> str:
    return os.environ["CLERK_WEBHOOK_SIGNING_SECRET"]


@pytest.fixture
def sign_webhook(svix_secret):
    from svix.webhooks import Webhook

    def _sign(payload: str, *, msg_id: str = "msg_test") -> dict[str, str]:
        wh = Webhook(svix_secret)
        ts = datetime.now(UTC)
        signature = wh.sign(msg_id, ts, payload)
        return {
            "svix-id": msg_id,
            "svix-timestamp": str(int(ts.timestamp())),
            "svix-signature": signature,
        }

    return _sign
