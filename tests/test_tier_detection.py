from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lxml")

from elan_pretty.config import ProjectConfig, TierMapping
from elan_pretty.mapping_registry import MappingRegistry
from elan_pretty.parser import EafParser
from elan_pretty.tier_detection import suggest_tier_mapping


FIXTURE = Path(__file__).parent / "fixtures" / "minimal.eaf"


def test_suggests_minimal_eaf_mapping() -> None:
    raw = EafParser().parse(FIXTURE)

    suggestion = suggest_tier_mapping(raw)

    assert suggestion.mapping.reference == "ref"
    assert suggestion.mapping.phrase == "tx"
    assert suggestion.mapping.words == "wd"
    assert suggestion.mapping.morphemes == "mb"
    assert suggestion.mapping.gloss == "ge"
    assert suggestion.mapping.translation == "ft"
    assert suggestion.confidence > 0.85


def test_mapping_registry_saves_loads_and_scores(tmp_path: Path) -> None:
    raw = EafParser().parse(FIXTURE)
    registry = MappingRegistry(tmp_path)
    config = ProjectConfig(
        tiers=TierMapping(
            reference="ref",
            phrase="tx",
            words="wd",
            morphemes="mb",
            gloss="ge",
            translation="ft",
        )
    )

    profile = registry.save("Minimal fixture", config, raw=raw)
    loaded = registry.load(profile.id)
    suggestion = registry.suggest(raw)

    assert loaded.tiers.gloss == "ge"
    assert suggestion is not None
    assert suggestion.profile.id == profile.id
    assert suggestion.confidence >= 0.89
