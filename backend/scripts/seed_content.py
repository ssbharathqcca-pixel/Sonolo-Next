"""Seed Sonolo content packs into the configured database.

Usage (from backend/, with DATABASE_URL pointing at the target DB):

    .venv/Scripts/python -m scripts.seed_content

Loads every pack declared in content/manifest.json (SN-027):

    scenarios  : canadian-life-v1 + v2, quebec-life-v1,
                 workplace-english-v1, healthcare-english-v1,
                 quebec-healthcare-v1, quebec-workplace-v1,
                 housing-english-v1, finance-english-v1,
                 quebec-housing-v1, quebec-finance-v1,
                 smalltalk-english-v1, job-interviews-english-v1,
                 hospitality-english-v1, speaking-F1/F2/F3  (161)
    vocabulary : core-v1 + v2, core-fr-v1, workplace + healthcare,
                 quebec-healthcare + quebec-workplace,
                 housing + finance, quebec-housing + quebec-finance,
                 smalltalk, job-interviews, hospitality,
                 listening, F1/F2/F3 primers                 (870)

Micro-lessons (culture-english-v1, culture-french-v1), pronunciation
drills (canadian-speech-english-v1), and listening dialogues
(listening-english-v1) are read-only content formats served straight
from the manifest — they never enter scenario seeding.

Scenarios are shared rows upserted idempotently under deterministic
uuid5 PKs derived in the SAME namespace as
app.services.content_service — the script delegates to the service
loader so both seeding paths stay byte-identical, including the
per-scenario pack_id mapping (SN-035). Vocabulary stays user-scoped
by design (D-008): this script validates and counts the combined
packs; cards materialize lazily per user via GET /api/review/due.
Re-running is a no-op: counts stay stable at 161 scenarios / 870
vocabulary pack items.
"""

import asyncio
import json
import sys
from typing import Any

from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.scenario import Scenario
from app.services.content_service import (
    _resolve_pack_path,
    load_manifest_packs,
    load_scenario_seeds,
    seed_scenarios as seed_scenarios_service,
)

REQUIRED_VOCAB_FIELDS = {"id", "word", "translations", "fsrs_params"}
REQUIRED_TRANSLATIONS = {"pa", "en", "hi", "zh", "es"}
# English-target packs carry the four L1 translations; French-target
# packs additionally carry "en" (SN-020/SN-036). Both sets are valid.
ALLOWED_TRANSLATION_SETS = {
    frozenset({"pa", "hi", "zh", "es"}),
    frozenset(REQUIRED_TRANSLATIONS),
}


def load_all_scenario_seeds():
    """Load every manifest scenario pack through the service loader."""
    return load_scenario_seeds()


def validate_vocabulary_packs() -> dict[str, int]:
    """Parse every manifest vocabulary pack and verify the shared schema."""
    counts: dict[str, int] = {}
    total = 0
    packs = [
        pack for pack in load_manifest_packs() if pack.type == "vocabulary"
    ]
    for pack in packs:
        path = _resolve_pack_path(pack.path)
        with path.open(encoding="utf-8") as handle:
            raw: list[dict[str, Any]] = json.load(handle)
        # Word overlaps ACROSS packs are by design (e.g. "interview");
        # only duplicates inside one pack indicate a curation mistake.
        seen_words: set[str] = set()
        for entry in raw:
            missing = REQUIRED_VOCAB_FIELDS - entry.keys()
            if missing:
                raise ValueError(
                    f"{pack.id}: item {entry.get('id')!r} missing {sorted(missing)}"
                )
            codes = frozenset(entry["translations"])
            if codes not in ALLOWED_TRANSLATION_SETS:
                raise ValueError(
                    f"{pack.id}: item {entry['id']!r} translations {sorted(codes)}"
                    f" not one of {[sorted(s) for s in ALLOWED_TRANSLATION_SETS]}"
                )
            params = entry["fsrs_params"]
            if not 0.0 <= float(params["difficulty"]) <= 1.0:
                raise ValueError(
                    f"{pack.id}: item {entry['id']!r} difficulty out of range"
                )
            word = str(entry["word"]).casefold()
            if word in seen_words:
                raise ValueError(f"{pack.id}: duplicate word {word!r}")
            seen_words.add(word)
        counts[pack.id] = len(raw)
        total += len(raw)
    counts["total"] = total
    return counts


async def seed_scenarios(db) -> int:
    """Idempotently upsert all scenarios from every loaded pack."""
    count = await seed_scenarios_service(db)
    print(f"scenario_upserts={count}")
    return count


async def main() -> int:
    vocab_counts = validate_vocabulary_packs()
    async with AsyncSessionLocal() as session:
        before = int(
            (
                await session.execute(
                    select(func.count()).select_from(Scenario)
                )
            ).scalar_one()
        )
        upserts = await seed_scenarios(session)
        after = int(
            (
                await session.execute(
                    select(func.count()).select_from(Scenario)
                )
            ).scalar_one()
        )
    print(f"scenarios_before={before}")
    print(f"scenarios_after={after}")
    print(
        "vocabulary_pack_items="
        f"{vocab_counts.pop('total')}"
        f" ({', '.join(f'{k}={v}' for k, v in vocab_counts.items())})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))