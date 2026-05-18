from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def normalize_annotation_value(value: str | None) -> str:
    """Normalize ELAN text without destroying linguistically meaningful spacing."""

    if value is None:
        return ""
    return unicodedata.normalize("NFC", value.strip())


def infer_text_direction(values: Iterable[str]) -> str:
    """Infer direction from the first strong Unicode bidi character."""

    for value in values:
        for char in value:
            bidi = unicodedata.bidirectional(char)
            if bidi in {"R", "AL"}:
                return "rtl"
            if bidi == "L":
                return "ltr"
    return "auto"


def format_ms(value: int | None) -> str:
    if value is None:
        return ""
    total_seconds, ms = divmod(value, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}.{ms:03d}"
    return f"{minutes:d}:{seconds:02d}.{ms:03d}"


def safe_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    asciiish = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", asciiish).strip("-").lower()
    return slug or "document"
