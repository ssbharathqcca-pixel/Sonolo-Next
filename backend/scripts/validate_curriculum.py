"""Validate C1 curriculum JSON (Part XVI C1). Run from backend/:

    python -m scripts.validate_curriculum
"""

from app.services.content_service import validate_curriculum_content


def main() -> int:
    errors = validate_curriculum_content()
    if errors:
        print("Curriculum validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("Curriculum validation passed (0 errors).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
