"""Billing module public interface (§9.3: `billing` owns plans + entitlement
computation; `identity` only consumes computed entitlements via
`identity.service.check_entitlement`).

Real Stripe integration (checkout, customer portal, subscription webhooks) is scoped to
Phase 3 (04-ROADMAP.md) and is not implemented here yet — no `stripe` dependency, no
`/billing/*` routes. This module exists now only so `recompute_entitlements` sits behind
the correct module boundary from the start; `identity.service.create_org` and
`identity.webhooks._upsert_org` call it directly to bootstrap a trialing org onto Basic.
When Stripe lands, a `webhooks.py` here will translate `customer.subscription.updated`
events into calls to the same function, keyed off `PLAN_ENTITLEMENTS`.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deallens.modules.identity.models import AlertLatency, Entitlement, SubscriptionPlan

# Values mirror PRD §33's pricing table. Team seats=5 base (+$39/seat as add-ons — add-on
# seats are tracked on `Subscription` once Stripe metered seats land, not modeled here).
PLAN_ENTITLEMENTS: dict[SubscriptionPlan, dict[str, object]] = {
    SubscriptionPlan.BASIC: dict(
        markets_limit=1,
        seats=1,
        alert_latency=AlertLatency.DAILY,
        exports_enabled=False,
        pipeline_enabled=False,
        api_access=False,
    ),
    SubscriptionPlan.PRO: dict(
        markets_limit=5,
        seats=1,
        alert_latency=AlertLatency.INSTANT,
        exports_enabled=True,
        pipeline_enabled=True,
        api_access=False,
    ),
    SubscriptionPlan.TEAM: dict(
        markets_limit=25,
        seats=5,
        alert_latency=AlertLatency.INSTANT,
        exports_enabled=True,
        pipeline_enabled=True,
        api_access=True,
    ),
}


async def recompute_entitlements(
    db: AsyncSession, *, org_id: UUID, plan: SubscriptionPlan
) -> Entitlement:
    """Derive `Entitlement` from `plan`. Called on org creation and (from Phase 3) on
    every subscription change — trial start, upgrade/downgrade, cancellation (falls back
    to Basic limits).
    """
    result = await db.execute(select(Entitlement).where(Entitlement.org_id == org_id))
    entitlement = result.scalar_one_or_none()
    fields = PLAN_ENTITLEMENTS[plan]
    if entitlement is None:
        entitlement = Entitlement(org_id=org_id, **fields)
        db.add(entitlement)
    else:
        for key, value in fields.items():
            setattr(entitlement, key, value)
    await db.flush()
    return entitlement
