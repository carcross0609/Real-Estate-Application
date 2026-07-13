"""SCORING bounded context (§9.3): factor computation, weights, scores, grades,
explanations, Top-25 materialization. Reads engine/vision/markets outputs only through
their public interfaces. The explanation ledger is produced by the same code path as the
number, so it cannot drift from the truth (03 §25.1).
"""
