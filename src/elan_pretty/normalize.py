from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from elan_pretty.config import ProjectConfig
from elan_pretty.models import InterlinearDocument, Morpheme, Segment, TextDirection, TierInfo, Word
from elan_pretty.raw import RawAnnotation, RawEafDocument
from elan_pretty.utils import infer_text_direction


class EafNormalizer:
    """Convert a raw ELAN annotation graph into normalized IGT examples."""

    def __init__(self, raw: RawEafDocument, config: ProjectConfig) -> None:
        self.raw = raw
        self.config = config
        self._anchor_cache: dict[str, str | None] = {}
        self._warnings: list[str] = []
        self._warning_keys: set[str] = set()
        self._by_anchor_tier: dict[str, dict[str, list[RawAnnotation]]] | None = None

    def normalize(self) -> InterlinearDocument:
        source_path = Path(self.raw.path)
        document = InterlinearDocument.empty(source_path, title=self.config.render.title)
        document.media = self.raw.media_descriptors
        document.tiers = self._tier_infos()
        document.warnings = [*self.raw.warnings, *self._validate_configured_tiers()]

        segments: list[Segment] = []
        for index, candidate in enumerate(self._segment_candidates(), start=1):
            segment = self._build_segment(index, candidate)
            if segment is not None:
                segments.append(segment)

        if not segments:
            document.warnings.append(
                "No segments were produced. Check the configured phrase/reference/words tiers."
            )

        document.warnings.extend(self._warnings)
        document.segments = segments
        return document

    def _tier_infos(self) -> list[TierInfo]:
        infos: list[TierInfo] = []
        for tier in self.raw.tiers.values():
            linguistic_type = (
                self.raw.linguistic_types.get(tier.linguistic_type_ref)
                if tier.linguistic_type_ref
                else None
            )
            infos.append(
                TierInfo(
                    id=tier.id,
                    linguistic_type=tier.linguistic_type_ref,
                    parent_ref=tier.parent_ref,
                    participant=tier.participant,
                    default_locale=tier.default_locale,
                    annotation_count=len(tier.annotations),
                    time_alignable=linguistic_type.time_alignable if linguistic_type else None,
                    constraints=linguistic_type.constraints if linguistic_type else None,
                )
            )
        return infos

    def _validate_configured_tiers(self) -> list[str]:
        warnings: list[str] = []
        available = ", ".join(self.raw.tier_ids()) or "(none)"
        for role, tier_id in self.config.tiers.configured_roles().items():
            if tier_id not in self.raw.tiers:
                warnings.append(
                    f"Configured tier for role {role!r} was not found: {tier_id!r}. "
                    f"Available tiers: {available}"
                )
        if not any(
            self._role_tier(role) for role in ("phrase", "reference", "words", "translation")
        ):
            warnings.append(
                "No segment-bearing tier is configured. Configure at least phrase, reference, "
                "words, or translation."
            )
        return warnings

    def _segment_candidates(self) -> list[RawAnnotation]:
        phrase_tier = self._role_tier("phrase")
        reference_tier = self._role_tier("reference")

        if phrase_tier:
            return self._sort_annotations(self.raw.annotations_on_tier(phrase_tier))

        if reference_tier:
            return self._sort_annotations(self.raw.annotations_on_tier(reference_tier))

        configured_tiers = [
            tier_id
            for tier_id in self.config.tiers.configured_roles().values()
            if tier_id in self.raw.tiers
        ]
        anchors: set[str] = set()
        for tier_id in configured_tiers:
            for annotation in self.raw.annotations_on_tier(tier_id):
                anchor_id = self._anchor_id(annotation)
                if anchor_id:
                    anchors.add(anchor_id)

        return self._sort_annotations(
            self.raw.annotations_by_id[anchor_id]
            for anchor_id in anchors
            if anchor_id in self.raw.annotations_by_id
        )

    def _build_segment(self, index: int, candidate: RawAnnotation) -> Segment | None:
        anchor_id = self._anchor_id(candidate)
        if anchor_id is None:
            self._warn_once(
                f"segment-anchor:{candidate.id}",
                f"Skipping annotation {candidate.id}; no time-aligned ancestor could be resolved.",
            )
            return None

        anchor = self.raw.annotations_by_id.get(anchor_id)
        segment_warnings: list[str] = []
        phrase = self._phrase_for(candidate, anchor_id)
        translation = self._first_text_for_role(anchor_id, "translation")
        words = self._words_for(anchor_id, candidate, segment_warnings)
        if not phrase:
            phrase = " ".join(word.surface for word in words if word.surface)
        if not words and phrase:
            words = self._fallback_words(phrase)
            segment_warnings.append("Words tier unavailable or empty; tokenized phrase by whitespace.")

        direction = self.config.render.text_direction
        if direction == "auto":
            direction = infer_text_direction(
                [
                    phrase,
                    translation or "",
                    *(word.surface for word in words),
                    *(morpheme.form for word in words for morpheme in word.morphemes),
                ]
            )

        return Segment(
            id=f"segment_{index:04d}",
            source_annotation_id=candidate.id,
            anchor_annotation_id=anchor_id,
            start_ms=anchor.start_ms if anchor else None,
            end_ms=anchor.end_ms if anchor else None,
            phrase=phrase,
            words=words,
            translation=translation,
            direction=TextDirection(direction),
            metadata=self._metadata_for(anchor_id),
            warnings=segment_warnings,
        )

    def _phrase_for(self, candidate: RawAnnotation, anchor_id: str) -> str:
        phrase_tier = self._role_tier("phrase")
        if phrase_tier and candidate.tier_id == phrase_tier:
            return candidate.value
        if phrase_tier:
            return self._first_text_on_tier(anchor_id, phrase_tier) or ""
        if candidate.value:
            return candidate.value
        return ""

    def _words_for(
        self,
        anchor_id: str,
        candidate: RawAnnotation,
        segment_warnings: list[str],
    ) -> list[Word]:
        word_tier = self._role_tier("words")
        if not word_tier:
            return []

        word_annotations = self._annotations_for_anchor(anchor_id, word_tier)
        phrase_tier = self._role_tier("phrase")
        if phrase_tier and candidate.tier_id == phrase_tier:
            descendant_words = [
                annotation
                for annotation in word_annotations
                if self._is_descendant_of(annotation, candidate.id)
            ]
            if descendant_words:
                word_annotations = descendant_words

        words: list[Word] = []
        for word_annotation in self._order_chain(word_annotations):
            words.append(self._word_from_annotation(word_annotation, segment_warnings))
        return words

    def _word_from_annotation(
        self,
        word_annotation: RawAnnotation,
        segment_warnings: list[str],
    ) -> Word:
        morpheme_tier = self._role_tier("morphemes")
        gloss_tier = self._role_tier("gloss")
        morpheme_annotations = (
            self._order_chain(self.raw.children_of(word_annotation.id, morpheme_tier))
            if morpheme_tier
            else []
        )

        morphemes: list[Morpheme] = []
        for morpheme_annotation in morpheme_annotations:
            gloss, gloss_id = self._gloss_for_morpheme(morpheme_annotation, gloss_tier)
            if gloss_tier and gloss is None:
                segment_warnings.append(
                    f"Morpheme {morpheme_annotation.id} has no gloss on tier {gloss_tier}."
                )
            morphemes.append(
                Morpheme(
                    id=morpheme_annotation.id,
                    gloss_id=gloss_id,
                    form=morpheme_annotation.value,
                    gloss=gloss,
                )
            )

        if not morphemes and word_annotation.value:
            morphemes.append(Morpheme(id=word_annotation.id, form=word_annotation.value))

        return Word(id=word_annotation.id, surface=word_annotation.value, morphemes=morphemes)

    def _gloss_for_morpheme(
        self, morpheme_annotation: RawAnnotation, gloss_tier: str | None
    ) -> tuple[str | None, str | None]:
        if not gloss_tier:
            return None, None
        gloss_annotations = self._order_chain(self.raw.children_of(morpheme_annotation.id, gloss_tier))
        gloss_values = [annotation.value for annotation in gloss_annotations if annotation.value]
        if not gloss_values:
            return None, None
        gloss_id = gloss_annotations[0].id if gloss_annotations else None
        return " / ".join(gloss_values), gloss_id

    def _metadata_for(self, anchor_id: str) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for role, tier_id in self.config.tiers.metadata.items():
            value = self._first_text_on_tier(anchor_id, tier_id)
            if value:
                metadata[role] = value

        structural_roles = {"reference", "phrase", "words", "morphemes", "gloss", "translation"}
        for role, tier_id in (self.config.tiers.model_extra or {}).items():
            if role in structural_roles or not isinstance(tier_id, str):
                continue
            value = self._first_text_on_tier(anchor_id, tier_id)
            if value:
                metadata[role] = value
        return metadata

    def _first_text_for_role(self, anchor_id: str, role: str) -> str | None:
        tier_id = self._role_tier(role)
        if not tier_id:
            return None
        return self._first_text_on_tier(anchor_id, tier_id)

    def _first_text_on_tier(self, anchor_id: str, tier_id: str) -> str | None:
        values = [annotation.value for annotation in self._annotations_for_anchor(anchor_id, tier_id)]
        values = [value for value in values if value]
        if not values:
            return None
        return " ".join(values)

    def _annotations_for_anchor(self, anchor_id: str, tier_id: str | None) -> list[RawAnnotation]:
        if not tier_id:
            return []
        grouped = self._anchor_tier_index()
        return grouped.get(anchor_id, {}).get(tier_id, [])

    def _anchor_tier_index(self) -> dict[str, dict[str, list[RawAnnotation]]]:
        if self._by_anchor_tier is not None:
            return self._by_anchor_tier

        grouped: dict[str, dict[str, list[RawAnnotation]]] = defaultdict(lambda: defaultdict(list))
        for annotation in self.raw.annotations_by_id.values():
            anchor_id = self._anchor_id(annotation)
            if anchor_id:
                grouped[anchor_id][annotation.tier_id].append(annotation)

        self._by_anchor_tier = grouped
        return grouped

    def _anchor_id(self, annotation: RawAnnotation, seen: frozenset[str] = frozenset()) -> str | None:
        cached = self._anchor_cache.get(annotation.id)
        if cached is not None or annotation.id in self._anchor_cache:
            return cached

        if annotation.is_alignable:
            self._anchor_cache[annotation.id] = annotation.id
            return annotation.id

        if annotation.id in seen:
            self._warn_once(
                f"cycle:{annotation.id}",
                f"Cycle detected while resolving parent annotations at {annotation.id}.",
            )
            self._anchor_cache[annotation.id] = None
            return None

        if not annotation.annotation_ref:
            self._warn_once(
                f"orphan:{annotation.id}",
                f"Reference annotation {annotation.id} has no ANNOTATION_REF.",
            )
            self._anchor_cache[annotation.id] = None
            return None

        parent = self.raw.annotations_by_id.get(annotation.annotation_ref)
        if parent is None:
            self._warn_once(
                f"missing-parent:{annotation.id}:{annotation.annotation_ref}",
                f"Annotation {annotation.id} references missing parent {annotation.annotation_ref}.",
            )
            self._anchor_cache[annotation.id] = None
            return None

        anchor_id = self._anchor_id(parent, seen | {annotation.id})
        self._anchor_cache[annotation.id] = anchor_id
        return anchor_id

    def _order_chain(self, annotations: list[RawAnnotation]) -> list[RawAnnotation]:
        if len(annotations) <= 1:
            return annotations

        by_id = {annotation.id: annotation for annotation in annotations}
        next_by_previous: dict[str, RawAnnotation] = {}
        duplicates: list[str] = []
        for annotation in annotations:
            previous = annotation.previous_annotation
            if previous and previous in by_id:
                if previous in next_by_previous:
                    duplicates.append(previous)
                else:
                    next_by_previous[previous] = annotation

        for previous in duplicates:
            self._warn_once(
                f"duplicate-previous:{previous}",
                f"Multiple annotations point to PREVIOUS_ANNOTATION {previous}; using XML order fallback.",
            )

        starts = [
            annotation
            for annotation in annotations
            if not annotation.previous_annotation or annotation.previous_annotation not in by_id
        ]
        ordered: list[RawAnnotation] = []
        seen: set[str] = set()

        for start in self._sort_annotations(starts):
            current: RawAnnotation | None = start
            while current is not None and current.id not in seen:
                ordered.append(current)
                seen.add(current.id)
                current = next_by_previous.get(current.id)

        remaining = [annotation for annotation in annotations if annotation.id not in seen]
        ordered.extend(self._sort_annotations(remaining))
        return ordered

    def _sort_annotations(self, annotations: list[RawAnnotation] | object) -> list[RawAnnotation]:
        return sorted(
            list(annotations),
            key=lambda annotation: (
                annotation.start_ms is None,
                annotation.start_ms if annotation.start_ms is not None else 10**18,
                annotation.end_ms if annotation.end_ms is not None else 10**18,
                annotation.order,
            ),
        )

    def _is_descendant_of(self, annotation: RawAnnotation, ancestor_id: str) -> bool:
        current = annotation
        seen: set[str] = set()
        while current.annotation_ref and current.id not in seen:
            if current.annotation_ref == ancestor_id:
                return True
            seen.add(current.id)
            parent = self.raw.annotations_by_id.get(current.annotation_ref)
            if parent is None:
                return False
            current = parent
        return False

    def _fallback_words(self, phrase: str) -> list[Word]:
        return [
            Word(surface=token, morphemes=[Morpheme(form=token)])
            for token in phrase.split()
            if token
        ]

    def _role_tier(self, role: str) -> str | None:
        tier_id = getattr(self.config.tiers, role, None)
        if isinstance(tier_id, str) and tier_id in self.raw.tiers:
            return tier_id
        return None

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._warning_keys:
            return
        self._warning_keys.add(key)
        self._warnings.append(message)
