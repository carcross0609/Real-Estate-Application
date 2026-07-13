"""Shared SQLAlchemy column types.

`pg_enum` is the ONLY way enum columns should be declared in this codebase. SQLAlchemy's
bare `Enum(SomePyEnum)` persists each member's **name** (`OrgRole.VIEWER` → `"VIEWER"`),
but every `postgresql.ENUM` in our migrations was created from the members' lowercase
**values** (`"viewer"`, per our `StrEnum` definitions). Those disagree, so an ORM insert
of any enum column raises `invalid input value for enum … "VIEWER"` against a real
Postgres — a failure invisible to unit tests and to integration tests that write raw SQL
literals, but fatal in production.

`values_callable` pins SQLAlchemy to the member *values*, realigning the ORM with the DDL.
Passing `create_type=False` mirrors the migrations, where the ENUM types are created and
dropped explicitly (see migration 0001's header) rather than implicitly per-table — so the
metadata never tries to emit or drop the type itself.
"""

import enum
from typing import Any

from sqlalchemy import Enum as SAEnum


def pg_enum(py_enum: type[enum.Enum], *, name: str, **kwargs: Any) -> SAEnum:
    """Declare a Postgres enum column bound to `py_enum` by member **value**, matching the
    types created in the Alembic migrations. Use this everywhere instead of `sa.Enum(...)`.
    """
    return SAEnum(
        py_enum,
        name=name,
        values_callable=lambda e: [member.value for member in e],
        create_type=False,
        **kwargs,
    )
