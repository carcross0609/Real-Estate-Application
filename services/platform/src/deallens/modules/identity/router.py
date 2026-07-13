"""Identity HTTP surface: profile, orgs, members, invites, API keys.

Every handler that touches org-scoped data calls `service.authorize()` before doing
anything else — no inline permission checks (§16.2, §20).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.auth import Actor, get_actor_db, get_api_actor, get_current_actor
from deallens.core.clerk_client import ClerkClient, get_clerk_client
from deallens.core.errors import NotFoundError
from deallens.modules.identity import schemas, service
from deallens.modules.identity.models import ApiKey, Org, OrgInvite, OrgMember, User

router = APIRouter(tags=["identity"])


# --- Profile (FR-053) -------------------------------------------------------------------


@router.get("/me", response_model=schemas.UserProfileOut)
async def get_my_profile(
    actor: Actor = Depends(get_current_actor), db: AsyncSession = Depends(get_actor_db)
) -> User:
    user = await db.get(User, actor.user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user


@router.patch("/me", response_model=schemas.UserProfileOut)
async def update_my_profile(
    body: schemas.UserProfileUpdate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> User:
    return await service.update_profile(
        db, user_id=actor.user_id, **body.model_dump(exclude_unset=True)
    )


@router.post(
    "/me/deletion-request",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=schemas.UserProfileOut,
)
async def request_my_account_deletion(
    actor: Actor = Depends(get_current_actor), db: AsyncSession = Depends(get_actor_db)
) -> User:
    return await service.request_account_deletion(db, user_id=actor.user_id)


@router.delete("/me/deletion-request", response_model=schemas.UserProfileOut)
async def cancel_my_account_deletion(
    actor: Actor = Depends(get_current_actor), db: AsyncSession = Depends(get_actor_db)
) -> User:
    return await service.cancel_account_deletion(db, user_id=actor.user_id)


@router.get("/me/orgs", response_model=list[schemas.OrgOut])
async def list_my_orgs(
    actor: Actor = Depends(get_current_actor), db: AsyncSession = Depends(get_actor_db)
) -> list[Org]:
    return await service.list_user_orgs(db, user_id=actor.user_id)


# --- Orgs (FR-052) -----------------------------------------------------------------------


@router.post("/orgs", response_model=schemas.OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(
    body: schemas.OrgCreate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
    clerk: ClerkClient = Depends(get_clerk_client),
) -> Org:
    """No `authorize()` call here: any authenticated user may create an org (they become
    its owner). Clerk is the org-identity source of truth, so it's created there first;
    if our local write then fails the Clerk org is orphaned but harmless (no local access
    was ever granted) — acceptable for Phase 1, revisit with a saga/outbox if it becomes a
    support burden.
    """
    clerk_org_id = await clerk.create_organization(
        name=body.name, slug=body.slug, created_by_clerk_user_id=actor.clerk_user_id
    )
    return await service.create_org(
        db, owner_user_id=actor.user_id, clerk_org_id=clerk_org_id, name=body.name, slug=body.slug
    )


@router.get("/orgs/{org_id}", response_model=schemas.OrgOut)
async def get_org(
    org_id: UUID,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> Org:
    service.authorize(actor, service.Action.ORG_VIEW, resource_org_id=org_id)
    org = await db.get(Org, org_id)
    if org is None:
        raise NotFoundError("Organization not found")
    return org


@router.patch("/orgs/{org_id}", response_model=schemas.OrgOut)
async def update_org(
    org_id: UUID,
    body: schemas.OrgUpdate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> Org:
    if body.locked_assumption_set is not None:
        service.authorize(actor, service.Action.ORG_LOCK_ASSUMPTIONS, resource_org_id=org_id)
    if body.name is not None:
        service.authorize(actor, service.Action.ORG_EDIT, resource_org_id=org_id)
    return await service.update_org(
        db, org_id=org_id, name=body.name, locked_assumption_set=body.locked_assumption_set
    )


# --- Members & invites ------------------------------------------------------------------


@router.get("/orgs/{org_id}/members", response_model=list[schemas.OrgMemberOut])
async def list_members(
    org_id: UUID,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> list[OrgMember]:
    service.authorize(actor, service.Action.ORG_VIEW, resource_org_id=org_id)
    return await service.list_members(db, org_id=org_id)


@router.post(
    "/orgs/{org_id}/invites", response_model=schemas.InviteOut, status_code=status.HTTP_201_CREATED
)
async def invite_member(
    org_id: UUID,
    body: schemas.InviteCreate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> OrgInvite:
    service.authorize(
        actor, service.Action.MEMBER_INVITE, resource_org_id=org_id, target_role=body.role
    )
    invite = await service.invite_member(
        db, org_id=org_id, invited_by_user_id=actor.user_id, email=body.email, role=body.role
    )
    # TODO(Phase 1): send the invite email via Resend/React Email with a link carrying
    # `invite.token`. Not wired yet — no email templates exist in this slice.
    return invite


@router.post("/orgs/invites/accept", response_model=schemas.OrgMemberOut)
async def accept_invite(
    body: schemas.InviteAccept,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
    clerk: ClerkClient = Depends(get_clerk_client),
) -> OrgMember:
    """No org context required — the invite token itself identifies the org. `service.accept_invite`
    binds the token to its intended recipient by requiring `actor.email` to match the invited
    address, so a forwarded invite link can't be redeemed by the wrong person.
    """
    member = await service.accept_invite(
        db, token=body.token, user_id=actor.user_id, accepting_email=actor.email
    )
    org = await db.get(Org, member.org_id)
    if org is not None:
        await clerk.create_organization_membership(
            clerk_org_id=org.clerk_org_id, clerk_user_id=actor.clerk_user_id, role=member.role
        )
    return member


@router.patch("/orgs/{org_id}/members/{user_id}", response_model=schemas.OrgMemberOut)
async def change_member_role(
    org_id: UUID,
    user_id: UUID,
    body: schemas.MemberRoleUpdate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
    clerk: ClerkClient = Depends(get_clerk_client),
) -> OrgMember:
    service.authorize(
        actor, service.Action.MEMBER_ROLE_CHANGE, resource_org_id=org_id, target_role=body.role
    )
    member = await service.change_member_role(
        db,
        org_id=org_id,
        target_user_id=user_id,
        new_role=body.role,
        changed_by_user_id=actor.user_id,
    )
    target_user = await db.get(User, user_id)
    org = await db.get(Org, org_id)
    if target_user is not None and org is not None:
        await clerk.update_organization_membership_role(
            clerk_org_id=org.clerk_org_id, clerk_user_id=target_user.clerk_user_id, role=body.role
        )
    return member


@router.delete("/orgs/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    org_id: UUID,
    user_id: UUID,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
    clerk: ClerkClient = Depends(get_clerk_client),
) -> None:
    service.authorize(actor, service.Action.MEMBER_REMOVE, resource_org_id=org_id)
    target_user = await db.get(User, user_id)
    org = await db.get(Org, org_id)
    await service.remove_member(
        db, org_id=org_id, target_user_id=user_id, removed_by_user_id=actor.user_id
    )
    if target_user is not None and org is not None:
        await clerk.delete_organization_membership(
            clerk_org_id=org.clerk_org_id, clerk_user_id=target_user.clerk_user_id
        )


# --- API keys (§15 "API keys scoped + metered") ------------------------------------------


@router.post(
    "/orgs/{org_id}/api-keys",
    response_model=schemas.ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    org_id: UUID,
    body: schemas.ApiKeyCreate,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> schemas.ApiKeyCreated:
    service.authorize(actor, service.Action.API_KEY_CREATE, resource_org_id=org_id)
    await service.check_entitlement(db, org_id=org_id, key="api_access")
    api_key, secret = await service.create_api_key(
        db, org_id=org_id, created_by_user_id=actor.user_id, name=body.name, scopes=body.scopes
    )
    return schemas.ApiKeyCreated(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        secret=secret,
        scopes=body.scopes,
    )


@router.get("/orgs/{org_id}/api-keys", response_model=list[schemas.ApiKeyOut])
async def list_api_keys(
    org_id: UUID,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> list[ApiKey]:
    service.authorize(actor, service.Action.API_KEY_LIST, resource_org_id=org_id)
    result = await db.execute(select(ApiKey).where(ApiKey.org_id == org_id))
    return list(result.scalars().all())


@router.delete("/orgs/{org_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    org_id: UUID,
    key_id: UUID,
    actor: Actor = Depends(get_current_actor),
    db: AsyncSession = Depends(get_actor_db),
) -> None:
    service.authorize(actor, service.Action.API_KEY_REVOKE, resource_org_id=org_id)
    await service.revoke_api_key(
        db, org_id=org_id, api_key_id=key_id, revoked_by_user_id=actor.user_id
    )


# --- Programmatic surface (API-key authenticated, not session) ----------------------------
#
# Every endpoint above authenticates a *human* via their Clerk session JWT. The routes below
# authenticate an *API key* (`Authorization: Bearer dlk_…`) through `get_api_actor` — the
# machine-facing half of §16.1. This `whoami` is the canonical "is my key live, and what can
# it do" call every API consumer needs; the real data endpoints it fronts (properties,
# analyses) arrive with their owning modules.


@router.get("/api/whoami", response_model=schemas.ApiIdentityOut, tags=["api"])
async def api_whoami(actor: Actor = Depends(get_api_actor)) -> schemas.ApiIdentityOut:
    return schemas.ApiIdentityOut(
        org_id=actor.require_org(),
        api_key_id=actor.api_key_id,
        scopes=sorted(actor.api_key_scopes),
        auth_method=actor.auth_method,
    )
