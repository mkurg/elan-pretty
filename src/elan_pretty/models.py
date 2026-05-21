from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from elan_pretty.utils import mirror_gloss_boundaries


class TextDirection(str, Enum):
    auto = "auto"
    ltr = "ltr"
    rtl = "rtl"


class MediaDescriptor(BaseModel):
    url: str | None = None
    relative_url: str | None = None
    mime_type: str | None = None


class TierInfo(BaseModel):
    id: str
    linguistic_type: str | None = None
    parent_ref: str | None = None
    participant: str | None = None
    default_locale: str | None = None
    annotation_count: int = 0
    time_alignable: bool | None = None
    constraints: str | None = None


class Morpheme(BaseModel):
    id: str | None = None
    gloss_id: str | None = None
    form: str
    gloss: str | None = None

    @field_validator("form", "gloss", mode="before")
    @classmethod
    def coerce_text(cls, value: object) -> object:
        if value is None:
            return value
        return str(value)

    @computed_field
    @property
    def display_gloss(self) -> str | None:
        return mirror_gloss_boundaries(self.form, self.gloss)


class Word(BaseModel):
    id: str | None = None
    surface: str
    morphemes: list[Morpheme] = Field(default_factory=list)

    @computed_field
    @property
    def morpheme_line(self) -> str:
        if not self.morphemes:
            return self.surface
        return "".join(morpheme.form for morpheme in self.morphemes)

    @computed_field
    @property
    def gloss_line(self) -> str:
        return "".join(morpheme.display_gloss or "" for morpheme in self.morphemes)


class Segment(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    source_annotation_id: str | None = None
    anchor_annotation_id: str | None = None
    speaker: str | None = None
    speaker_index: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    phrase: str = ""
    words: list[Word] = Field(default_factory=list)
    translation: str | None = None
    direction: TextDirection = TextDirection.auto
    metadata: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_times(self) -> Segment:
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            msg = f"Segment {self.id} has end_ms earlier than start_ms"
            raise ValueError(msg)
        return self

    @computed_field
    @property
    def duration_ms(self) -> int | None:
        if self.start_ms is None or self.end_ms is None:
            return None
        return self.end_ms - self.start_ms


class InterlinearDocument(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    title: str
    source_file: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    media: list[MediaDescriptor] = Field(default_factory=list)
    tiers: list[TierInfo] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def empty(cls, source_path: Path, title: str | None = None) -> InterlinearDocument:
        return cls(
            id=source_path.stem,
            title=title or source_path.stem.replace("_", " "),
            source_file=str(source_path),
        )
