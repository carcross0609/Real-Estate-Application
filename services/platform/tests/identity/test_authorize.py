"""§16.2 RBAC unit tests — the full owner/admin/analyst/viewer × Action permission matrix,
plus the privilege-escalation guard and resource-ownership check. No DB needed: `authorize`
is a pure function of `(Actor, Action)`.
"""

from uuid import uuid4

import pytest

from deallens.core.auth import Actor
from deallens.core.errors import ForbiddenError
from deallens.modules.identity.models import OrgRole
from deallens.modules.identity.service import ROLE_PERMISSIONS, Action, authorize

ORG_ID = uuid4()


def make_actor(role: OrgRole | None, *, org_id=ORG_ID) -> Actor:
    return Actor(
        user_id=uuid4(),
        clerk_user_id="user_test",
        email="test@example.com",
        org_id=org_id,
        org_role=role.value if role else None,
    )


@pytest.mark.parametrize("role", list(OrgRole))
@pytest.mark.parametrize("action", list(Action))
def test_permission_matrix_matches_role_permissions_table(role: OrgRole, action: Action) -> None:
    actor = make_actor(role)
    should_succeed = action in ROLE_PERMISSIONS[role]

    if should_succeed:
        authorize(actor, action, resource_org_id=ORG_ID)  # must not raise
    else:
        with pytest.raises(ForbiddenError):
            authorize(actor, action, resource_org_id=ORG_ID)


def test_viewer_cannot_invite_members() -> None:
    with pytest.raises(ForbiddenError):
        authorize(make_actor(OrgRole.VIEWER), Action.MEMBER_INVITE, resource_org_id=ORG_ID)


def test_owner_can_do_everything_admin_can() -> None:
    for action in ROLE_PERMISSIONS[OrgRole.ADMIN]:
        assert action in ROLE_PERMISSIONS[OrgRole.OWNER]


def test_only_billing_and_delete_and_lock_are_owner_exclusive() -> None:
    owner_only = ROLE_PERMISSIONS[OrgRole.OWNER] - ROLE_PERMISSIONS[OrgRole.ADMIN]
    assert owner_only == {Action.ORG_DELETE, Action.ORG_LOCK_ASSUMPTIONS, Action.BILLING_MANAGE}


def test_resource_org_mismatch_is_forbidden_even_for_owner() -> None:
    other_org = uuid4()
    with pytest.raises(ForbiddenError):
        authorize(make_actor(OrgRole.OWNER), Action.ORG_VIEW, resource_org_id=other_org)


def test_no_active_org_is_forbidden() -> None:
    actor = make_actor(OrgRole.OWNER, org_id=None)
    with pytest.raises(ForbiddenError):
        authorize(actor, Action.ORG_VIEW, resource_org_id=ORG_ID)


def test_non_member_is_forbidden() -> None:
    actor = make_actor(None)
    with pytest.raises(ForbiddenError):
        authorize(actor, Action.ORG_VIEW, resource_org_id=ORG_ID)


class TestOwnerRoleGrantGuard:
    """Only an owner may grant or revoke the owner role — an admin promoting someone (or
    themself) to owner would be a privilege-escalation bug.
    """

    def test_admin_cannot_grant_owner_role(self) -> None:
        with pytest.raises(ForbiddenError):
            authorize(
                make_actor(OrgRole.ADMIN),
                Action.MEMBER_ROLE_CHANGE,
                resource_org_id=ORG_ID,
                target_role=OrgRole.OWNER,
            )

    def test_owner_can_grant_owner_role(self) -> None:
        authorize(
            make_actor(OrgRole.OWNER),
            Action.MEMBER_ROLE_CHANGE,
            resource_org_id=ORG_ID,
            target_role=OrgRole.OWNER,
        )

    def test_admin_can_grant_non_owner_roles(self) -> None:
        authorize(
            make_actor(OrgRole.ADMIN),
            Action.MEMBER_ROLE_CHANGE,
            resource_org_id=ORG_ID,
            target_role=OrgRole.ANALYST,
        )
