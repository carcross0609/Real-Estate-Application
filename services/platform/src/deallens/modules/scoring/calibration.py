"""Scoring drift & recalibration policy — pure (03 §25.1 #5, PRD §4.3 accuracy tracking).

The scorer is a deterministic weighted engine over versioned config curves (`weights.py`), not
a trained ML model — so "retrain the scoring model where appropriate" here means: watch how the
calibrated score tracks *realized outcomes*, and decide when the calibration has drifted enough
to warrant a recalibration. This module is the **decision**, kept pure so the drift math is
testable without a database; a worker task assembles the realized-outcome sample and the
scoring maintainers act on the recommendation.

Crucially, it recommends — it does not auto-ship. Weight/curve changes ship like code:
eval-gated, changelogged, version-bumped (03 preamble, §25.1 #5). Auto-mutating live weights
from an online signal is exactly the failure mode that principle guards against. So the output
is a `RecalibrationDecision` (a flag + the evidence), which the task records for review, never a
new weight table applied in place.

Two drift signals, both on the 0–100 calibrated score:
- **Bias** — mean signed error (predicted − realized). A persistent positive bias means the
  score runs hot (over-promising); negative runs cold. Calibration targets zero bias.
- **Rank quality** — does a higher score actually correspond to a better realized outcome? A
  simple concordance (fraction of correctly-ordered pairs, a Kendall-style agreement) catches a
  calibration that stayed unbiased on average but lost its ordering power.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class DriftSeverity(StrEnum):
    OK = "ok"  # within tolerance — no action
    WATCH = "watch"  # drifting; log it, don't recalibrate yet
    RECALIBRATE = "recalibrate"  # past tolerance — recommend a recalibration pass


@dataclass(frozen=True, slots=True)
class OutcomeSample:
    """One realized outcome: the score we predicted vs. an observed quality on the same 0–100
    scale (e.g. a realized-return or DOM-derived outcome score for a property that transacted).
    `realized` is produced by the task from ground truth — this module never invents it."""

    predicted: Decimal
    realized: Decimal


@dataclass(frozen=True, slots=True)
class DriftThresholds:
    """Tolerances the decision is read against. Defaults are deliberately conservative — a
    recalibration is a config release, so the bar to recommend one is high and needs a
    meaningful sample behind it (03 §25.1 #5)."""

    min_samples: int = 50
    bias_watch: Decimal = Decimal("5")  # |mean error| over this = watch
    bias_recalibrate: Decimal = Decimal("10")  # and over this = recommend recalibration
    concordance_floor: Decimal = Decimal("0.60")  # rank agreement under this = recommend


@dataclass(frozen=True, slots=True)
class DriftReport:
    n: int
    mean_error: Decimal  # signed: predicted − realized (positive = runs hot)
    mae: Decimal  # mean absolute error
    concordance: Decimal  # 0–1 fraction of correctly-ordered pairs (0.5 = no signal)


@dataclass(frozen=True, slots=True)
class RecalibrationDecision:
    should_recalibrate: bool
    severity: DriftSeverity
    report: DriftReport
    reasons: tuple[str, ...]

    def __bool__(self) -> bool:
        return self.should_recalibrate


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values)) if values else Decimal(0)


def concordance(samples: list[OutcomeSample]) -> Decimal:
    """Fraction of comparable prediction pairs whose predicted order matches the realized order
    — a rank-agreement measure (0.5 ≈ coin-flip, 1.0 = perfect ordering). Pairs that tie on
    either side are not comparable and are excluded; with fewer than two comparable pairs there
    is no signal, reported as 0.5 (neutral)."""
    concordant = 0
    comparable = 0
    n = len(samples)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = samples[i], samples[j]
            if a.predicted == b.predicted or a.realized == b.realized:
                continue
            comparable += 1
            pred_order = a.predicted > b.predicted
            real_order = a.realized > b.realized
            if pred_order == real_order:
                concordant += 1
    if comparable == 0:
        return Decimal("0.5")
    return (Decimal(concordant) / Decimal(comparable)).quantize(Decimal("0.001"))


def drift_report(samples: list[OutcomeSample]) -> DriftReport:
    """Aggregate a realized-outcome sample into the three drift statistics. Pure over the
    sample — the task's only job is to assemble honest (predicted, realized) pairs."""
    errors = [s.predicted - s.realized for s in samples]
    abs_errors = [abs(e) for e in errors]
    return DriftReport(
        n=len(samples),
        mean_error=_mean(errors).quantize(Decimal("0.01")),
        mae=_mean(abs_errors).quantize(Decimal("0.01")),
        concordance=concordance(samples),
    )


def should_recalibrate(
    report: DriftReport, thresholds: DriftThresholds | None = None
) -> RecalibrationDecision:
    """Turn a drift report into a recommendation. An under-powered sample never recommends a
    recalibration (a config release on thin evidence is worse than waiting) — it reports OK
    with the reason, so the log distinguishes "healthy" from "not enough data yet"."""
    t = thresholds or DriftThresholds()
    reasons: list[str] = []

    if report.n < t.min_samples:
        return RecalibrationDecision(
            should_recalibrate=False,
            severity=DriftSeverity.OK,
            report=report,
            reasons=(f"insufficient_sample({report.n}<{t.min_samples})",),
        )

    abs_bias = abs(report.mean_error)
    severity = DriftSeverity.OK

    if abs_bias >= t.bias_recalibrate:
        severity = DriftSeverity.RECALIBRATE
        reasons.append(f"bias_{'hot' if report.mean_error > 0 else 'cold'}({report.mean_error})")
    elif abs_bias >= t.bias_watch:
        severity = DriftSeverity.WATCH
        reasons.append(f"bias_watch({report.mean_error})")

    if report.concordance < t.concordance_floor:
        severity = DriftSeverity.RECALIBRATE
        reasons.append(f"rank_agreement_low({report.concordance})")

    return RecalibrationDecision(
        should_recalibrate=severity is DriftSeverity.RECALIBRATE,
        severity=severity,
        report=report,
        reasons=tuple(reasons) or ("within_tolerance",),
    )


__all__ = [
    "DriftReport",
    "DriftSeverity",
    "DriftThresholds",
    "OutcomeSample",
    "RecalibrationDecision",
    "concordance",
    "drift_report",
    "should_recalibrate",
]
