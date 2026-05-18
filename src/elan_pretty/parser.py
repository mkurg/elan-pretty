from __future__ import annotations

from pathlib import Path
from typing import Any

from elan_pretty.models import MediaDescriptor
from elan_pretty.raw import RawAnnotation, RawEafDocument, RawLinguisticType, RawTier
from elan_pretty.utils import normalize_annotation_value

try:  # lxml is required at runtime, but keeping import lazy-friendly helps local tooling.
    from lxml import etree
except ImportError:  # pragma: no cover - exercised only in under-provisioned environments.
    etree = None  # type: ignore[assignment]


class EafParser:
    """Parse EAF XML into a raw annotation graph.

    The parser deliberately preserves ELAN's own graph shape: alignable
    annotations point to time slots, reference annotations point to parent
    annotations, and symbolic subdivisions may use `PREVIOUS_ANNOTATION` to
    encode order. The normalizer later interprets those edges linguistically.
    """

    def parse(self, path: Path | str) -> RawEafDocument:
        source_path = Path(path)
        if etree is None:
            msg = "lxml is required to parse .eaf files. Install with: pip install lxml"
            raise RuntimeError(msg)

        raw = RawEafDocument(path=source_path)
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=True)
        try:
            tree = etree.parse(str(source_path), parser)
        except etree.XMLSyntaxError as exc:
            msg = f"Could not parse EAF XML {source_path}: {exc}"
            raise ValueError(msg) from exc

        root = tree.getroot()
        self._parse_time_order(root, raw)
        self._parse_media(root, raw)
        self._parse_linguistic_types(root, raw)
        self._parse_tiers(root, raw)
        self._validate_graph(raw)
        return raw

    def _parse_time_order(self, root: Any, raw: RawEafDocument) -> None:
        for slot in root.xpath("./*[local-name()='TIME_ORDER']/*[local-name()='TIME_SLOT']"):
            slot_id = slot.get("TIME_SLOT_ID")
            if not slot_id:
                raw.warnings.append("Encountered TIME_SLOT without TIME_SLOT_ID")
                continue
            raw.time_slots[slot_id] = self._parse_int(slot.get("TIME_VALUE"), raw, slot_id)

    def _parse_media(self, root: Any, raw: RawEafDocument) -> None:
        for media in root.xpath("./*[local-name()='HEADER']/*[local-name()='MEDIA_DESCRIPTOR']"):
            raw.media_descriptors.append(
                MediaDescriptor(
                    url=media.get("MEDIA_URL"),
                    relative_url=media.get("RELATIVE_MEDIA_URL"),
                    mime_type=media.get("MIME_TYPE"),
                )
            )

    def _parse_linguistic_types(self, root: Any, raw: RawEafDocument) -> None:
        for element in root.xpath("./*[local-name()='LINGUISTIC_TYPE']"):
            type_id = element.get("LINGUISTIC_TYPE_ID")
            if not type_id:
                raw.warnings.append("Encountered LINGUISTIC_TYPE without LINGUISTIC_TYPE_ID")
                continue
            time_alignable = self._parse_bool(element.get("TIME_ALIGNABLE"))
            raw.linguistic_types[type_id] = RawLinguisticType(
                id=type_id,
                time_alignable=time_alignable,
                constraints=element.get("CONSTRAINTS"),
            )

    def _parse_tiers(self, root: Any, raw: RawEafDocument) -> None:
        annotation_order = 0
        for tier_element in root.xpath("./*[local-name()='TIER']"):
            tier_id = tier_element.get("TIER_ID")
            if not tier_id:
                tier_id = f"__missing_tier_id_{len(raw.tiers) + 1}"
                raw.warnings.append(f"Encountered TIER without TIER_ID; assigned {tier_id}")

            tier = RawTier(
                id=tier_id,
                linguistic_type_ref=tier_element.get("LINGUISTIC_TYPE_REF"),
                parent_ref=tier_element.get("PARENT_REF"),
                participant=tier_element.get("PARTICIPANT"),
                default_locale=tier_element.get("DEFAULT_LOCALE"),
            )
            raw.tiers[tier_id] = tier

            for wrapper in tier_element.xpath("./*[local-name()='ANNOTATION']"):
                annotation_element = self._first_annotation_child(wrapper)
                if annotation_element is None:
                    raw.warnings.append(f"Tier {tier_id} contains empty ANNOTATION wrapper")
                    continue

                annotation_order += 1
                annotation = self._parse_annotation(
                    annotation_element,
                    tier_id=tier_id,
                    order=annotation_order,
                    raw=raw,
                )
                if annotation.id in raw.annotations_by_id:
                    original_id = annotation.id
                    annotation.id = f"{original_id}__duplicate_{annotation.order}"
                    raw.warnings.append(
                        f"Duplicate annotation id {original_id}; renamed duplicate to {annotation.id}"
                    )

                tier.annotations.append(annotation)
                raw.annotations_by_id[annotation.id] = annotation
                if annotation.annotation_ref:
                    raw.children_by_ref[annotation.annotation_ref].append(annotation)

    def _parse_annotation(
        self,
        element: Any,
        *,
        tier_id: str,
        order: int,
        raw: RawEafDocument,
    ) -> RawAnnotation:
        kind = etree.QName(element).localname
        annotation_id = element.get("ANNOTATION_ID") or f"__missing_annotation_id_{order}"
        if annotation_id.startswith("__missing"):
            raw.warnings.append(f"Tier {tier_id} annotation at position {order} has no id")

        value = normalize_annotation_value(self._annotation_text(element))
        if kind == "ALIGNABLE_ANNOTATION":
            slot_ref1 = element.get("TIME_SLOT_REF1")
            slot_ref2 = element.get("TIME_SLOT_REF2")
            start_ms = raw.time_slots.get(slot_ref1) if slot_ref1 else None
            end_ms = raw.time_slots.get(slot_ref2) if slot_ref2 else None
            if slot_ref1 and slot_ref1 not in raw.time_slots:
                raw.warnings.append(f"Annotation {annotation_id} references missing time slot {slot_ref1}")
            if slot_ref2 and slot_ref2 not in raw.time_slots:
                raw.warnings.append(f"Annotation {annotation_id} references missing time slot {slot_ref2}")
            return RawAnnotation(
                id=annotation_id,
                tier_id=tier_id,
                value=value,
                kind=kind,
                order=order,
                time_slot_ref1=slot_ref1,
                time_slot_ref2=slot_ref2,
                start_ms=start_ms,
                end_ms=end_ms,
            )

        if kind == "REF_ANNOTATION":
            return RawAnnotation(
                id=annotation_id,
                tier_id=tier_id,
                value=value,
                kind=kind,
                order=order,
                annotation_ref=element.get("ANNOTATION_REF"),
                previous_annotation=element.get("PREVIOUS_ANNOTATION"),
            )

        raw.warnings.append(f"Unsupported annotation element {kind} in tier {tier_id}")
        return RawAnnotation(
            id=annotation_id,
            tier_id=tier_id,
            value=value,
            kind=kind,
            order=order,
        )

    def _validate_graph(self, raw: RawEafDocument) -> None:
        for tier in raw.tiers.values():
            if tier.parent_ref and tier.parent_ref not in raw.tiers:
                raw.warnings.append(f"Tier {tier.id} references missing parent tier {tier.parent_ref}")
            if tier.linguistic_type_ref and tier.linguistic_type_ref not in raw.linguistic_types:
                raw.warnings.append(
                    f"Tier {tier.id} references missing linguistic type {tier.linguistic_type_ref}"
                )

        for annotation in raw.annotations_by_id.values():
            if annotation.annotation_ref and annotation.annotation_ref not in raw.annotations_by_id:
                raw.warnings.append(
                    f"Annotation {annotation.id} references missing parent annotation "
                    f"{annotation.annotation_ref}"
                )
            if (
                annotation.previous_annotation
                and annotation.previous_annotation not in raw.annotations_by_id
            ):
                raw.warnings.append(
                    f"Annotation {annotation.id} references missing previous annotation "
                    f"{annotation.previous_annotation}"
                )

    def _annotation_text(self, element: Any) -> str:
        for child in element:
            if etree.QName(child).localname == "ANNOTATION_VALUE":
                return "".join(child.itertext())
        return ""

    def _first_annotation_child(self, wrapper: Any) -> Any | None:
        for child in wrapper:
            local_name = etree.QName(child).localname
            if local_name in {"ALIGNABLE_ANNOTATION", "REF_ANNOTATION"}:
                return child
        return None

    def _parse_int(self, value: str | None, raw: RawEafDocument, label: str) -> int | None:
        if value is None:
            raw.warnings.append(f"Time slot {label} has no TIME_VALUE")
            return None
        try:
            return int(value)
        except ValueError:
            raw.warnings.append(f"Time slot {label} has non-integer TIME_VALUE {value!r}")
            return None

    def _parse_bool(self, value: str | None) -> bool | None:
        if value is None:
            return None
        return value.lower() == "true"
