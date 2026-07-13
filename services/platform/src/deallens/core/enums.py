"""Cross-cutting domain enums shared by more than one module.

Module-local enums live in that module's `models.py` (e.g. `identity.OrgRole`). This file
is only for value types that genuinely span modules — putting them in `core` keeps the
`engine`/`scoring`/`alerts` model files from importing each other (§9.3 boundary rule).
"""

import enum


class Strategy(enum.StrEnum):
    """Investment strategies the engine underwrites and scoring ranks (03 §25.4, §26).

    `OVERALL` is a *scoring-only* value — it is the per-property max across the user's
    enabled strategy scores (03 §25.4), never an `engine` run input. The Postgres type is
    shared by `analyses`, `scenarios`, `scores`, and `buy_boxes`; a CHECK on `analyses`
    forbids `overall` there (see migration 0004).
    """

    FLIP = "flip"
    LTR = "ltr"
    BRRRR = "brrrr"
    STR = "str"
    HOUSE_HACK = "house_hack"
    WHOLESALE = "wholesale"
    MULTIFAMILY = "multifamily"
    LAND = "land"
    COMMERCIAL = "commercial"
    VALUE_ADD = "value_add"
    OVERALL = "overall"
