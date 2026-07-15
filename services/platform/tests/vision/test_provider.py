"""Vision provider abstraction tests (no DB, no network) — the model-upgrade seam (§27.2/§13.4):
the deterministic bootstrap provider returns the model-agnostic `PhotoPerception`, honest about
what a hint can and can't tell it, and the registry resolves providers by version.
"""

import pytest

from deallens.modules.vision import provider
from deallens.modules.vision.provider import HeuristicVisionProvider, PhotoInput
from deallens.modules.vision.schemas import RedFlagType

pytestmark = pytest.mark.asyncio


def _pid(n: int) -> object:
    import uuid
    return uuid.UUID(int=n)


async def test_heuristic_reads_room_and_grade_hint() -> None:
    p = HeuristicVisionProvider()
    out = await p.analyze(PhotoInput(
        photo_id=_pid(1), hints={"room_type": "kitchen", "condition_grade": 4}
    ))
    assert out.room_type == "kitchen"
    assert out.condition_grade == 4
    assert out.confidence > 0  # graded + usable → moderate confidence
    assert out.model_id == "heuristic-v0"


async def test_heuristic_unknown_room_is_none_not_guessed() -> None:
    p = HeuristicVisionProvider()
    out = await p.analyze(PhotoInput(photo_id=_pid(2), hints={"condition_grade": 3}))
    assert out.room_type is None
    assert out.condition_grade is None  # unusable without a room → unknown, not grade 3
    assert out.confidence == 0.0


async def test_heuristic_staged_photo_low_confidence() -> None:
    p = HeuristicVisionProvider()
    out = await p.analyze(PhotoInput(
        photo_id=_pid(3),
        hints={"room_type": "kitchen", "condition_grade": 5, "staged_or_virtual": True},
    ))
    assert out.quality.staged_or_virtual is True
    assert out.confidence < 0.5  # staged photos are down-weighted


async def test_heuristic_coerces_red_flags_and_drops_bad_ones() -> None:
    p = HeuristicVisionProvider()
    out = await p.analyze(PhotoInput(
        photo_id=_pid(4),
        hints={
            "room_type": "basement", "condition_grade": 2,
            "red_flags": [
                {"type": "foundation_crack", "severity": "severe"},
                {"type": "not_a_real_flag", "severity": "severe"},  # dropped, not crashed
            ],
        },
    ))
    assert len(out.red_flags) == 1
    assert out.red_flags[0].type is RedFlagType.FOUNDATION_CRACK


async def test_registry_resolves_default_and_by_version() -> None:
    default = provider.get_provider()
    pinned = provider.get_provider(provider.DEFAULT_PIPELINE_VERSION)
    assert default.pipeline_version == pinned.pipeline_version == "heuristic-v0"


async def test_registry_unknown_version_raises() -> None:
    with pytest.raises(KeyError):
        provider.get_provider("nonexistent-model-v99")
