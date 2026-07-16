"""Scoring drift/recalibration decision unit tests (no DB) — the pure calibration math behind
"retrain where appropriate" (§25.1 #5): bias, rank concordance, and the gated recommendation.
"""

from decimal import Decimal

from deallens.modules.scoring.calibration import (
    DriftSeverity,
    DriftThresholds,
    OutcomeSample,
    concordance,
    drift_report,
    should_recalibrate,
)


def _samples(pairs: list[tuple[str, str]]) -> list[OutcomeSample]:
    return [OutcomeSample(predicted=Decimal(p), realized=Decimal(r)) for p, r in pairs]


def test_perfect_calibration_is_ok() -> None:
    report = drift_report(_samples([(str(v), str(v)) for v in range(40, 100)]))
    assert report.mean_error == Decimal("0.00")
    decision = should_recalibrate(report)
    assert not decision.should_recalibrate
    assert decision.severity is DriftSeverity.OK


def test_insufficient_sample_never_recommends() -> None:
    report = drift_report(_samples([("90", "40"), ("80", "30")]))  # huge bias but n=2
    decision = should_recalibrate(report)
    assert not decision.should_recalibrate
    assert any("insufficient_sample" in r for r in decision.reasons)


def test_hot_bias_triggers_recalibration() -> None:
    # Scores run ~15 points hot across a healthy sample → recommend recalibration.
    report = drift_report(_samples([(str(60 + (i % 5)), str(45 + (i % 5))) for i in range(80)]))
    assert report.mean_error > Decimal("10")
    decision = should_recalibrate(report)
    assert decision.should_recalibrate
    assert decision.severity is DriftSeverity.RECALIBRATE
    assert any("bias_hot" in r for r in decision.reasons)


def test_watch_band_does_not_recalibrate() -> None:
    report = drift_report(_samples([(str(60 + (i % 5)), str(53 + (i % 5))) for i in range(80)]))
    thresholds = DriftThresholds()
    assert thresholds.bias_watch <= abs(report.mean_error) < thresholds.bias_recalibrate
    decision = should_recalibrate(report)
    assert not decision.should_recalibrate
    assert decision.severity is DriftSeverity.WATCH


def test_concordance_ordering() -> None:
    # Predicted order perfectly matches realized order → concordance 1.0.
    perfect = _samples([("10", "20"), ("20", "40"), ("30", "60")])
    assert concordance(perfect) == Decimal("1.000")
    # Predicted order is the exact reverse of realized → 0.0.
    inverted = _samples([("10", "60"), ("20", "40"), ("30", "20")])
    assert concordance(inverted) == Decimal("0.000")


def test_low_rank_agreement_triggers_recalibration() -> None:
    # Unbiased on average but ordering is inverted → rank agreement floor catches it.
    pairs = [(str(50 + i), str(50 - i)) for i in range(-30, 30)]
    report = drift_report(_samples(pairs))
    assert abs(report.mean_error) < DriftThresholds().bias_watch  # ~unbiased
    assert report.concordance < DriftThresholds().concordance_floor
    decision = should_recalibrate(report)
    assert decision.should_recalibrate
    assert any("rank_agreement_low" in r for r in decision.reasons)
