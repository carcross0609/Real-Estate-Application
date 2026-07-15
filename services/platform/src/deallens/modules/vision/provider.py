"""Vision-model provider abstraction (03 §27, §13.4) — the seam that makes model upgrades free.

Perception is the one part of §27 that is inherently a model call ("vision models *perceive*",
§27.1). Everything downstream — aggregation (§27.3) and the rehab cost model (§27.4) — is
deterministic and consumes a `PhotoPerception`, which is model-agnostic by construction. So a
newer/better vision model is just a new `VisionProvider` that returns the same schema: nothing in
the aggregation, rehab pricing, scoring factors, or storage changes. Providers are selected by
`pipeline_version`, and `photo_analyses` stores that version per row, so a candidate model is
diffed against the incumbent on the eval set before it is promoted (§13.4/§27.6) — the swap is a
config change, never a code rewrite.

Two providers ship here:
- **`HeuristicVisionProvider`** — deterministic, no LLM. It reads structured hints already present
  on a photo (a feed-supplied room label / grade, or none) and returns a calibrated-but-humble
  perception. It's the cold-start/bootstrap provider and the one the tests drive, so the whole
  pipeline runs end-to-end today without a model wired up or a network call.
- The concrete multimodal LLM provider (Claude et al.) is a deferred adapter: it needs the
  versioned prompt rubric in `vision/prompts/` (§27.2) and live API wiring, the same way the
  ingestion feed adapters are deferred behind their base class. It will register here under its
  own `pipeline_version` and return the identical `PhotoPerception`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from deallens.modules.vision.schemas import (
    PhotoFindings,
    PhotoPerception,
    PhotoQuality,
    RedFlag,
    RedFlagSeverity,
    RedFlagType,
)


@dataclass(frozen=True, slots=True)
class PhotoInput:
    """One photo handed to a provider. `image_ref` is the S3 key / URL a real model would fetch;
    `hints` carries any structured signal already known (feed-supplied room label, MLS grade,
    virtual-staging flag) that a deterministic provider can use and a real model may ignore in
    favor of the pixels."""

    photo_id: UUID
    image_ref: str | None = None
    position: int = 0
    hints: dict[str, Any] = field(default_factory=dict)


class VisionProvider(Protocol):
    """The contract every vision model plugs into (§27.2). `analyze` is async because a real
    provider is a network call; the deterministic one is sync-under-the-hood but honors the same
    signature so the service is provider-agnostic."""

    pipeline_version: str
    model_id: str

    async def analyze(self, photo: PhotoInput) -> PhotoPerception: ...


_VALID_ROOMS = {
    "kitchen", "bath", "bedroom", "living", "exterior_front", "exterior_rear", "roof",
    "garage", "basement", "yard", "utility", "floorplan", "other",
}


class HeuristicVisionProvider:
    """Deterministic bootstrap provider (§27.5 degraded-but-honest). Derives a perception from a
    photo's structured hints — never from pixels — and reports a deliberately modest confidence so
    a downstream reader never mistakes the bootstrap for a real model read. A photo with no usable
    hint returns an *unknown* perception (grade None), which is the truthful answer, not grade 3.
    """

    pipeline_version = "heuristic-v0"
    model_id = "heuristic-v0"

    async def analyze(self, photo: PhotoInput) -> PhotoPerception:
        hints = photo.hints or {}
        room = hints.get("room_type")
        room = room if isinstance(room, str) and room in _VALID_ROOMS else None

        grade_hint = hints.get("condition_grade")
        grade = grade_hint if isinstance(grade_hint, int) and 1 <= grade_hint <= 5 else None

        staged = bool(hints.get("staged_or_virtual", False))
        usable = bool(hints.get("photo_usable", True)) and room is not None

        red_flags: list[RedFlag] = []
        for raw in hints.get("red_flags", []) or []:
            flag = _coerce_flag(raw)
            if flag is not None:
                red_flags.append(flag)

        # Confidence: a graded, usable, unstaged photo is a moderate 0.6 (it's still only a hint);
        # anything missing drags it down. Never a confident 1.0 from a heuristic.
        confidence = 0.0
        if usable and grade is not None:
            confidence = 0.3 if staged else 0.6

        return PhotoPerception(
            room_type=room,
            condition_grade=grade if usable else None,
            findings=PhotoFindings(room_confidence=confidence),
            red_flags=red_flags,
            quality=PhotoQuality(photo_usable=usable, staged_or_virtual=staged),
            confidence=confidence,
            model_id=self.model_id,
            pipeline_version=self.pipeline_version,
            cost_usd=Decimal("0"),
        )


def _coerce_flag(raw: Any) -> RedFlag | None:
    """Turn a hint dict into a validated `RedFlag`, tolerating unknown types/severities by
    dropping them (a bad hint must not crash the pipeline — §14.4 flag-don't-fail)."""
    if not isinstance(raw, dict):
        return None
    try:
        return RedFlag(
            type=RedFlagType(raw["type"]),
            severity=RedFlagSeverity(raw.get("severity", "possible")),
            photo_evidence=raw.get("photo_evidence"),
        )
    except (KeyError, ValueError):
        return None


# Provider registry (§13.4): pipeline_version → provider. A new model registers a new entry; the
# service resolves the requested version, defaulting to the current bootstrap provider.
_PROVIDERS: dict[str, VisionProvider] = {
    HeuristicVisionProvider.pipeline_version: HeuristicVisionProvider(),
}
DEFAULT_PIPELINE_VERSION = HeuristicVisionProvider.pipeline_version


def register_provider(provider: VisionProvider) -> None:
    """Register a vision provider under its `pipeline_version` (called at startup by whichever
    concrete model adapter is wired in)."""
    _PROVIDERS[provider.pipeline_version] = provider


def get_provider(pipeline_version: str | None = None) -> VisionProvider:
    """Resolve a provider by version (§13.4). Unknown version → `KeyError`; None → the default
    (current) provider, so callers that don't pin a version always get the live model."""
    if pipeline_version is None:
        return _PROVIDERS[DEFAULT_PIPELINE_VERSION]
    return _PROVIDERS[pipeline_version]


__all__ = [
    "DEFAULT_PIPELINE_VERSION",
    "HeuristicVisionProvider",
    "PhotoInput",
    "VisionProvider",
    "get_provider",
    "register_provider",
]
