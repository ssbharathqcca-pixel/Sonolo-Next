"""
Content service for loading and materializing Sonolo learning packs.
Pack discovery is manifest-driven: content/manifest.json at the repo
root declares every scenario and vocabulary edition (SN-027), so new
packs ship without loader code changes. Vocabulary materialization
stays capped by `content_vocabulary_pack_limit` (default 500); seeds
tagged with the learner's preferred language are ordered ahead of the
pool so French users materialize French cards inside the same cap
instead of losing them to other-language cards.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import dialect_name
from app.models.curriculum import ReadingExercise, Unit, WritingExercise
from app.models.scenario import Scenario
from app.models.user import User
from app.models.vocabulary import VocabularyCard

logger = logging.getLogger(__name__)

#: Stable namespace so repeated seeds and materializations reuse IDs.
CONTENT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://sonolo.app/content")

LEVEL_DIFFICULTY: dict[str, int] = {
    "seed": 1,
    "sprout": 2,
    "branch": 3,
    "bloom": 4,
    "canopy": 5,
    "summit": 5,
}

#: Repository root: backend/app/services/content_service.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]

#: Single source of truth for pack discovery (SN-027): every scenario and
#: vocabulary edition is declared in the manifest with language, tier, and
#: UI metadata instead of hardcoded loader tuples.
MANIFEST_PATH = REPO_ROOT / "content" / "manifest.json"


def _vocabulary_pack_limit() -> int:
    """Cap on cards materialized per user from the vocabulary packs.

    Settings-driven via the optional `content_vocabulary_pack_limit`
    field, defaulting to 1000 for the combined pack catalog.
    """
    return get_settings().content_vocabulary_pack_limit

def content_scenario_id(content_id: str) -> UUID:
    """Deterministic scenario PK for a content-pack slug."""
    return uuid.uuid5(CONTENT_NAMESPACE, f"scenario:{content_id}")


def content_vocabulary_card_id(user_id: UUID, content_id: str) -> UUID:
    """Deterministic per-user card PK for a content-pack vocab id."""
    return uuid.uuid5(CONTENT_NAMESPACE, f"vocab:{user_id}:{content_id}")


def _primary_language_tag(code: str) -> str:
    """Base subtag of a BCP-47-style code so "fr-CA" matches the "fr" pack."""
    return code.strip().lower().split("-", 1)[0]


@dataclass(frozen=True)
class ManifestPack:
    """One pack entry from content/manifest.json."""

    id: str
    type: str  # "scenarios" | "vocabulary"
    language: str
    path: str  # Repo-root relative, e.g. content/scenarios/canadian-life-v1.json.


def load_manifest() -> dict[str, Any]:
    """Read and return the parsed content manifest (SN-030)."""
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        manifest: dict[str, Any] = json.load(handle)
    return manifest


def load_manifest_packs() -> list[ManifestPack]:
    """Read the content manifest and validate its pack entries.

    Pack ids must be unique across the manifest; loaders filter the
    returned entries by `type` and resolve `path` against the repo root.
    """
    manifest = load_manifest()
    packs: list[ManifestPack] = []
    seen_ids: set[str] = set()
    for entry in manifest.get("packs", []):
        pack_id = str(entry["id"])
        if pack_id in seen_ids:
            raise ValueError(f"Duplicate pack id in content manifest: {pack_id!r}")
        seen_ids.add(pack_id)
        packs.append(
            ManifestPack(
                id=pack_id,
                type=str(entry["type"]),
                language=str(entry["language"]),
                path=str(entry["path"]),
            )
        )
    return packs


def _resolve_pack_path(manifest_path: str) -> Path:
    """Resolve a manifest pack path relative to the repository root."""
    path = Path(manifest_path)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


@dataclass(frozen=True)
class ScenarioSeed:
    """One flattened scenario row from the SN-008 pack."""

    id: UUID
    title: str
    description: str
    category: str
    mode: str
    level: str
    difficulty: int
    target_language: str
    system_prompt: str
    opening_line: str
    expected_turns: int
    success_criteria: dict[str, Any]
    vocabulary_targets: list[str]
    grammar_targets: list[str]
    cultural_notes: str
    is_premium: bool
    #: Manifest pack the scenario belongs to (SN-035); drives the Learn
    #: tab's per-pack filtering and counts.
    pack_id: str
    #: Authoring slug (content id). Deterministic PK is ``id``.
    content_id: str
    unit_id: str | None = None
    sonolo_level: int | None = None
    is_published: bool = True


@dataclass(frozen=True)
class VocabularySeed:
    """One word definition from the SN-009 pack with FSRS priors."""

    content_id: str
    word: str
    translations: dict[str, str]
    difficulty: float  # FSRS 1-10
    stability: float  # FSRS days
    language: str
    unit_id: str | None = None
    sonolo_level: int | None = None
    is_published: bool = True
    example_sentences: tuple[str, ...] = ()
    phonetic: str = ""


@dataclass(frozen=True)
class MicroLessonSection:
    """One headed paragraph inside a Culture Corner micro-lesson (SN-047)."""

    heading: str
    text: str


@dataclass(frozen=True)
class MicroLessonSeed:
    """One Culture Corner micro-lesson (SN-047), new content format.

    Unlike scenarios, micro-lessons are read-only reference content:
    they are served straight from the manifest packs and never seeded
    into the database, so the Learn rail renders them without touching
    Scenario rows.
    """

    id: str
    title: str
    hook: str
    read_minutes: int
    sections: list[MicroLessonSection]
    takeaway: str
    try_it: str
    #: Manifest pack the lesson belongs to, plus the pack's UI metadata
    #: so the mobile rail can theme and icon the cards.
    pack_id: str
    theme_color: str
    icon: str
    #: Pack-level language ("en" | "fr") from the manifest (SN-049);
    #: the Culture Corner rail filters on it via the language query.
    language: str


@dataclass(frozen=True)
class PronunciationDrill:
    """One Pronunciation Lab drill (SN-049), new content format.

    Drills are read-only content served straight from the manifest
    packs; they never enter scenario seeding. The first three drills
    are free, the rest premium (gated like scenarios via is_locked).
    """

    id: str
    title: str
    focus: str
    target_sentence: str
    target_words: list[str]
    ipa_hint: str
    tip: str
    level: str
    is_premium: bool
    #: Manifest pack the drill belongs to, plus UI metadata.
    pack_id: str
    theme_color: str
    icon: str
    unit_id: str | None = None
    sonolo_level: int | None = None
    is_published: bool = True


def load_scenario_seeds() -> list[ScenarioSeed]:
    """Read and validate every scenario pack listed in the manifest."""
    # Manifest order governs pack precedence (English v1+v2, workplace,
    # healthcare, then Quebec): 65 total scenarios today; new packs
    # append without code changes. Each seed carries its manifest pack
    # id so seeded rows map back to their pack (SN-035).
    packs = [
        pack for pack in load_manifest_packs() if pack.type == "scenarios"
    ]
    seeds: list[ScenarioSeed] = []
    seen_ids: set[str] = set()
    for pack in packs:
        with _resolve_pack_path(pack.path).open(encoding="utf-8") as handle:
            raw: list[dict[str, Any]] = json.load(handle)
        for entry in raw:
            content_id = str(entry["id"])
            if content_id in seen_ids:
                raise ValueError(
                    f"Duplicate scenario id across packs: {content_id!r}"
                )
            seen_ids.add(content_id)
            level = str(entry["level"])
            seeds.append(
                ScenarioSeed(
                    id=content_scenario_id(content_id),
                    title=str(entry["title"]),
                    description=str(entry["description"]),
                    category=str(entry["category"]),
                    mode=str(entry["mode"]),
                    level=level,
                    difficulty=LEVEL_DIFFICULTY.get(level, 3),
                    target_language=str(entry["target_language"]),
                    system_prompt=str(entry["system_prompt"]),
                    opening_line=str(entry["opening_line"]),
                    expected_turns=int(entry["expected_turns"]),
                    success_criteria={"items": list(entry["success_criteria"])},
                    vocabulary_targets=list(entry["vocabulary_targets"]),
                    grammar_targets=list(entry["grammar_targets"]),
                    cultural_notes=str(entry["cultural_notes"]),
                    is_premium=bool(entry["is_premium"]),
                    pack_id=pack.id,
                    content_id=content_id,
                    unit_id=(
                        str(entry["unit_id"]) if entry.get("unit_id") else None
                    ),
                    sonolo_level=(
                        int(entry["sonolo_level"])
                        if entry.get("sonolo_level") is not None
                        else None
                    ),
                    is_published=bool(entry.get("is_published", True)),
                )
            )
    return seeds


def get_scenario_seed_by_id(scenario_id: UUID) -> ScenarioSeed | None:
    """Reverse-map a deterministic scenario PK to its authored seed."""
    for seed in load_scenario_seeds():
        if seed.id == scenario_id:
            return seed
    return None


def get_scenario_seed_by_content_id(content_id: str) -> ScenarioSeed | None:
    for seed in load_scenario_seeds():
        if seed.content_id == content_id:
            return seed
    return None


def get_pronunciation_drill(drill_id: str) -> PronunciationDrill | None:
    for drill in load_pronunciation_drills():
        if drill.id == drill_id:
            return drill
    return None


def gym_pronunciation_drills() -> list[PronunciationDrill]:
    """Pronunciation Lab rail: unit-bound curriculum drills stay off the gym."""
    return [drill for drill in load_pronunciation_drills() if not drill.unit_id]


def load_vocabulary_seeds() -> list[VocabularySeed]:
    """Read and validate every vocabulary pack listed in the manifest."""
    # Pack-level language comes from the manifest and orders seeds at
    # materialization time so a learner's preferred language fills the
    # pack limit before other languages.
    editions = [
        (_resolve_pack_path(pack.path), pack.language)
        for pack in load_manifest_packs()
        if pack.type == "vocabulary"
    ]
    seeds: list[VocabularySeed] = []
    seen_ids: set[str] = set()
    for path, pack_language in editions:
        with path.open(encoding="utf-8") as handle:
            raw: list[dict[str, Any]] = json.load(handle)
        language = str(pack_language or "en")
        for entry in raw:
            content_id = str(entry["id"])
            if content_id in seen_ids:
                raise ValueError(
                    f"Duplicate vocabulary id across packs: {content_id!r}"
                )
            seen_ids.add(content_id)
            params = entry["fsrs_params"]
            seeds.append(
                VocabularySeed(
                    content_id=content_id,
                    word=str(entry["word"]),
                    translations={
                        code: str(translation)
                        for code, translation in entry["translations"].items()
                    },
                    # Pack difficulty is 0-1; FSRS difficulty runs 1-10.
                    difficulty=round(1.0 + 9.0 * float(params["difficulty"]), 4),
                    stability=float(params["stability"]),
                    language=language,
                    unit_id=(
                        str(entry["unit_id"]) if entry.get("unit_id") else None
                    ),
                    sonolo_level=(
                        int(entry["sonolo_level"])
                        if entry.get("sonolo_level") is not None
                        else None
                    ),
                    is_published=bool(entry.get("is_published", True)),
                    example_sentences=tuple(
                        str(sentence)
                        for sentence in (entry.get("example_sentences") or [])
                    ),
                    phonetic=str(entry.get("phonetic") or ""),
                )
            )
    return seeds


def get_vocabulary_seed(content_id: str) -> VocabularySeed | None:
    for seed in load_vocabulary_seeds():
        if seed.content_id == content_id:
            return seed
    return None


def load_grammar_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pack in load_manifest_packs():
        if pack.type != "grammar":
            continue
        with _resolve_pack_path(pack.path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        items = raw if isinstance(raw, list) else [raw]
        for entry in items:
            content_id = str(entry["id"])
            if content_id in seen:
                raise ValueError(f"Duplicate grammar id: {content_id!r}")
            seen.add(content_id)
            documents.append(entry)
    return documents


def get_grammar_document(content_id: str) -> dict[str, Any] | None:
    for document in load_grammar_documents():
        if str(document["id"]) == content_id:
            return document
    return None


def get_grammar_document_for_unit(unit_code: str) -> dict[str, Any] | None:
    unit = get_unit_document(unit_code)
    spotlight_id = None if unit is None else unit.get("grammar_spotlight_id")
    if spotlight_id:
        document = get_grammar_document(str(spotlight_id))
        if document is not None:
            return document
    for document in load_grammar_documents():
        if str(document.get("unit_id")) == unit_code:
            return document
    return None


def load_microlesson_packs() -> list[ManifestPack]:
    """Every manifest pack of type "microlessons" (SN-047).

    The new content format is filtered by manifest type at the loader
    boundary, so it can never leak into scenario seeding or the
    scenarios/packs endpoints, which read `type == "scenarios"`.
    """
    return [
        pack for pack in load_manifest_packs() if pack.type == "microlessons"
    ]


def load_microlesson_seeds() -> list[MicroLessonSeed]:
    """Read every Culture Corner micro-lesson from the manifest (SN-047).

    Lessons are pure content-pack reads (no DB rows, no per-user state);
    the manifest supplies the pack id plus theme_color and icon so the
    mobile rail can render themed cards. Unknown fields are rejected by
    schema check; duplicate lesson ids across packs raise.
    """
    manifest = load_manifest()
    meta = {
        str(entry["id"]): entry
        for entry in manifest.get("packs", [])
        if entry.get("type") == "microlessons"
    }
    seeds: list[MicroLessonSeed] = []
    seen_ids: set[str] = set()
    for pack in load_microlesson_packs():
        entry = meta[pack.id]
        path = _resolve_pack_path(pack.path)
        with path.open(encoding="utf-8") as handle:
            raw: list[dict[str, Any]] = json.load(handle)
        for lesson in raw:
            lesson_id = str(lesson["id"])
            if lesson_id in seen_ids:
                raise ValueError(
                    f"Duplicate microlesson id across packs: {lesson_id!r}"
                )
            seen_ids.add(lesson_id)
            sections = [
                MicroLessonSection(
                    heading=str(section["heading"]),
                    text=str(section["text"]),
                )
                for section in lesson["sections"]
            ]
            seeds.append(
                MicroLessonSeed(
                    id=lesson_id,
                    title=str(lesson["title"]),
                    hook=str(lesson["hook"]),
                    read_minutes=int(lesson["read_minutes"]),
                    sections=sections,
                    takeaway=str(lesson["takeaway"]),
                    try_it=str(lesson["try_it"]),
                    pack_id=pack.id,
                    theme_color=str(entry.get("theme_color", "")),
                    icon=str(entry.get("icon", "")),
                    language=pack.language,
                )
            )
    return seeds


def load_pronunciation_packs() -> list[ManifestPack]:
    """Every manifest pack of type "pronunciation" (SN-049)."""
    return [
        pack for pack in load_manifest_packs() if pack.type == "pronunciation"
    ]


def load_pronunciation_drills() -> list[PronunciationDrill]:
    """Read every Pronunciation Lab drill from the manifest (SN-049).

    Like micro-lessons, drills are read-only content-pack reads (no DB
    rows); the manifest supplies the pack id plus theme_color and icon.
    Duplicate drill ids across packs raise.
    """
    manifest = load_manifest()
    meta = {
        str(entry["id"]): entry
        for entry in manifest.get("packs", [])
        if entry.get("type") == "pronunciation"
    }
    drills: list[PronunciationDrill] = []
    seen_ids: set[str] = set()
    for pack in load_pronunciation_packs():
        entry = meta[pack.id]
        path = _resolve_pack_path(pack.path)
        with path.open(encoding="utf-8") as handle:
            raw: list[dict[str, Any]] = json.load(handle)
        for drill in raw:
            drill_id = str(drill["id"])
            if drill_id in seen_ids:
                raise ValueError(
                    f"Duplicate pronunciation drill id across packs: {drill_id!r}"
                )
            seen_ids.add(drill_id)
            drills.append(
                PronunciationDrill(
                    id=drill_id,
                    title=str(drill["title"]),
                    focus=str(drill["focus"]),
                    target_sentence=str(drill["target_sentence"]),
                    target_words=[
                        str(word) for word in drill["target_words"]
                    ],
                    ipa_hint=str(drill["ipa_hint"]),
                    tip=str(drill["tip"]),
                    level=str(drill["level"]),
                    is_premium=bool(drill["is_premium"]),
                    pack_id=pack.id,
                    theme_color=str(entry.get("theme_color", "")),
                    icon=str(entry.get("icon", "")),
                    unit_id=(
                        str(drill["unit_id"]) if drill.get("unit_id") else None
                    ),
                    sonolo_level=(
                        int(drill["sonolo_level"])
                        if drill.get("sonolo_level") is not None
                        else None
                    ),
                    is_published=bool(drill.get("is_published", True)),
                )
            )
    return drills



@dataclass(frozen=True)
class ListeningTurn:
    """One spoken turn inside a Listening Gym dialogue (SN-050)."""

    role: str
    text: str
    pause_after_ms: int


@dataclass(frozen=True)
class ListeningQuestion:
    """One comprehension question with its correct answer (SN-050)."""

    prompt: str
    choices: list[str]
    correct_index: int
    explanation: str
    type: str = "multiple_choice"
    correct_order: list[int] | None = None


@dataclass(frozen=True)
class DictationSegment:
    """C5 dictation line bound to a turn (Part VIII §8.4)."""

    turn_index: int
    text: str
    key_words: list[str]


@dataclass(frozen=True)
class ListeningDialogue:
    """One Listening Gym dialogue (SN-050), new content format.

    Like micro-lessons and pronunciation drills, listening dialogues are
    read-only content-pack reads (no DB rows); the manifest supplies the
    pack id plus theme_color and icon. Duplicate ids across packs raise.
    """

    id: str
    title: str
    context: str
    level: str
    difficulty: float
    listening_focus: str
    is_premium: bool
    turns: list[ListeningTurn]
    questions: list[ListeningQuestion]
    vocab_targets: list[str]
    pack_id: str
    theme_color: str
    icon: str
    unit_id: str | None = None
    sonolo_level: int | None = None
    dictation_segments: list[DictationSegment] | None = None


def load_listening_packs() -> list[ManifestPack]:
    """Every manifest pack of type "listening" (SN-050)."""
    return [
        pack for pack in load_manifest_packs() if pack.type == "listening"
    ]


def load_listening_dialogues() -> list[ListeningDialogue]:
    """Read every Listening Gym dialogue from the manifest (SN-050)."""
    manifest = load_manifest()
    meta = {
        str(entry["id"]): entry
        for entry in manifest.get("packs", [])
        if entry.get("type") == "listening"
    }
    dialogues: list[ListeningDialogue] = []
    seen_ids: set[str] = set()
    for pack in load_listening_packs():
        entry = meta[pack.id]
        path = _resolve_pack_path(pack.path)
        with path.open(encoding="utf-8") as handle:
            raw: list[dict[str, Any]] = json.load(handle)
        for dialogue in raw:
            dialogue_id = str(dialogue["id"])
            if dialogue_id in seen_ids:
                raise ValueError(
                    f"Duplicate listening dialogue id across packs: {dialogue_id!r}"
                )
            seen_ids.add(dialogue_id)
            dialogues.append(
                ListeningDialogue(
                    id=dialogue_id,
                    title=str(dialogue["title"]),
                    context=str(dialogue["context"]),
                    level=str(dialogue["level"]),
                    difficulty=float(dialogue["difficulty"]),
                    listening_focus=str(dialogue["listening_focus"]),
                    is_premium=bool(dialogue["is_premium"]),
                    turns=[
                        ListeningTurn(
                            role=str(turn["role"]),
                            text=str(turn["text"]),
                            pause_after_ms=int(turn["pause_after_ms"]),
                        )
                        for turn in dialogue["turns"]
                    ],
                    questions=[
                        ListeningQuestion(
                            prompt=str(question["prompt"]),
                            choices=[
                                str(choice) for choice in question["choices"]
                            ],
                            correct_index=int(question.get("correct_index") or 0),
                            explanation=str(question["explanation"]),
                            type=str(question.get("type") or "multiple_choice"),
                            correct_order=(
                                [int(item) for item in question["correct_order"]]
                                if question.get("correct_order") is not None
                                else None
                            ),
                        )
                        for question in dialogue["questions"]
                    ],
                    vocab_targets=[
                        str(word) for word in dialogue["vocab_targets"]
                    ],
                    pack_id=pack.id,
                    theme_color=str(entry.get("theme_color", "")),
                    icon=str(entry.get("icon", "")),
                    unit_id=(
                        str(dialogue["unit_id"])
                        if dialogue.get("unit_id")
                        else None
                    ),
                    sonolo_level=(
                        int(dialogue["sonolo_level"])
                        if dialogue.get("sonolo_level") is not None
                        else None
                    ),
                    dictation_segments=(
                        [
                            DictationSegment(
                                turn_index=int(segment["turn_index"]),
                                text=str(segment["text"]),
                                key_words=[
                                    str(word)
                                    for word in (segment.get("key_words") or [])
                                ],
                            )
                            for segment in dialogue["dictation_segments"]
                        ]
                        if dialogue.get("dictation_segments")
                        else None
                    ),
                )
            )
    return dialogues
def _scenario_row_values(seed: ScenarioSeed) -> dict[str, Any]:
    """Shared Scenario upsert payload (gym + unit-bound F3)."""
    unit_pk = (
        content_unit_id(seed.unit_id, seed.target_language)
        if seed.unit_id
        else None
    )
    return {
        "id": seed.id,
        "title": seed.title,
        "description": seed.description,
        "category": seed.category,
        "mode": seed.mode,
        "level": seed.level,
        "difficulty": seed.difficulty,
        "target_language": seed.target_language,
        "pack_id": seed.pack_id,
        "unit_id": unit_pk,
        "sonolo_level": seed.sonolo_level,
        "system_prompt": seed.system_prompt,
        "opening_line": seed.opening_line,
        "expected_turns": seed.expected_turns,
        "success_criteria": seed.success_criteria,
        "vocabulary_targets": seed.vocabulary_targets,
        "grammar_targets": seed.grammar_targets,
        "cultural_notes": seed.cultural_notes,
        "is_premium": seed.is_premium,
        "is_published": seed.is_published,
    }


async def _upsert_scenario(db: AsyncSession, seed: ScenarioSeed) -> None:
    values = _scenario_row_values(seed)
    if dialect_name(db) == "postgresql":
        statement = (
            pg_insert(Scenario)
            .values(**values)
            .on_conflict_do_update(index_elements=["id"], set_=values)
        )
    else:
        statement = (
            sqlite_insert(Scenario)
            .values(**values)
            .on_conflict_do_update(index_elements=["id"], set_=values)
        )
    await db.execute(statement)


async def seed_scenarios(db: AsyncSession) -> int:
    """Idempotently upsert all scenarios from every pack edition."""
    # Units must exist before F3 scenarios write unit_id FKs.
    await persist_curriculum(db)
    seeds = load_scenario_seeds()
    for seed in seeds:
        await _upsert_scenario(db, seed)
    await db.commit()
    logger.info("Seeded %d scenarios from the content pack.", len(seeds))
    return len(seeds)


async def ensure_user_vocabulary(
    db: AsyncSession,
    user_id: UUID,
    preferred_language: str | None = None,
) -> int:
    """Lazily materialize the vocabulary packs for a user with no cards.

    Loads every edition up to the settings-driven pack limit after
    ordering seeds whose language matches the learner's preference
    ahead of the pool, so preferred-language cards always fit inside
    the cap. The preference is read from the users table when the
    caller omits it, falling back to "en" when unset; regional tags
    such as "fr-CA" match their base pack ("fr"). Existing cards are
    never touched; re-runs are no-ops thanks to deterministic
    per-user card ids.
    """
    count = (
        await db.execute(
            select(func.count())
            .select_from(VocabularyCard)
            .where(VocabularyCard.user_id == user_id)
        )
    ).scalar_one()
    if int(count) > 0:
        return 0

    preference = preferred_language
    if not preference:
        preference = (
            await db.execute(
                select(User.preferred_language).where(User.id == user_id)
            )
        ).scalar_one_or_none()
    preferred_tag = _primary_language_tag(preference or "en")

    seeds = load_vocabulary_seeds()
    matching = [
        seed
        for seed in seeds
        if _primary_language_tag(seed.language) == preferred_tag
    ]
    seeds = matching + [
        seed
        for seed in seeds
        if _primary_language_tag(seed.language) != preferred_tag
    ]
    # The cap applies after sorting so preferred-language cards are
    # never crowded out of materialization by other languages.
    seeds = seeds[:_vocabulary_pack_limit()]
    for seed in seeds:
        card = VocabularyCard(
            id=content_vocabulary_card_id(user_id, seed.content_id),
            user_id=user_id,
            word=seed.word,
            translations=seed.translations,
            stability=seed.stability,
            difficulty=seed.difficulty,
            state=0,
        )
        db.add(card)
    await db.flush()
    logger.info(
        "Materialized %d vocabulary cards for user %s.", len(seeds), user_id
    )
    return len(seeds)


def content_unit_id(unit_code: str, language: str) -> UUID:
    """Deterministic unit PK."""
    return uuid.uuid5(CONTENT_NAMESPACE, f"unit:{unit_code}:{language}")


def content_reading_id(content_id: str) -> UUID:
    return uuid.uuid5(CONTENT_NAMESPACE, f"reading:{content_id}")


def content_writing_id(content_id: str) -> UUID:
    return uuid.uuid5(CONTENT_NAMESPACE, f"writing:{content_id}")


def load_unit_documents() -> list[dict[str, Any]]:
    """Unit JSON documents declared in the manifest (type=units)."""
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pack in load_manifest_packs():
        if pack.type != "units":
            continue
        with _resolve_pack_path(pack.path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError(f"Unit pack {pack.id!r} must be a JSON object")
        unit_id = str(raw["id"])
        key = f"{unit_id}:{raw['language']}"
        if key in seen:
            raise ValueError(f"Duplicate unit id in manifest: {key!r}")
        seen.add(key)
        documents.append(raw)
    return documents


def load_reading_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pack in load_manifest_packs():
        if pack.type != "reading":
            continue
        with _resolve_pack_path(pack.path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        items = raw if isinstance(raw, list) else [raw]
        for entry in items:
            content_id = str(entry["id"])
            if content_id in seen:
                raise ValueError(f"Duplicate reading id: {content_id!r}")
            seen.add(content_id)
            documents.append(entry)
    return documents


def load_writing_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pack in load_manifest_packs():
        if pack.type != "writing":
            continue
        with _resolve_pack_path(pack.path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        items = raw if isinstance(raw, list) else [raw]
        for entry in items:
            content_id = str(entry["id"])
            if content_id in seen:
                raise ValueError(f"Duplicate writing id: {content_id!r}")
            seen.add(content_id)
            documents.append(entry)
    return documents


def load_vocabulary_hunt_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pack in load_manifest_packs():
        if pack.type != "vocabulary_hunts":
            continue
        with _resolve_pack_path(pack.path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        items = raw if isinstance(raw, list) else [raw]
        for entry in items:
            content_id = str(entry["id"])
            if content_id in seen:
                raise ValueError(f"Duplicate hunt id: {content_id!r}")
            seen.add(content_id)
            documents.append(entry)
    return documents


def get_unit_document(unit_code: str) -> dict[str, Any] | None:
    for document in load_unit_documents():
        if str(document["id"]) == unit_code:
            return document
    return None


def get_reading_document(content_id: str) -> dict[str, Any] | None:
    for document in load_reading_documents():
        if str(document["id"]) == content_id:
            return document
    return None


def get_hunt_document(content_id: str) -> dict[str, Any] | None:
    for document in load_vocabulary_hunt_documents():
        if str(document["id"]) == content_id:
            return document
    return None


def load_unit_test_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pack in load_manifest_packs():
        if pack.type != "unit_tests":
            continue
        with _resolve_pack_path(pack.path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        items = raw if isinstance(raw, list) else [raw]
        for entry in items:
            content_id = str(entry["id"])
            if content_id in seen:
                raise ValueError(f"Duplicate unit test id: {content_id!r}")
            seen.add(content_id)
            documents.append(entry)
    return documents


def get_unit_test_document(unit_code: str) -> dict[str, Any] | None:
    unit = get_unit_document(unit_code)
    test_id = None if unit is None else unit.get("unit_test_id")
    for document in load_unit_test_documents():
        if test_id and str(document.get("id")) == str(test_id):
            return document
        if str(document.get("unit_id")) == unit_code:
            return document
    return None


def get_writing_document(content_id: str) -> dict[str, Any] | None:
    for document in load_writing_documents():
        if str(document["id"]) == content_id:
            return document
    return None


def required_reading_activity_ids(unit: dict[str, Any]) -> list[str]:
    activities = unit.get("reading_required_activities") or []
    return [str(item["id"]) for item in activities]


def validate_curriculum_content() -> list[str]:
    """Return human-readable errors; empty means F3/content contract passes."""
    errors: list[str] = []
    units = load_unit_documents()
    readings = {str(item["id"]): item for item in load_reading_documents()}
    writings = {str(item["id"]): item for item in load_writing_documents()}
    hunts = {str(item["id"]): item for item in load_vocabulary_hunt_documents()}

    if not units:
        errors.append("No unit documents loaded")
    for unit in units:
        vocab = unit.get("vocabulary_targets") or []
        grammar = unit.get("grammar_targets") or []
        if not 15 <= len(vocab) <= 25:
            errors.append(
                f"Unit {unit.get('id')} must define 15–25 vocabulary targets"
            )
        if not 1 <= len(grammar) <= 3:
            errors.append(
                f"Unit {unit.get('id')} must define 1–3 grammar targets"
            )
        if not (unit.get("reading_ids") or unit.get("reading_required_activities")):
            errors.append(f"Unit {unit.get('id')} must have at least 1 reading exercise")
        if not unit.get("writing_ids"):
            errors.append(f"Unit {unit.get('id')} must have at least 1 writing exercise")
        errors.extend(_validate_unit_speaking(unit))
        errors.extend(_validate_unit_primer_and_grammar(unit))
        band = unit.get("band")
        level = int(unit.get("level_target") or 0)
        if band == "foundation" and not 1 <= level <= 3:
            errors.append(f"Unit {unit.get('id')} level_target does not match band")
        for activity in unit.get("reading_required_activities") or []:
            aid = str(activity.get("id"))
            atype = str(activity.get("type"))
            if atype == "reading_exercise" and aid not in readings:
                errors.append(f"Required reading {aid} missing")
            if atype == "vocabulary_hunt" and aid not in hunts:
                errors.append(f"Required hunt {aid} missing")
        for writing_id in unit.get("writing_ids") or []:
            if writing_id not in writings:
                errors.append(f"Writing {writing_id} missing for unit {unit.get('id')}")
        listening_docs = {item.id: item for item in load_listening_dialogues()}
        if not unit.get("listening_ids"):
            errors.append(f"Unit {unit.get('id')} must have at least 1 listening exercise")
        for listening_id in unit.get("listening_ids") or []:
            if listening_id not in listening_docs:
                errors.append(f"Listening {listening_id} missing for unit {unit.get('id')}")
            else:
                item = listening_docs[listening_id]
                if item.unit_id != str(unit.get("id")):
                    errors.append(f"Listening {listening_id} unit_id must be {unit.get('id')}")
                segs = item.dictation_segments or []
                if str(unit.get("id")) == "F3" and len(segs) != 2:
                    errors.append("F3 listening must include two dictation sentences")
                if str(unit.get("id")) == "F3" and len(item.turns) != 7:
                    errors.append("F3 listening audio story must have 7 turns")

    for reading in readings.values():
        errors.extend(_validate_reading_document(reading))
    for hunt in hunts.values():
        errors.extend(_validate_hunt_document(hunt, readings))
    tests = {str(item["id"]): item for item in load_unit_test_documents()}
    for unit in units:
        test_id = unit.get("unit_test_id")
        if not test_id:
            continue
        document = tests.get(str(test_id))
        if document is None:
            errors.append(f"Unit {unit.get('id')} unit_test_id {test_id} missing")
            continue
        errors.extend(_validate_unit_test_document(document, str(unit.get("id"))))
    return errors


def _validate_unit_primer_and_grammar(unit: dict[str, Any]) -> list[str]:
    """F3 Vocab Primer + Grammar Spotlight are catalog content, not C0 flags."""
    errors: list[str] = []
    unit_code = str(unit.get("id"))
    targets = [str(word) for word in (unit.get("vocabulary_targets") or [])]
    primer_ids = [str(item) for item in (unit.get("vocab_primer_ids") or [])]
    seeds = {seed.content_id: seed for seed in load_vocabulary_seeds()}
    if unit_code == "F3" and len(primer_ids) != 20:
        errors.append("F3 vocab_primer_ids must list exactly 20 items")
    if primer_ids:
        if not 15 <= len(primer_ids) <= 25:
            errors.append(
                f"Unit {unit_code} vocab_primer_ids must list 15–25 items"
            )
        primer_words = []
        for content_id in primer_ids:
            seed = seeds.get(content_id)
            if seed is None:
                errors.append(f"Vocab primer {content_id} missing")
                continue
            if seed.unit_id != unit_code:
                errors.append(f"Vocab primer {content_id} unit_id must be {unit_code}")
            if not seed.is_published:
                errors.append(f"Vocab primer {content_id} must be published")
            primer_words.append(seed.word)
        if primer_words and {word.casefold() for word in primer_words} != {
            word.casefold() for word in targets
        }:
            errors.append(
                f"Unit {unit_code} vocab primer words must match vocabulary_targets"
            )
    spotlight_id = str(unit.get("grammar_spotlight_id") or "")
    if unit_code == "F3" and not spotlight_id:
        errors.append("F3 grammar_spotlight_id required")
    if spotlight_id:
        document = get_grammar_document(spotlight_id)
        if document is None:
            errors.append(f"Grammar spotlight {spotlight_id} missing")
        else:
            errors.extend(_validate_grammar_document(document, unit_code, unit))
    return errors


def _validate_grammar_document(
    document: dict[str, Any], unit_code: str, unit: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    gid = document.get("id")
    if str(document.get("unit_id")) != unit_code:
        errors.append(f"{gid}: unit_id must be {unit_code}")
    if document.get("language") not in {"en-CA", "fr-CA"}:
        errors.append(f"{gid}: language must be en-CA or fr-CA")
    if not document.get("is_published", False):
        errors.append(f"{gid}: grammar spotlight must be published")
    if int(document.get("level") or 0) != int(unit.get("level_target") or 0):
        errors.append(f"{gid}: level must match unit level_target")
    authored = [str(item) for item in (document.get("grammar_targets") or [])]
    expected = [str(item) for item in (unit.get("grammar_targets") or [])]
    if {item.casefold() for item in authored} != {item.casefold() for item in expected}:
        errors.append(f"{gid}: grammar_targets must match the unit grammar scope")
    if unit_code != "F3":
        return errors
    blob = " ".join(
        [
            str(document.get("explanation") or ""),
            " ".join(
                str(section.get("text") or "")
                for section in (document.get("sections") or [])
            ),
        ]
    ).casefold()
    if not all(token in blob for token in (" a ", " an ", " the ")):
        errors.append(f"{gid}: explanation must cover articles a/an/the")
    if "can i have" not in blob:
        errors.append(f"{gid}: explanation must cover Can I have...?")
    if "where is" not in blob:
        errors.append(f"{gid}: explanation must cover Where is...?")
    if "uncount" not in blob:
        errors.append(f"{gid}: explanation must cover count vs uncount nouns")
    return errors


def _validate_unit_speaking(unit: dict[str, Any]) -> list[str]:
    """F3 SpeakUp: pronunciation + clerk + SpeakSprint, bound and published."""
    errors: list[str] = []
    unit_code = str(unit.get("id"))
    speaking_ids = [str(item) for item in (unit.get("speaking_ids") or [])]
    if unit_code == "F3" and not speaking_ids:
        errors.append("F3 must define speaking_ids (pronunciation, clerk, SpeakSprint)")
        return errors
    if not speaking_ids:
        return errors
    scenarios = {seed.content_id: seed for seed in load_scenario_seeds()}
    drills = {drill.id: drill for drill in load_pronunciation_drills()}
    if unit_code == "F3":
        required = {"speak-F3-clerk", "speak-F3-sprint", "pron-F3-grocery"}
        if set(speaking_ids) != required:
            errors.append(
                "F3 speaking_ids must be speak-F3-clerk, speak-F3-sprint, "
                "pron-F3-grocery"
            )
    for speaking_id in speaking_ids:
        if speaking_id in scenarios:
            seed = scenarios[speaking_id]
            if seed.unit_id != unit_code:
                errors.append(
                    f"Speaking {speaking_id} unit_id must be {unit_code}"
                )
            if not seed.is_published:
                errors.append(f"Speaking {speaking_id} must be published")
            if speaking_id == "speak-F3-clerk" and seed.expected_turns != 5:
                errors.append("F3 clerk conversation must have 5 expected turns")
            if speaking_id == "speak-F3-sprint":
                blob = f"{seed.description} {seed.opening_line}".casefold()
                if "60" not in blob:
                    errors.append("F3 SpeakSprint must be a 60-second prompt")
        elif speaking_id in drills:
            drill = drills[speaking_id]
            if drill.unit_id != unit_code:
                errors.append(
                    f"Pronunciation {speaking_id} unit_id must be {unit_code}"
                )
            if not drill.is_published:
                errors.append(f"Pronunciation {speaking_id} must be published")
            if unit_code == "F3":
                words = {word.casefold() for word in drill.target_words}
                for required_word in ("grocery", "receipt", "aisle"):
                    if required_word not in words:
                        errors.append(
                            f"F3 pronunciation must include {required_word!r}"
                        )
        else:
            errors.append(f"Speaking {speaking_id} missing for unit {unit_code}")
    return errors


def _validate_reading_document(reading: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    rid = reading.get("id")
    if reading.get("language") not in {"en-CA", "fr-CA"}:
        errors.append(f"{rid}: language must be en-CA or fr-CA")
    if reading.get("text_source") != "original":
        errors.append(f"{rid}: text_source must be original")
    level = int(reading.get("level") or 0)
    word_count = int(reading.get("word_count") or 0)
    if 1 <= level <= 3 and not 50 <= word_count <= 150:
        errors.append(f"{rid}: L1–L3 word_count must be 50–150")
    questions = reading.get("questions") or []
    if 1 <= level <= 3 and not 3 <= len(questions) <= 5:
        errors.append(f"{rid}: L1–L3 must have 3–5 questions")
    text = str(reading.get("text_content") or "").casefold()
    targets = [str(word) for word in (reading.get("vocabulary_targets") or [])]
    if targets:
        hits = sum(1 for word in targets if word.casefold() in text)
        if hits / len(targets) < 0.6:
            errors.append(f"{rid}: vocabulary_targets overlap with text must be ≥60%")
    for question in questions:
        qtype = question.get("type")
        options = question.get("options")
        if qtype == "vocabulary_hunt":
            errors.append(f"{rid}: vocabulary_hunt is not a ReadingQuestion.type")
        if qtype == "multiple_choice":
            if not isinstance(options, list) or len(options) != 4:
                errors.append(f"{question.get('id')}: MC must have exactly 4 options")
        elif qtype == "true_false":
            if not isinstance(options, list) or len(options) != 2:
                errors.append(f"{question.get('id')}: TF must have exactly 2 options")
        elif qtype in {"fill_blank", "short_answer"}:
            if options not in (None, []):
                errors.append(f"{question.get('id')}: {qtype} must not have options")
        else:
            errors.append(f"{question.get('id')}: unknown question type {qtype!r}")
    return errors


def _validate_hunt_document(
    hunt: dict[str, Any], readings: dict[str, dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    hid = hunt.get("id")
    if hunt.get("type") != "vocabulary_hunt":
        errors.append(f"{hid}: type must be vocabulary_hunt")
    reading_id = str(hunt.get("reading_exercise_id") or "")
    reading = readings.get(reading_id)
    if reading is None:
        errors.append(f"{hid}: bound reading {reading_id} missing")
        return errors
    text = str(reading.get("text_content") or "").casefold()
    targets = [str(word) for word in (hunt.get("target_words") or [])]
    if not targets:
        errors.append(f"{hid}: target_words required")
    for word in targets:
        if word.casefold() not in text:
            errors.append(f"{hid}: target {word!r} does not appear in bound text")
    return errors


def _validate_unit_test_document(document: dict[str, Any], unit_code: str) -> list[str]:
    errors: list[str] = []
    tid = document.get("id")
    if document.get("type") != "unit_test":
        errors.append(f"{tid}: type must be unit_test")
    if str(document.get("unit_id")) != unit_code:
        errors.append(f"{tid}: unit_id must be {unit_code}")
    if document.get("language") not in {"en-CA", "fr-CA"}:
        errors.append(f"{tid}: language must be en-CA or fr-CA")
    if not document.get("is_published", False):
        errors.append(f"{tid}: F3 unit test must be published")
    sections = document.get("sections") or {}
    listening = (sections.get("listening") or {}).get("questions") or []
    reading = (sections.get("reading") or {}).get("questions") or []
    writing = (sections.get("writing") or {}).get("tasks") or []
    speaking = (sections.get("speaking") or {}).get("task")
    if len(listening) != 5:
        errors.append(f"{tid}: listening must have 5 questions")
    listen_types = [item.get("type") for item in listening]
    if listen_types.count("multiple_choice") != 3 or listen_types.count("true_false") != 1 or listen_types.count("dictation") != 1:
        errors.append(f"{tid}: listening must be 3 MC + 1 TF + 1 dictation")
    if len(reading) != 5:
        errors.append(f"{tid}: reading must have 5 questions")
    read_types = [item.get("type") for item in reading]
    if read_types.count("multiple_choice") != 3 or read_types.count("fill_blank") != 1 or read_types.count("vocabulary_hunt") != 1:
        errors.append(f"{tid}: reading must be 3 MC + 1 fill-blank + 1 vocabulary hunt")
    hunt = next((item for item in reading if item.get("type") == "vocabulary_hunt"), None)
    text = str((sections.get("reading") or {}).get("text_content") or "").casefold()
    if hunt is not None:
        for word in hunt.get("target_words") or []:
            if str(word).casefold() not in text:
                errors.append(f"{tid}: hunt word {word!r} missing from reading text")
    if not speaking or not speaking.get("prompt"):
        errors.append(f"{tid}: speaking task required")
    if len(writing) != 2:
        errors.append(f"{tid}: writing must have 2 tasks")
    weights = {
        "listening": (sections.get("listening") or {}).get("weight"),
        "reading": (sections.get("reading") or {}).get("weight"),
        "speaking": (sections.get("speaking") or {}).get("weight"),
        "writing": (sections.get("writing") or {}).get("weight"),
    }
    if weights != {"listening": 0.25, "reading": 0.25, "speaking": 0.30, "writing": 0.20}:
        errors.append(f"{tid}: section weights must be 0.25/0.25/0.30/0.20")
    return errors


async def persist_curriculum(db: AsyncSession) -> dict[str, int]:
    """Upsert units, reading, and writing catalog rows from manifest JSON."""
    units = 0
    for document in load_unit_documents():
        unit_id = content_unit_id(str(document["id"]), str(document["language"]))
        existing = await db.get(Unit, unit_id)
        values = dict(
            unit_code=str(document["id"]),
            band=str(document["band"]),
            title=str(document["title"]),
            story_chapter=str(document.get("story_chapter") or ""),
            theme=str(document.get("theme") or ""),
            icon=str(document.get("icon") or ""),
            level_target=int(document["level_target"]),
            sort_order=int(document["sort_order"]),
            language=str(document["language"]),
            cultural_context=str(document.get("cultural_context") or ""),
            vocabulary_targets=list(document.get("vocabulary_targets") or []),
            grammar_targets=list(document.get("grammar_targets") or []),
            prerequisites=list(document.get("prerequisites") or []),
            is_published=bool(document.get("is_published", False)),
        )
        if existing is None:
            db.add(Unit(id=unit_id, **values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        units += 1

    readings = 0
    for document in load_reading_documents():
        unit_code = str(document.get("unit_id") or "")
        language = str(document["language"])
        unit_pk = content_unit_id(unit_code, language) if unit_code else None
        reading_pk = content_reading_id(str(document["id"]))
        existing = await db.get(ReadingExercise, reading_pk)
        values = dict(
            content_id=str(document["id"]),
            unit_id=unit_pk,
            title=str(document["title"]),
            language=language,
            text_content=str(document["text_content"]),
            text_source=str(document.get("text_source") or "original"),
            word_count=document.get("word_count"),
            sonolo_level=document.get("level"),
            text_type=str(document.get("text_type") or ""),
            questions=list(document.get("questions") or []),
            vocabulary_targets=list(document.get("vocabulary_targets") or []),
            grammar_targets=list(document.get("grammar_targets") or []),
            cultural_note=document.get("cultural_note"),
            reading_time_minutes=document.get("reading_time_minutes"),
            is_published=bool(document.get("is_published", False)),
        )
        if existing is None:
            db.add(ReadingExercise(id=reading_pk, **values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        readings += 1

    writings = 0
    for document in load_writing_documents():
        unit_code = str(document.get("unit_id") or "")
        language = str(document["language"])
        unit_pk = content_unit_id(unit_code, language) if unit_code else None
        writing_pk = content_writing_id(str(document["id"]))
        existing = await db.get(WritingExercise, writing_pk)
        values = dict(
            content_id=str(document["id"]),
            unit_id=unit_pk,
            title=str(document["title"]),
            language=language,
            exercise_type=str(document["exercise_type"]),
            sonolo_level=document.get("level"),
            prompt=str(document["prompt"]),
            scaffold=document.get("scaffold"),
            model_answer=document.get("model_answer"),
            word_count_target=document.get("word_count_target") or {},
            rubric=document.get("rubric") or {},
            vocabulary_targets=list(document.get("vocabulary_targets") or []),
            grammar_targets=list(document.get("grammar_targets") or []),
            word_bank=document.get("word_bank"),
            correct_sentence=document.get("correct_sentence"),
            error_text=document.get("error_text"),
            error_count=document.get("error_count"),
            corrected_text=document.get("corrected_text"),
            is_published=bool(document.get("is_published", False)),
        )
        if existing is None:
            db.add(WritingExercise(id=writing_pk, **values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        writings += 1

    await db.flush()
    scenarios = 0
    for seed in load_scenario_seeds():
        if not seed.unit_id:
            continue
        await _upsert_scenario(db, seed)
        scenarios += 1

    await db.flush()
    return {
        "units": units,
        "reading": readings,
        "writing": writings,
        "scenarios": scenarios,
    }