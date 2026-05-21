from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from elan_pretty.config import ProjectConfig, RenderConfig, TierMapping
from elan_pretty.raw import RawEafDocument
from elan_pretty.utils import safe_slug


class MappingMatchHints(BaseModel):
    tier_ids: list[str] = Field(default_factory=list)
    tier_suffixes: list[str] = Field(default_factory=list)
    source_file: str | None = None


class MappingProfile(BaseModel):
    id: str
    name: str
    tiers: TierMapping
    render: RenderConfig = Field(default_factory=RenderConfig)
    description: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    updated_at: str | None = None
    match: MappingMatchHints = Field(default_factory=MappingMatchHints)

    def as_config(self) -> ProjectConfig:
        return ProjectConfig(tiers=self.tiers, render=self.render)


class MappingRegistrySuggestion(BaseModel):
    profile: MappingProfile
    confidence: float = Field(ge=0.0, le=1.0)
    present_roles: dict[str, str] = Field(default_factory=dict)
    missing_roles: dict[str, str] = Field(default_factory=dict)
    reason: str


class MappingRegistry:
    """Load and score reusable tier mappings.

    Profiles are intentionally plain YAML so a future bot admin or corpus worker
    can edit them without a database. "Learning" is explicit: when a user says
    to save a corrected mapping, it becomes another profile to score next time.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def list_profiles(self) -> list[MappingProfile]:
        if not self.root.exists():
            return []
        profiles: list[MappingProfile] = []
        for path in sorted(self.root.glob("*.yaml")):
            try:
                profiles.append(self.load(path.stem))
            except (OSError, ValueError, yaml.YAMLError):
                continue
        return profiles

    def load(self, profile_id: str) -> MappingProfile:
        path = self._path(profile_id)
        if not path.exists():
            msg = f"No saved mapping profile named {profile_id!r}"
            raise ValueError(msg)
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            msg = f"Mapping profile must be a YAML mapping: {path}"
            raise ValueError(msg)
        return MappingProfile.model_validate(payload)

    def save(
        self,
        name: str,
        config: ProjectConfig,
        *,
        raw: RawEafDocument | None = None,
        profile_id: str | None = None,
        description: str | None = None,
        overwrite: bool = True,
    ) -> MappingProfile:
        self.root.mkdir(parents=True, exist_ok=True)
        resolved_id = safe_slug(profile_id or name)
        path = self._path(resolved_id)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing: MappingProfile | None = None
        if path.exists():
            if not overwrite:
                msg = f"Mapping profile already exists: {resolved_id}"
                raise ValueError(msg)
            existing = self.load(resolved_id)

        profile = MappingProfile(
            id=resolved_id,
            name=name,
            description=description,
            tiers=config.tiers,
            render=config.render,
            created_at=existing.created_at if existing else now,
            updated_at=now if existing else None,
            match=self._match_hints(raw, config),
        )
        path.write_text(_dump_yaml(profile.model_dump(exclude_none=True)), encoding="utf-8")
        return profile

    def suggest(self, raw: RawEafDocument) -> MappingRegistrySuggestion | None:
        scored = [
            suggestion
            for profile in self.list_profiles()
            if (suggestion := self._score_profile(profile, raw)) is not None
        ]
        if not scored:
            return None
        return max(scored, key=lambda item: item.confidence)

    def _score_profile(
        self, profile: MappingProfile, raw: RawEafDocument
    ) -> MappingRegistrySuggestion | None:
        configured = profile.tiers.configured_roles()
        configured = {
            role: tier_id
            for role, tier_id in configured.items()
            if not role.startswith("metadata.")
        }
        if not configured:
            return None

        present_roles = {
            role: tier_id for role, tier_id in configured.items() if tier_id in raw.tiers
        }
        missing_roles = {
            role: tier_id for role, tier_id in configured.items() if tier_id not in raw.tiers
        }
        role_weights = {
            "reference": 0.10,
            "phrase": 0.22,
            "words": 0.18,
            "morphemes": 0.18,
            "gloss": 0.18,
            "translation": 0.14,
        }
        possible = sum(role_weights.get(_base_role(role), 0.08) for role in configured)
        matched = sum(role_weights.get(_base_role(role), 0.08) for role in present_roles)
        role_score = matched / possible if possible else 0.0

        raw_tier_ids = set(raw.tier_ids())
        hinted_tiers = set(profile.match.tier_ids)
        hint_score = (
            len(raw_tier_ids & hinted_tiers) / len(hinted_tiers)
            if hinted_tiers
            else 0.0
        )
        suffix_score = _suffix_overlap(raw_tier_ids, set(profile.match.tier_suffixes))
        confidence = min(1.0, (0.72 * role_score) + (0.18 * hint_score) + (0.10 * suffix_score))
        if confidence < 0.35:
            return None

        reason = f"{len(present_roles)} of {len(configured)} configured role tiers are present"
        if suffix_score > 0:
            reason = f"{reason}; tier suffixes look familiar"
        return MappingRegistrySuggestion(
            profile=profile,
            confidence=confidence,
            present_roles=present_roles,
            missing_roles=missing_roles,
            reason=reason,
        )

    def _match_hints(
        self, raw: RawEafDocument | None, config: ProjectConfig
    ) -> MappingMatchHints:
        tier_ids = (
            sorted(raw.tier_ids()) if raw else sorted(config.tiers.configured_roles().values())
        )
        suffixes = sorted({_tier_suffix(tier_id) for tier_id in tier_ids if _tier_suffix(tier_id)})
        return MappingMatchHints(
            tier_ids=tier_ids,
            tier_suffixes=suffixes,
            source_file=raw.path.name if raw else None,
        )

    def _path(self, profile_id: str) -> Path:
        return self.root / f"{safe_slug(profile_id)}.yaml"


def _dump_yaml(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _tier_suffix(tier_id: str) -> str | None:
    if "@" not in tier_id:
        return None
    suffix = tier_id.rsplit("@", 1)[-1].strip()
    return suffix or None


def _base_role(role: str) -> str:
    return role.split("[", 1)[0]


def _suffix_overlap(raw_tier_ids: set[str], profile_suffixes: set[str]) -> float:
    if not profile_suffixes:
        return 0.0
    raw_suffixes = {_tier_suffix(tier_id) for tier_id in raw_tier_ids}
    raw_suffixes.discard(None)
    if not raw_suffixes:
        return 0.0
    return len(raw_suffixes & profile_suffixes) / len(profile_suffixes)
