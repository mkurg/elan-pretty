from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from elan_pretty.config import ProjectConfig, RenderConfig, TierMapping
from elan_pretty.raw import RawEafDocument, RawTier


STRUCTURAL_ROLES = ("reference", "phrase", "words", "morphemes", "gloss", "translation")


class TierInventoryItem(BaseModel):
    id: str
    parent_ref: str | None = None
    linguistic_type: str | None = None
    annotation_count: int = 0
    non_empty_count: int = 0
    time_alignable: bool | None = None
    constraints: str | None = None


class RoleSuggestion(BaseModel):
    role: str
    tier_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class TierDetectionResult(BaseModel):
    mapping: TierMapping
    confidence: float = Field(ge=0.0, le=1.0)
    roles: list[RoleSuggestion] = Field(default_factory=list)
    available_tiers: list[TierInventoryItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def as_config(self, render: RenderConfig | None = None) -> ProjectConfig:
        return ProjectConfig(tiers=self.mapping, render=render or RenderConfig())


@dataclass(frozen=True, slots=True)
class _TierStats:
    tier: RawTier
    annotation_count: int
    non_empty_count: int
    time_alignable: bool | None
    constraints: str | None
    values: tuple[str, ...]
    average_chars: float
    average_tokens: float
    uppercase_ratio: float
    boundary_ratio: float


class TierDetector:
    """Heuristic role detector for ELAN tier inventories.

    ELAN files do not carry a standard "this is the morpheme tier" marker, so
    detection is intentionally conservative: tier names, hierarchy, annotation
    counts, linguistic type constraints, and value shape all vote together. The
    result is a suggestion, not a hidden replacement for explicit config.
    """

    def suggest(self, raw: RawEafDocument) -> TierDetectionResult:
        stats = {tier_id: self._stats(raw, tier) for tier_id, tier in raw.tiers.items()}
        suggestions: dict[str, RoleSuggestion] = {}
        used: set[str] = set()

        for role in STRUCTURAL_ROLES:
            suggestion = self._best_role(role, raw, stats, used, suggestions)
            if suggestion is not None:
                suggestions[role] = suggestion
                used.add(suggestion.tier_id)

        mapping = self._expanded_mapping(raw, suggestions)
        role_list = [suggestions[role] for role in STRUCTURAL_ROLES if role in suggestions]
        confidence = self._overall_confidence(role_list)
        warnings = self._warnings(role_list)
        return TierDetectionResult(
            mapping=mapping,
            confidence=confidence,
            roles=role_list,
            available_tiers=self._inventory(raw),
            warnings=warnings,
        )

    def _best_role(
        self,
        role: str,
        raw: RawEafDocument,
        stats: dict[str, _TierStats],
        used: set[str],
        suggestions: dict[str, RoleSuggestion],
    ) -> RoleSuggestion | None:
        candidates: list[tuple[float, str, str]] = []
        for tier_id, tier_stats in stats.items():
            if tier_id in used and role != "reference":
                continue
            score, reason = self._score_role(role, raw, tier_stats, suggestions)
            if score > 0:
                candidates.append((score, tier_id, reason))

        if not candidates:
            return None

        score, tier_id, reason = max(candidates, key=lambda item: item[0])
        confidence = max(0.05, min(1.0, score))
        if confidence < 0.32:
            return None
        return RoleSuggestion(role=role, tier_id=tier_id, confidence=confidence, reason=reason)

    def _score_role(
        self,
        role: str,
        raw: RawEafDocument,
        stats: _TierStats,
        suggestions: dict[str, RoleSuggestion],
    ) -> tuple[float, str]:
        name_score = _name_score(stats.tier.id, role)
        hierarchy_score = self._hierarchy_score(role, stats, suggestions)
        shape_score = self._shape_score(role, raw, stats, suggestions)
        total = (0.46 * name_score) + (0.34 * hierarchy_score) + (0.20 * shape_score)

        reasons: list[str] = []
        if name_score >= 0.7:
            reasons.append("tier name matches common role labels")
        if hierarchy_score >= 0.7:
            reasons.append("tier hierarchy fits the role")
        if shape_score >= 0.7:
            reasons.append("annotation values/counts fit the role")
        if not reasons:
            reasons.append("weak but plausible heuristic match")
        return total, "; ".join(reasons)

    def _hierarchy_score(
        self,
        role: str,
        stats: _TierStats,
        suggestions: dict[str, RoleSuggestion],
    ) -> float:
        tier = stats.tier
        if role == "reference":
            child_count = sum(1 for item in suggestions.values() if item.tier_id == tier.id)
            if stats.time_alignable is True and tier.parent_ref is None:
                return 1.0
            if stats.time_alignable is True:
                return 0.85
            return 0.15 + (0.1 * child_count)

        reference = suggestions.get("reference")
        phrase = suggestions.get("phrase")
        words = suggestions.get("words")
        morphemes = suggestions.get("morphemes")

        if role in {"phrase", "translation"}:
            if reference and tier.parent_ref == reference.tier_id:
                return 1.0
            if tier.parent_ref is None:
                return 0.35
            return 0.45

        if role == "words":
            if phrase and tier.parent_ref == phrase.tier_id:
                return 1.0
            if reference and tier.parent_ref == reference.tier_id:
                return 0.58
            return 0.25

        if role == "morphemes":
            if words and tier.parent_ref == words.tier_id:
                return 1.0
            if phrase and tier.parent_ref == phrase.tier_id:
                return 0.58
            return 0.25

        if role == "gloss":
            if morphemes and tier.parent_ref == morphemes.tier_id:
                return 1.0
            if words and tier.parent_ref == words.tier_id:
                return 0.52
            return 0.28

        return 0.0

    def _shape_score(
        self,
        role: str,
        raw: RawEafDocument,
        stats: _TierStats,
        suggestions: dict[str, RoleSuggestion],
    ) -> float:
        if stats.annotation_count == 0:
            return 0.0

        if role == "reference":
            child_tiers = _children_of(raw, stats.tier.id)
            if stats.time_alignable is True and child_tiers:
                return 1.0
            if stats.time_alignable is True:
                return 0.8
            return 0.2

        if role == "phrase":
            token_score = _clamp(stats.average_tokens / 6.0)
            non_empty_score = _clamp(stats.non_empty_count / max(1, stats.annotation_count))
            return max(token_score, 0.45) * non_empty_score

        if role == "translation":
            token_score = _clamp(stats.average_tokens / 8.0)
            prose_score = 1.0 - min(stats.uppercase_ratio, 0.7)
            return (0.6 * token_score) + (0.4 * prose_score)

        if role == "words":
            phrase = suggestions.get("phrase")
            ratio_score = self._count_ratio(stats, raw, phrase.tier_id if phrase else None, target=2.0)
            token_score = 1.0 - _clamp((stats.average_tokens - 1.0) / 3.0)
            return (0.62 * ratio_score) + (0.38 * token_score)

        if role == "morphemes":
            words = suggestions.get("words")
            ratio_score = self._count_ratio(stats, raw, words.tier_id if words else None, target=1.35)
            boundary_score = min(1.0, stats.boundary_ratio * 3.0)
            token_score = 1.0 - _clamp((stats.average_tokens - 1.0) / 3.0)
            return (0.50 * ratio_score) + (0.25 * token_score) + (0.25 * boundary_score)

        if role == "gloss":
            morphemes = suggestions.get("morphemes")
            ratio_score = self._count_ratio(
                stats,
                raw,
                morphemes.tier_id if morphemes else None,
                target=1.0,
            )
            uppercase_score = min(1.0, stats.uppercase_ratio * 1.8)
            return (0.56 * ratio_score) + (0.44 * uppercase_score)

        return 0.0

    def _count_ratio(
        self,
        stats: _TierStats,
        raw: RawEafDocument,
        parent_tier_id: str | None,
        *,
        target: float,
    ) -> float:
        if parent_tier_id is None or parent_tier_id not in raw.tiers:
            return 0.45
        parent_count = max(1, len(raw.tiers[parent_tier_id].annotations))
        ratio = stats.annotation_count / parent_count
        if ratio <= 0:
            return 0.0
        return math.exp(-abs(math.log(ratio / target)))

    def _stats(self, raw: RawEafDocument, tier: RawTier) -> _TierStats:
        linguistic_type = (
            raw.linguistic_types.get(tier.linguistic_type_ref)
            if tier.linguistic_type_ref
            else None
        )
        values = tuple(annotation.value for annotation in tier.annotations if annotation.value)
        tokens = [len(value.split()) for value in values]
        chars = [len(value) for value in values]
        letters = [char for value in values for char in value if char.isalpha()]
        uppercase_letters = [char for char in letters if not char.islower()]
        boundary_values = [value for value in values if "-" in value or "=" in value]
        return _TierStats(
            tier=tier,
            annotation_count=len(tier.annotations),
            non_empty_count=len(values),
            time_alignable=linguistic_type.time_alignable if linguistic_type else None,
            constraints=linguistic_type.constraints if linguistic_type else None,
            values=values,
            average_chars=sum(chars) / len(chars) if chars else 0.0,
            average_tokens=sum(tokens) / len(tokens) if tokens else 0.0,
            uppercase_ratio=(len(uppercase_letters) / len(letters)) if letters else 0.0,
            boundary_ratio=(len(boundary_values) / len(values)) if values else 0.0,
        )

    def _inventory(self, raw: RawEafDocument) -> list[TierInventoryItem]:
        items: list[TierInventoryItem] = []
        for tier in raw.tiers.values():
            linguistic_type = (
                raw.linguistic_types.get(tier.linguistic_type_ref)
                if tier.linguistic_type_ref
                else None
            )
            items.append(
                TierInventoryItem(
                    id=tier.id,
                    parent_ref=tier.parent_ref,
                    linguistic_type=tier.linguistic_type_ref,
                    annotation_count=len(tier.annotations),
                    non_empty_count=sum(1 for annotation in tier.annotations if annotation.value),
                    time_alignable=linguistic_type.time_alignable if linguistic_type else None,
                    constraints=linguistic_type.constraints if linguistic_type else None,
                )
            )
        return items

    def _overall_confidence(self, roles: list[RoleSuggestion]) -> float:
        if not roles:
            return 0.0
        role_weights = {
            "reference": 0.12,
            "phrase": 0.22,
            "words": 0.18,
            "morphemes": 0.16,
            "gloss": 0.18,
            "translation": 0.14,
        }
        present = sum(role_weights.get(role.role, 0.1) * role.confidence for role in roles)
        possible = sum(role_weights.values())
        coverage_bonus = len(roles) / len(STRUCTURAL_ROLES)
        return min(1.0, (present / possible * 0.82) + (coverage_bonus * 0.18))

    def _warnings(self, roles: list[RoleSuggestion]) -> list[str]:
        present = {role.role for role in roles}
        warnings: list[str] = []
        for role in ("phrase", "words", "morphemes", "gloss", "translation"):
            if role not in present:
                warnings.append(f"No confident suggestion for {role!r} tier.")
        for suggestion in roles:
            if suggestion.confidence < 0.55:
                warnings.append(
                    f"Low-confidence suggestion for {suggestion.role!r}: {suggestion.tier_id}"
                )
        return warnings

    def _expanded_mapping(
        self, raw: RawEafDocument, suggestions: dict[str, RoleSuggestion]
    ) -> TierMapping:
        return TierMapping(
            reference=self._role_value(raw, suggestions, "reference"),
            phrase=self._role_value(raw, suggestions, "phrase"),
            words=self._role_value(raw, suggestions, "words"),
            morphemes=self._role_value(raw, suggestions, "morphemes"),
            gloss=self._role_value(raw, suggestions, "gloss"),
            translation=self._role_value(raw, suggestions, "translation"),
        )

    def _role_value(
        self, raw: RawEafDocument, suggestions: dict[str, RoleSuggestion], role: str
    ) -> str | list[str] | None:
        primary = self._role_tier(suggestions, role)
        if primary is None:
            return None
        primary_base = _tier_base(primary)
        peers = [
            tier_id
            for tier_id in raw.tier_ids()
            if _tier_base(tier_id) == primary_base and _name_score(tier_id, role) >= 0.4
        ]
        ordered = _primary_first(primary, peers)
        if len(ordered) <= 1:
            return primary
        return ordered

    def _role_tier(self, suggestions: dict[str, RoleSuggestion], role: str) -> str | None:
        suggestion = suggestions.get(role)
        return suggestion.tier_id if suggestion else None


def suggest_tier_mapping(raw: RawEafDocument) -> TierDetectionResult:
    return TierDetector().suggest(raw)


def expand_mapping_for_parallel_tiers(raw: RawEafDocument, mapping: TierMapping) -> TierMapping:
    """Expand a saved single-speaker mapping when matching speaker tiers exist."""

    payload = mapping.model_dump()
    for role in STRUCTURAL_ROLES:
        configured = mapping.role_tiers(role)
        if not configured:
            continue
        peers: list[str] = []
        for tier_id in configured:
            base = _tier_base(tier_id)
            peers.extend(
                candidate
                for candidate in raw.tier_ids()
                if _tier_base(candidate) == base and _name_score(candidate, role) >= 0.4
            )
        ordered = _primary_first(configured[0], peers or configured)
        payload[role] = ordered if len(ordered) > 1 else ordered[0]
    return TierMapping.model_validate(payload)


def _children_of(raw: RawEafDocument, tier_id: str) -> list[RawTier]:
    return [tier for tier in raw.tiers.values() if tier.parent_ref == tier_id]


def _tier_base(tier_id: str) -> str:
    if "@" in tier_id:
        return tier_id.split("@", 1)[0].casefold()
    parts = re.split(r"[:/._\-\s]+", tier_id)
    if len(parts) >= 2 and 0 < len(parts[-1]) <= 16:
        return tier_id[: -len(parts[-1])].rstrip(":/._- ").casefold()
    return tier_id.casefold()


def _speaker_label(raw: RawEafDocument, tier_id: str) -> str | None:
    tier = raw.tiers.get(tier_id)
    if tier is None:
        return None
    suffix = _tier_suffix_label(tier.id)
    if suffix:
        return suffix
    if tier.participant:
        return tier.participant
    if tier.parent_ref:
        return _speaker_label(raw, tier.parent_ref)
    return None


def _primary_first(primary: str, tier_ids: list[str]) -> list[str]:
    unique = list(dict.fromkeys(tier_ids))
    return sorted(unique, key=lambda tier_id: (tier_id != primary, tier_id))


def _tier_suffix_label(tier_id: str) -> str | None:
    if "@" in tier_id:
        suffix = tier_id.rsplit("@", 1)[-1].strip()
        return suffix or None
    parts = re.split(r"[:/._\-\s]+", tier_id)
    if len(parts) >= 2 and 0 < len(parts[-1]) <= 16:
        return parts[-1]
    return None


def _name_score(tier_id: str, role: str) -> float:
    labels = _tier_labels(tier_id)
    exact = ROLE_LABELS[role]
    contains = ROLE_CONTAINS[role]
    if any(label in exact for label in labels):
        return 1.0
    if any(any(pattern in label for pattern in contains) for label in labels):
        return 0.82
    if any(re.fullmatch(pattern, label) for pattern in ROLE_REGEXES[role] for label in labels):
        return 0.75
    return 0.0


def _tier_labels(tier_id: str) -> set[str]:
    lowered = tier_id.casefold()
    pieces = re.split(r"[@:/._\-\s]+", lowered)
    labels = {lowered, *[piece for piece in pieces if piece]}
    if "@" in lowered:
        labels.add(lowered.split("@", 1)[0])
    return labels


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


ROLE_LABELS: dict[str, set[str]] = {
    "reference": {"ref", "reference", "segment", "segments", "seg", "id", "utterance", "u"},
    "phrase": {"tx", "txt", "text", "po", "phrase", "orth", "transcription", "sentence"},
    "words": {"wd", "word", "words", "w", "tok", "token", "tokens"},
    "morphemes": {"mb", "morph", "morpheme", "morphemes", "morphs", "mrp"},
    "gloss": {"ge", "gl", "gloss", "glosses", "mgl", "morphgloss", "mg"},
    "translation": {"ft", "free", "translation", "translations", "trans", "eng", "en"},
}

ROLE_CONTAINS: dict[str, set[str]] = {
    "reference": {"ref", "segment", "utter"},
    "phrase": {"phrase", "transcription", "orth", "sentence"},
    "words": {"word", "token"},
    "morphemes": {"morph", "morpheme"},
    "gloss": {"gloss"},
    "translation": {"translation", "free"},
}

ROLE_REGEXES: dict[str, set[str]] = {
    "reference": {r"ref\d*", r"seg\d*"},
    "phrase": {r"tx\d*", r"text\d*", r"po\d*"},
    "words": {r"wd\d*", r"word\d*", r"w\d*"},
    "morphemes": {r"mb\d*", r"morph\d*"},
    "gloss": {r"ge\d*", r"gl\d*"},
    "translation": {r"ft\d*", r"tr\d*", r"en\d*"},
}
