from __future__ import annotations

from datetime import date, datetime

import pytest

from kb_librarian.errors import NoteParseError, NoteValidationError
from kb_librarian.notes import (
    BODY_TEMPLATES,
    Note,
    _date_to_iso,
    _normalize_yaml_value,
    _require_non_empty_string,
    _validate_string_list,
    body_template,
    generate_note_id,
    parse_note_text,
    read_note,
    validate_frontmatter,
    write_note,
)


def valid_frontmatter() -> dict[str, object]:
    return {
        "id": "2026-05-04-agent-context-cli-contract",
        "title": "Use a CLI as the stable contract",
        "summary": "A stable CLI lets multiple agents access the same knowledge artifact.",
        "topic": "agent-systems",
        "created": "2026-05-04",
        "updated": "2026-05-04",
        "knowledge_type": "technique",
        "status": "active",
        "confidence": "high",
        "basis": ["design judgment"],
        "sources": [{"type": "conversation", "ref": "kb-flag-2026-05-04-2312"}],
        "retrieval_phrases": ["agent-accessible knowledge", "cross-agent memory"],
        "agent_use": ["architecture review"],
        "applies_when": ["building local-first tools"],
        "does_not_apply_when": ["building collaborative SaaS"],
        "failure_modes": ["CLI output becomes too verbose"],
        "staleness_risk": "low",
        "reviewed_by_user": True,
        "disputes": [],
        "tags": ["cli", "retrieval"],
    }


def test_parse_validate_and_round_trip_note_text():
    note = Note(valid_frontmatter(), "## Use when\n\nUse this for cross-agent tools.\n")
    text = note.to_markdown()

    parsed = parse_note_text(text)
    rendered = parsed.to_markdown()

    assert parsed.frontmatter["created"] == "2026-05-04"
    assert "sources:" in rendered
    assert "agent_use:" in rendered
    assert rendered.splitlines()[1] == "id: 2026-05-04-agent-context-cli-contract"
    assert rendered.splitlines()[2] == "title: Use a CLI as the stable contract"
    assert parse_note_text(rendered).body == "## Use when\n\nUse this for cross-agent tools.\n"


def test_write_and_read_note(tmp_path):
    path = tmp_path / "topics" / "agent-systems" / "2026-05-04-agent-context-cli-contract.md"
    note = Note(valid_frontmatter(), body_template("technique"))

    write_note(path, note)

    assert read_note(path).frontmatter["id"] == "2026-05-04-agent-context-cli-contract"


def test_validation_requires_all_minimum_fields():
    frontmatter = valid_frontmatter()
    del frontmatter["tags"]

    with pytest.raises(NoteValidationError, match="tags"):
        validate_frontmatter(frontmatter)


def test_validation_enforces_enums():
    frontmatter = valid_frontmatter()
    frontmatter["knowledge_type"] = "article"

    with pytest.raises(NoteValidationError, match="knowledge_type"):
        validate_frontmatter(frontmatter)


def test_validation_enforces_string_lists():
    frontmatter = valid_frontmatter()
    frontmatter["retrieval_phrases"] = ["good", 42]

    with pytest.raises(NoteValidationError, match=r"retrieval_phrases\[1\]"):
        validate_frontmatter(frontmatter)


def test_generate_note_id_slugifies_and_suffixes_collisions():
    existing = {
        "2026-05-04-use-a-cli-contract",
        "2026-05-04-use-a-cli-contract-2",
    }

    assert generate_note_id("Use a CLI contract!", "2026-05-04") == "2026-05-04-use-a-cli-contract"
    assert (
        generate_note_id("Use a CLI contract!", "2026-05-04", existing_ids=existing)
        == "2026-05-04-use-a-cli-contract-3"
    )


def test_body_templates_cover_all_note_types():
    assert set(BODY_TEMPLATES) == {
        "fact",
        "technique",
        "heuristic",
        "pattern",
        "anti-pattern",
        "decision",
        "open-question",
    }
    assert "## Agent instruction" in body_template("pattern")
    assert "## Question" in body_template("open-question")

    with pytest.raises(NoteValidationError):
        body_template("memo")


def test_parse_note_text_raises_on_missing_closing_delimiter():
    text = "---\nid: test\n"
    with pytest.raises(NoteParseError, match="missing closing"):
        parse_note_text(text, validate=False)


def test_parse_note_text_raises_on_invalid_yaml():
    text = "---\nkey: !!python/object:os.system [id]\n---\nbody\n"
    with pytest.raises(NoteParseError, match="Malformed YAML"):
        parse_note_text(text, validate=False)


def test_parse_note_text_raises_on_non_mapping_yaml():
    text = "---\njust a scalar value\n---\nbody\n"
    with pytest.raises(NoteParseError, match="must be a mapping"):
        parse_note_text(text, validate=False)


def test_validate_frontmatter_raises_on_non_mapping():
    with pytest.raises(NoteValidationError, match="must be a mapping"):
        validate_frontmatter(42)


def test_require_non_empty_string_raises_on_empty():
    with pytest.raises(NoteValidationError, match="must be a non-empty string"):
        _require_non_empty_string("", "title")


def test_validate_string_list_raises_on_non_list():
    with pytest.raises(NoteValidationError, match="must be a list"):
        _validate_string_list(42, "tags")


def test_date_to_iso_handles_datetime_object():
    result = _date_to_iso(datetime(2026, 1, 15, 12, 0), "created")
    assert result == "2026-01-15"


def test_date_to_iso_raises_on_invalid_date_string():
    with pytest.raises(NoteValidationError, match="must be an ISO date"):
        _date_to_iso("not-a-date", "created")


def test_date_to_iso_raises_on_non_string_non_date():
    with pytest.raises(NoteValidationError, match="must be an ISO date string"):
        _date_to_iso(20260115, "created")


def test_normalize_yaml_value_converts_datetime_to_date():
    dt = datetime(2026, 3, 1, 10, 30)
    assert _normalize_yaml_value(dt) == date(2026, 3, 1)
