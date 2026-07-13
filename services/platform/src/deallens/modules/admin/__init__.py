"""ADMIN / OPS bounded context (§9.3): ops surfaces, DQ queue, evals, flags, cost ledger.
May read across modules via admin-only interfaces. Hosts the OPS-group tables of §11.2:
ingestion_runs, dq_flags, model_versions, prompt_versions, ai_calls, feedback_labels.
"""
