"""No billing-owned tables yet. `Subscription` and `Entitlement` are defined in
`deallens.modules.identity.models` — they're colocated with `Org` for the identity
schema's migration and FKs, even though `billing` (not `identity`) owns the business
logic that computes their values (see `billing.service`, §9.3).

Phase 3 Stripe integration will add tables here that are genuinely billing-only and have
no reason to live in identity's schema: a webhook-event idempotency log and a cached
invoice/payment-method view for the customer portal.
"""
