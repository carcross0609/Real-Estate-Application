"""Adapter registry — the seam that makes sources pluggable (NFR-12).

An adapter declares itself with `@register_adapter`; the scheduler builds one by
`source_key` without importing the concrete module. This is the single place that knows the
set of installed sources, so "what can we ingest?" (ops S43 market-onboarding) and "spin up
the poller for `mls_grid`" are both one lookup.

Registration is keyed by `source_key` (a stable slug, e.g. `mls_grid`, `attom`) and stores a
*factory* — `Callable[[AdapterConfig], SourceAdapter]` — not an instance, because an adapter
needs its `data_sources` row id and a transport bound at build time.
"""

from __future__ import annotations

from collections.abc import Callable

from deallens.core.errors import NotFoundError
from deallens.modules.ingestion.adapters.base import AdapterConfig, SourceAdapter

AdapterFactory = Callable[[AdapterConfig], SourceAdapter]

_REGISTRY: dict[str, AdapterFactory] = {}


def register_adapter(source_key: str) -> Callable[[AdapterFactory], AdapterFactory]:
    """Decorator registering a factory under `source_key`. Applied to the adapter class
    itself (a class is a `Callable[[AdapterConfig], SourceAdapter]` — its `__init__`), or to
    a builder function when construction needs more than the config.

    Raises on a duplicate key so two adapters can't silently shadow each other — a real risk
    once there are a dozen source modules importing at startup.
    """

    def _decorate(factory: AdapterFactory) -> AdapterFactory:
        if source_key in _REGISTRY:
            raise ValueError(f"Adapter already registered for source_key {source_key!r}")
        _REGISTRY[source_key] = factory
        return factory

    return _decorate


def build_adapter(source_key: str, config: AdapterConfig) -> SourceAdapter:
    """Instantiate the adapter registered under `source_key`. Raises `NotFoundError` (→ 404
    at the API edge, or a paged ops error from the scheduler) rather than `KeyError` so an
    unknown/typo'd source surfaces as a first-class domain error.
    """
    factory = _REGISTRY.get(source_key)
    if factory is None:
        raise NotFoundError(
            f"No adapter registered for source_key {source_key!r}",
            available=sorted(_REGISTRY),
        )
    return factory(config)


def available_sources() -> list[str]:
    """Every registered `source_key`, sorted. Drives the admin market-onboarding picker (S43)
    and a `make`-time sanity check that a configured `data_sources` row has a live adapter.
    """
    return sorted(_REGISTRY)


def is_registered(source_key: str) -> bool:
    return source_key in _REGISTRY
