"""API request/response schemas — the FastAPI boundary. Dicts never cross module
boundaries (§20); every router function accepts/returns one of these.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from deallens.modules.identity.models import OrgRole


class UserProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    email_verified: bool
    full_name: str | None
    avatar_url: str | None
    preferences: dict[str, Any]
    deletion_requested_at: datetime | None
    created_at: datetime


class UserProfileUpdate(BaseModel):
    full_name: str | None = None
    avatar_url: str | None = None
    preferences: dict[str, Any] | None = None


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    locked_assumption_set: dict[str, Any] | None
    created_at: datetime


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255, pattern=r"^[a-z0-9-]+$")


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    locked_assumption_set: dict[str, Any] | None = None


class OrgMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    role: OrgRole
    created_at: datetime


class MemberRoleUpdate(BaseModel):
    role: OrgRole


class InviteCreate(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.VIEWER


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: OrgRole
    status: str
    expires_at: datetime


class InviteAccept(BaseModel):
    token: str


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(default_factory=list)


class ApiKeyCreated(BaseModel):
    """Returned exactly once, on creation — the plaintext secret is never retrievable
    again."""

    id: UUID
    name: str
    key_prefix: str
    secret: str
    scopes: list[str]


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
