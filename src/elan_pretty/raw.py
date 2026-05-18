from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from elan_pretty.models import MediaDescriptor


@dataclass(slots=True)
class RawAnnotation:
    id: str
    tier_id: str
    value: str
    kind: str
    order: int
    time_slot_ref1: str | None = None
    time_slot_ref2: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    annotation_ref: str | None = None
    previous_annotation: str | None = None

    @property
    def is_alignable(self) -> bool:
        return self.kind == "ALIGNABLE_ANNOTATION"


@dataclass(slots=True)
class RawTier:
    id: str
    linguistic_type_ref: str | None = None
    parent_ref: str | None = None
    participant: str | None = None
    default_locale: str | None = None
    annotations: list[RawAnnotation] = field(default_factory=list)


@dataclass(slots=True)
class RawLinguisticType:
    id: str
    time_alignable: bool | None = None
    constraints: str | None = None


@dataclass(slots=True)
class RawEafDocument:
    path: Path
    time_slots: dict[str, int | None] = field(default_factory=dict)
    media_descriptors: list[MediaDescriptor] = field(default_factory=list)
    linguistic_types: dict[str, RawLinguisticType] = field(default_factory=dict)
    tiers: dict[str, RawTier] = field(default_factory=dict)
    annotations_by_id: dict[str, RawAnnotation] = field(default_factory=dict)
    children_by_ref: dict[str, list[RawAnnotation]] = field(
        default_factory=lambda: defaultdict(list)
    )
    warnings: list[str] = field(default_factory=list)

    def tier_ids(self) -> list[str]:
        return list(self.tiers.keys())

    def annotations_on_tier(self, tier_id: str | None) -> list[RawAnnotation]:
        if tier_id is None:
            return []
        tier = self.tiers.get(tier_id)
        if tier is None:
            return []
        return tier.annotations

    def children_of(self, annotation_id: str, tier_id: str | None = None) -> list[RawAnnotation]:
        children = self.children_by_ref.get(annotation_id, [])
        if tier_id is None:
            return list(children)
        return [child for child in children if child.tier_id == tier_id]
