import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "content" / "manifest.json"

REQUIRED_FIELDS = (
    "id",
    "type",
    "language",
    "path",
    "title",
    "description",
    "tier",
    "is_published",
)


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_exists() -> None:
    assert MANIFEST_PATH.is_file()


def test_manifest_parses() -> None:
    payload = _load_manifest()
    assert payload["version"] == 1
    assert isinstance(payload["packs"], list)
    assert len(payload["packs"]) > 0


def test_manifest_pack_fields() -> None:
    payload = _load_manifest()
    for pack in payload["packs"]:
        for field in REQUIRED_FIELDS:
            assert field in pack, f"Missing manifest field: {field}"


def test_manifest_paths_exist() -> None:
    payload = _load_manifest()
    for pack in payload["packs"]:
        pack_path = REPO_ROOT / pack["path"]
        assert pack_path.is_file(), f"Missing pack file: {pack['path']}"


def test_manifest_types_and_languages() -> None:
    payload = _load_manifest()
    for pack in payload["packs"]:
        assert pack["type"] in {
            "scenarios",
            "vocabulary",
            "microlessons",
            "pronunciation",
            "listening",
            "units",
            "reading",
            "writing",
            "vocabulary_hunts",
            "diagnostic",
            "unit_tests",
            "grammar",
        }
        assert pack["language"] in {"en", "fr", "en-CA", "fr-CA"}
        assert isinstance(pack["is_published"], bool)