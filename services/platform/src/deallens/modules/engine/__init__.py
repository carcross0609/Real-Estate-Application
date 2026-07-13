"""ENGINE bounded context (§9.3): deterministic financial calculations, comps/ARV/rent
estimators, scenarios. The calc core is pure (no I/O) and versioned (03 §26). These models
are the *stored outputs* of that core, not the core itself (which lives in engine/ pure fns).
"""
