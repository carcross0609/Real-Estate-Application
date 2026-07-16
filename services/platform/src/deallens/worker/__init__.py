"""Background automation (Phase 1 M1.1 — the worker layer the rest of the platform was
built to slot behind).

The modules under `deallens.modules.*` are, by design, pure decision logic + async service
entrypoints + a swappable `EventEmitter` (03 §9.3/§9.4/§9.5): nothing there polls a feed,
runs on a timer, or reacts to an event on its own. This package is the part that does — the
Celery app, the async↔sync bridge, the beat schedule, and thin tasks that call the existing
services. It depends on every module; no module depends on it (the dependency graph stays
acyclic, §9.3).

What runs here (the FR-060/§9.5 continuous loop):
- **Ingestion polls** at each source's license cadence (`tasks.ingestion`) — new listings.
- **Event fan-out** — a `CeleryEmitter` turns the ingestion writer's domain events into
  tasks that drive enrich→analyze→score→match (`emitter`, `orchestration`, `tasks.pipeline`).
- **Analysis refresh** — stale valuations recompute as comps close / periods roll
  (`tasks.analysis` over `engine.recompute_if_stale`).
- **Alerting** — buy-box matches and watched-property changes become notifications
  (`tasks.alerts` over `alerts.service`).
- **Scoring drift audit** — a scheduled check of realized outcomes vs. predictions that
  *flags* when a recalibration is warranted (`tasks.scoring`); weights still ship like code.
"""
