from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lxml")

from elan_pretty.bot.telegram_bot import BotSettings, ElanPrettyTelegramBot
from elan_pretty.config import ProjectConfig, TierMapping
from elan_pretty.parser import EafParser


FIXTURE = Path(__file__).parent / "fixtures" / "two_speakers.eaf"


def test_bot_expands_saved_profile_for_parallel_speaker_tiers(tmp_path: Path) -> None:
    settings = BotSettings(
        repo_root=tmp_path,
        work_dir=tmp_path / "bot",
        mapping_dir=tmp_path / "mappings",
        pages_dir=tmp_path / "published",
    )
    bot = ElanPrettyTelegramBot(settings)
    raw = EafParser().parse(FIXTURE)
    bot.registry.save(
        "Single speaker",
        ProjectConfig(
            tiers=TierMapping(
                reference="ref@A",
                phrase="tx@A",
                words="wd@A",
                morphemes="mb@A",
                gloss="ge@A",
                translation="ft@A",
            )
        ),
        raw=raw,
    )

    pending = bot._make_pending(1, "jobid", "two_speakers.eaf", FIXTURE, raw)

    assert pending.registry_profile_id == "single-speaker"
    assert pending.mapping.reference == ["ref@A", "ref@B"]
    assert pending.mapping.phrase == ["tx@A", "tx@B"]
