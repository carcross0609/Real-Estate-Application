"""Source adapters (§14.2). Every licensed feed conforms to the one `SourceAdapter`
contract in `base.py`; concrete adapters (RESO/MLS, ATTOM public records, …) register
themselves in `registry.py`. Importing this package registers the built-in adapters as a
side effect (so `build_adapter("mls_grid", …)` works without the caller importing the
module), the same way a plugin system auto-discovers entry points.

New source = new adapter module + one `@register_adapter` decorator. Nothing else in the
pipeline changes (NFR-12).
"""

from deallens.modules.ingestion.adapters import attom, reso  # noqa: F401 — registration side effect
from deallens.modules.ingestion.adapters.base import (
    AdapterConfig,
    CanonicalDelta,
    Cursor,
    FetchTask,
    FixtureTransport,
    HttpxTransport,
    RawBatch,
    RawItem,
    RawTransport,
    SourceAdapter,
    SourceHealth,
)
from deallens.modules.ingestion.adapters.registry import (
    available_sources,
    build_adapter,
    register_adapter,
)

__all__ = [
    "AdapterConfig",
    "CanonicalDelta",
    "Cursor",
    "FetchTask",
    "FixtureTransport",
    "HttpxTransport",
    "RawBatch",
    "RawItem",
    "RawTransport",
    "SourceAdapter",
    "SourceHealth",
    "available_sources",
    "build_adapter",
    "register_adapter",
    "attom",
    "reso",
]
