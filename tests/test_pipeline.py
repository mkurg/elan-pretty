from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lxml")

from elan_pretty.config import ProjectConfig, TierMapping
from elan_pretty.normalize import EafNormalizer
from elan_pretty.parser import EafParser


FIXTURE = Path(__file__).parent / "fixtures" / "minimal.eaf"


def test_normalizes_eaf_to_interlinear_segments() -> None:
    raw = EafParser().parse(FIXTURE)
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

    document = EafNormalizer(raw, config).normalize()

    assert document.media[0].url == "file:///tmp/example.wav"
    assert len(document.segments) == 1
    segment = document.segments[0]
    assert segment.start_ms == 1200
    assert segment.end_ms == 4600
    assert segment.phrase == "tsa mi"
    assert segment.translation == "that man"
    assert [word.surface for word in segment.words] == ["tsa", "mi"]
    assert segment.words[0].morphemes[0].gloss == "DIST"
    assert segment.words[1].morphemes[0].gloss == "man"
