from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

TierValue = str | list[str] | None


class TierMapping(BaseModel):
    """Configurable role-to-tier mapping.

    ELAN projects vary wildly in tier naming. These fields describe roles in the
    normalized model; their values are actual EAF `TIER_ID` strings.
    """

    model_config = ConfigDict(extra="allow")

    reference: TierValue = None
    phrase: TierValue = None
    words: TierValue = None
    morphemes: TierValue = None
    gloss: TierValue = None
    translation: TierValue = None
    metadata: dict[str, str] = Field(default_factory=dict)

    def configured_roles(self) -> dict[str, str]:
        roles: dict[str, str] = {}
        for role in ("reference", "phrase", "words", "morphemes", "gloss", "translation"):
            for index, tier_id in enumerate(self.role_tiers(role), start=1):
                key = role if index == 1 else f"{role}[{index}]"
                roles[key] = tier_id

        for role, tier_id in self.metadata.items():
            if tier_id:
                roles[f"metadata.{role}"] = tier_id

        for role, tier_id in (self.model_extra or {}).items():
            if isinstance(tier_id, str) and tier_id:
                roles[role] = tier_id

        return roles

    def role_tiers(self, role: str) -> list[str]:
        value = getattr(self, role, None)
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list):
            return [item for item in value if item]
        return []


class RenderConfig(BaseModel):
    title: str | None = None
    language: str = "und"
    theme: str = "light"
    text_direction: str = "auto"
    show_timestamps: bool = True
    audio_links: bool = False
    collapsible_morphology: bool = False
    gloss_abbreviations: dict[str, str] = Field(default_factory=dict)

    @field_validator("text_direction")
    @classmethod
    def validate_text_direction(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"auto", "ltr", "rtl"}:
            msg = "text_direction must be one of: auto, ltr, rtl"
            raise ValueError(msg)
        return normalized

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"light", "dark", "system"}:
            msg = "theme must be one of: light, dark, system"
            raise ValueError(msg)
        return normalized


class ProjectConfig(BaseModel):
    tiers: TierMapping = Field(default_factory=TierMapping)
    render: RenderConfig = Field(default_factory=RenderConfig)


def load_config(path: Path | None) -> ProjectConfig:
    if path is None:
        return ProjectConfig()

    with path.open("r", encoding="utf-8") as handle:
        payload: Any = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        msg = f"Config file must contain a YAML mapping: {path}"
        raise ValueError(msg)
    return ProjectConfig.model_validate(payload)
