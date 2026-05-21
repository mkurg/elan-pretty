from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lxml")

from elan_pretty.config import ProjectConfig, TierMapping
from elan_pretty.normalize import EafNormalizer
from elan_pretty.parser import EafParser


FIXTURE = Path(__file__).parent / "fixtures" / "minimal.eaf"
TWO_SPEAKER_FIXTURE = Path(__file__).parent / "fixtures" / "two_speakers.eaf"


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


def test_normalizes_multiple_speaker_tier_bundles() -> None:
    raw = EafParser().parse(TWO_SPEAKER_FIXTURE)
    raw.tiers["ref@A"].participant = "Speaker 1"
    raw.tiers["tx@A"].participant = "Speaker 1"
    config = ProjectConfig(
        tiers=TierMapping(
            reference=["ref@A", "ref@B"],
            phrase=["tx@A", "tx@B"],
            words=["wd@A", "wd@B"],
            morphemes=["mb@A", "mb@B"],
            gloss=["ge@A", "ge@B"],
            translation=["ft@A", "ft@B"],
        )
    )

    document = EafNormalizer(raw, config).normalize()

    assert [segment.speaker for segment in document.segments] == ["A", "B"]
    assert [segment.speaker_index for segment in document.segments] == [0, 1]
    assert [segment.phrase for segment in document.segments] == ["tsa mi", "nu ko"]
    assert document.segments[1].translation == "I went"
    assert document.segments[1].words[0].morphemes[0].gloss == "1SG"


def test_display_gloss_mirrors_leipzig_boundaries() -> None:
    from elan_pretty.models import Morpheme, Word

    assert Morpheme(form="-mae", gloss="PL").display_gloss == "-PL"
    assert Morpheme(form="=r", gloss="LOC").display_gloss == "=LOC"
    assert Morpheme(form="pre-", gloss="NEG").display_gloss == "NEG-"
    assert Morpheme(form="-mae", gloss="-PL").display_gloss == "-PL"

    word = Word(
        surface="mumae",
        morphemes=[Morpheme(form="mu", gloss="be"), Morpheme(form="-mae", gloss="PL")],
    )
    assert word.morpheme_line == "mu-mae"
    assert word.gloss_line == "be-PL"


def test_gloss_tooltips_ignore_boundary_markers() -> None:
    from elan_pretty.render.html import HTMLRenderer

    rendered = HTMLRenderer()._format_gloss(
        "tree=LOC be-PL NEG-", {"LOC": "locative", "PL": "plural", "NEG": "negative"}
    )

    assert 'title="locative"' in rendered
    assert 'title="plural"' in rendered
    assert 'title="negative"' in rendered
