"""REPORTS bounded context (§9.3): AI report generation, PDF rendering, share links.
Read-only over other modules' published views. Also hosts the user-workspace tables
(notes, pipeline board) that are schema-colocated here — the same pattern by which
identity's schema hosts Subscription/Entitlement while billing owns their logic (§9.3).
"""
