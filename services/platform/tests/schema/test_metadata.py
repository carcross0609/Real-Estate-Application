"""Pure-unit guards on the ORM schema — no database required (run everywhere, incl. the
sandbox). These lock in the invariants of docs/02 §11: the table set, tenant scoping,
partition-key PKs, money-as-numeric, and that every FK column is indexed (§20 SQL standard).
"""

from sqlalchemy import Float, Numeric

import deallens.models  # noqa: F401  — populates Base.metadata
from deallens.core.db import Base

# The complete §11.2 entity model (identity group + the platform build). Kept explicit so a
# renamed/dropped/added table is a deliberate, reviewed change — not a silent drift.
EXPECTED_TABLES = {
    # IDENTITY (0001)
    "users",
    "orgs",
    "org_members",
    "org_invites",
    "subscriptions",
    "entitlements",
    "api_keys",
    "audit_log",
    # GEO (0002)
    "markets",
    "market_stats",
    "search_areas",
    # PROPERTY (0003)
    "data_sources",
    "raw_records",
    "properties",
    "listings",
    "listing_events",
    "listing_photos",
    "ownership_records",
    "tax_records",
    "geo_layers",
    # ANALYSIS + SCORING (0004)
    "photo_analyses",
    "property_conditions",
    "comp_sets",
    "comp_members",
    "valuations",
    "analyses",
    "scenarios",
    "scores",
    "score_factors",
    # USER-WORK + OPS (0005)
    "buy_boxes",
    "watchlist_items",
    "notifications",
    "reports",
    "share_links",
    "notes",
    "pipeline_deals",
    "feedback_labels",
    "ingestion_runs",
    "dq_flags",
    "model_versions",
    "prompt_versions",
    "ai_calls",
}

# Tables that carry tenant data → must have org_id (RLS scopes them; see the migrations).
ORG_SCOPED_TABLES = {
    "org_members",
    "org_invites",
    "subscriptions",
    "entitlements",
    "api_keys",
    "search_areas",
    "scenarios",
    "comp_sets",
    "comp_members",
    "buy_boxes",
    "watchlist_items",
    "notifications",
    "reports",
    "share_links",
    "notes",
    "pipeline_deals",
    "feedback_labels",
}

# Shared licensed/reference data → must NOT be org-scoped (every tenant sees it; §11.1).
SHARED_TABLES = {
    "markets",
    "market_stats",
    "data_sources",
    "raw_records",
    "properties",
    "listings",
    "listing_events",
    "listing_photos",
    "ownership_records",
    "tax_records",
    "geo_layers",
    "photo_analyses",
    "property_conditions",
    "valuations",
    "analyses",
    "scores",
    "score_factors",
    "ingestion_runs",
    "dq_flags",
    "model_versions",
    "prompt_versions",
    "ai_calls",
}

# Monthly range-partitioned (§11.6): PK must include the partition key so the partitioned
# table is valid Postgres.
PARTITIONED_PK = {
    "audit_log": "created_at",
    "listing_events": "observed_at",
    "notifications": "created_at",
    "ai_calls": "created_at",
}


def test_all_expected_tables_present() -> None:
    actual = set(Base.metadata.tables)
    assert actual == EXPECTED_TABLES, {
        "missing": EXPECTED_TABLES - actual,
        "unexpected": actual - EXPECTED_TABLES,
    }


def test_org_scoped_tables_have_org_id() -> None:
    for name in ORG_SCOPED_TABLES:
        cols = Base.metadata.tables[name].columns
        assert "org_id" in cols, f"{name} is tenant data but has no org_id"


def test_shared_tables_are_not_org_scoped() -> None:
    for name in SHARED_TABLES:
        cols = Base.metadata.tables[name].columns
        assert "org_id" not in cols, f"{name} is shared reference data; org_id would over-scope it"


def test_partitioned_tables_have_composite_pk() -> None:
    for table, key in PARTITIONED_PK.items():
        pk_cols = {c.name for c in Base.metadata.tables[table].primary_key.columns}
        assert pk_cols == {"id", key}, f"{table} PK must be (id, {key}), got {pk_cols}"


def test_no_float_columns_anywhere() -> None:
    """Money/rates are numeric end-to-end (§11.1, §20) — a Float column is a finance bug."""
    offenders = [
        f"{t}.{c.name}"
        for t in Base.metadata.tables.values()
        for c in t.columns
        if isinstance(c.type, Float) and not isinstance(c.type, Numeric)
    ]
    assert not offenders, f"float columns found (must be Numeric): {offenders}"


def test_foreign_keys_are_indexed() -> None:
    """§20: FKs indexed. An unindexed FK is a lock/cascade-scan hazard. We check that every
    FK column is the left-most column of some index (or unique constraint / PK)."""
    unindexed: list[str] = []
    for table in Base.metadata.tables.values():
        indexed_first_cols = {list(ix.columns)[0].name for ix in table.indexes if len(ix.columns)}
        indexed_first_cols |= {
            list(uc.columns)[0].name
            for uc in table.constraints
            if hasattr(uc, "columns") and len(getattr(uc, "columns", []))
        }
        pk_first = (
            {list(table.primary_key.columns)[0].name} if len(table.primary_key.columns) else set()
        )
        indexed_first_cols |= pk_first
        for col in table.columns:
            if col.foreign_keys and col.name not in indexed_first_cols:
                unindexed.append(f"{table.name}.{col.name}")
    assert not unindexed, f"unindexed foreign-key columns: {unindexed}"


def test_scores_top25_index_exists() -> None:
    """The Top-25 query (§11.5 #1) relies on a (market_id, strategy, score) composite."""
    idx = {ix.name: [c.name for c in ix.columns] for ix in Base.metadata.tables["scores"].indexes}
    assert idx.get("ix_scores_top25") == ["market_id", "strategy", "score"]
