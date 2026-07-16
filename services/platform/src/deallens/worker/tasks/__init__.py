"""Celery task registrations. Each module here is a thin sync wrapper: it opens the owner-role
`task_session`, runs an async orchestration/service call on the worker's persistent loop
(`worker.runtime.run_async`), and returns a small JSON-serializable summary for the result
backend. All real logic lives in the modules' services and `worker.orchestration` — a task is
only glue + retry policy (03 §9.5)."""
