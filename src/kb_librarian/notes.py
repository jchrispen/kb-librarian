"""Markdown/frontmatter note model and helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from kb_librarian.atomic import atomic_write_text
from kb_librarian.errors import NoteParseError, NoteValidationError

KNOWLEDGE_TYPES = {
    "fact",
    "technique",
    "heuristic",
    "pattern",
    "anti-pattern",
    "decision",
    "open-question",
}

STATUSES = {"active", "disputed", "superseded", "needs-review", "archived"}

CONFIDENCE_LEVELS = {"high", "medium", "low"}

STALENESS_RISKS = {"low", "medium", "high"}

REQUIRED_FIELDS = (
    "id",
    "title",
    "summary",
    "topic",
    "created",
    "updated",
    "knowledge_type",
    "status",
    "confidence",
    "retrieval_phrases",
    "tags",
)

FRONTMATTER_ORDER = (
    "id",
    "title",
    "summary",
    "topic",
    "created",
    "updated",
    "knowledge_type",
    "status",
    "confidence",
    "basis",
    "sources",
    "retrieval_phrases",
    "agent_use",
    "applies_when",
    "does_not_apply_when",
    "failure_modes",
    "staleness_risk",
    "reviewed_by_user",
    "disputes",
    "tags",
)

BODY_TEMPLATES = {
    "fact": (
        "## Claim\n\n"
        "## Evidence\n\n"
        "## Caveats\n\n"
        "## Staleness risk\n\n"
        "## Related\n"
    ),
    "technique": (
        "## Use when\n\n"
        "## Core idea\n\n"
        "## Agent instruction\n\n"
        "## Procedure / application\n\n"
        "## Failure modes\n\n"
        "## Related\n"
    ),
    "heuristic": (
        "## Judgment\n\n"
        "## Basis\n\n"
        "## Applies when\n\n"
        "## Does not apply when\n\n"
        "## Failure modes\n\n"
        "## Related\n"
    ),
    "pattern": (
        "## Use when\n\n"
        "## Core idea\n\n"
        "## Agent instruction\n\n"
        "## Procedure / application\n\n"
        "## Failure modes\n\n"
        "## Related\n"
    ),
    "anti-pattern": (
        "## Use when\n\n"
        "## Core idea\n\n"
        "## Agent instruction\n\n"
        "## Procedure / application\n\n"
        "## Failure modes\n\n"
        "## Related\n"
    ),
    "decision": (
        "## Decision\n\n"
        "## Rationale\n\n"
        "## Alternatives considered\n\n"
        "## Consequences\n\n"
        "## Revisit if\n"
    ),
    "open-question": (
        "## Question\n\n"
        "## Why it matters\n\n"
        "## Current thinking\n\n"
        "## Possible answers\n\n"
        "## What would resolve it\n"
    ),
}

SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Note:
    """A markdown note split into YAML frontmatter and body."""

    frontmatter: dict[str, Any]
    body: str

    def validate(self) -> None:
        validate_frontmatter(self.frontmatter)

    def to_markdown(self) -> str:
        """Render the note with stable frontmatter ordering."""

        self.validate()
        ordered = order_frontmatter(self.frontmatter)
        frontmatter = yaml.safe_dump(
            ordered,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=False,
        ).strip()
        body = self.body
        if body and not body.startswith("\n"):
            body = "\n" + body
        return f"---\n{frontmatter}\n---{body}"


def parse_note_text(text: str, *, validate: bool = True) -> Note:
    """Parse markdown text with YAML frontmatter."""

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise NoteParseError("Markdown note must start with YAML frontmatter delimiter '---'.")

    end_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break

    if end_index is None:
        raise NoteParseError("Markdown note is missing closing YAML frontmatter delimiter '---'.")

    raw_frontmatter = "".join(lines[1:end_index])
    try:
        loaded = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        raise NoteParseError(f"Malformed YAML frontmatter: {exc}") from exc

    if not isinstance(loaded, dict):
        raise NoteParseError("YAML frontmatter must be a mapping.")

    body = "".join(lines[end_index + 1 :])
    note = Note(dict(loaded), body)
    if validate:
        note.validate()
    return note


def read_note(path: str | Path, *, validate: bool = True) -> Note:
    return parse_note_text(Path(path).read_text(encoding="utf-8"), validate=validate)


def write_note(path: str | Path, note: Note) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(destination, note.to_markdown())


def order_frontmatter(frontmatter: Mapping[str, Any]) -> dict[str, Any]:
    """Return frontmatter ordered by the published note schema."""

    ordered: dict[str, Any] = {}
    for key in FRONTMATTER_ORDER:
        if key in frontmatter:
            ordered[key] = _normalize_yaml_value(frontmatter[key])

    for key in sorted(frontmatter):
        if key not in ordered:
            ordered[key] = _normalize_yaml_value(frontmatter[key])

    return ordered


def validate_frontmatter(frontmatter: Mapping[str, Any]) -> None:
    if not isinstance(frontmatter, Mapping):
        raise NoteValidationError("Note frontmatter must be a mapping.")

    for field in REQUIRED_FIELDS:
        if field not in frontmatter:
            raise NoteValidationError(f"Note frontmatter is missing required field: {field}")

    for field in ("id", "title", "summary", "topic"):
        _require_non_empty_string(frontmatter[field], field)

    _validate_date(frontmatter["created"], "created")
    _validate_date(frontmatter["updated"], "updated")
    _validate_enum(frontmatter["knowledge_type"], KNOWLEDGE_TYPES, "knowledge_type")
    _validate_enum(frontmatter["status"], STATUSES, "status")
    _validate_enum(frontmatter["confidence"], CONFIDENCE_LEVELS, "confidence")

    if "staleness_risk" in frontmatter and frontmatter["staleness_risk"] is not None:
        _validate_enum(frontmatter["staleness_risk"], STALENESS_RISKS, "staleness_risk")

    _validate_string_list(frontmatter["retrieval_phrases"], "retrieval_phrases")
    _validate_string_list(frontmatter["tags"], "tags")


def generate_note_id(
    title: str,
    created: str | date | datetime,
    *,
    existing_ids: Iterable[str] = (),
) -> str:
    """Generate a deterministic note ID with a numeric suffix on collision."""

    created_text = _date_to_iso(created, "created")
    slug = slugify(title)
    base = f"{created_text}-{slug}"
    used = set(existing_ids)
    if base not in used:
        return base

    suffix = 2
    while f"{base}-{suffix}" in used:
        suffix += 1
    return f"{base}-{suffix}"


def slugify(value: str) -> str:
    slug = SLUG_PATTERN.sub("-", value.lower()).strip("-")
    return slug or "note"


def body_template(knowledge_type: str) -> str:
    try:
        return BODY_TEMPLATES[knowledge_type]
    except KeyError as exc:
        allowed = ", ".join(sorted(KNOWLEDGE_TYPES))
        raise NoteValidationError(f"Unknown knowledge_type {knowledge_type!r}; expected one of: {allowed}") from exc


def _require_non_empty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise NoteValidationError(f"Note field {field} must be a non-empty string.")


def _validate_enum(value: Any, allowed: set[str], field: str) -> None:
    if not isinstance(value, str) or value not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise NoteValidationError(f"Note field {field} must be one of: {allowed_text}")


def _validate_string_list(value: Any, field: str) -> None:
    if not isinstance(value, list):
        raise NoteValidationError(f"Note field {field} must be a list of strings.")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise NoteValidationError(f"Note field {field}[{index}] must be a string.")


def _validate_date(value: Any, field: str) -> None:
    _date_to_iso(value, field)


def _date_to_iso(value: str | date | datetime, field: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise NoteValidationError(f"Note field {field} must be an ISO date, got {value!r}.") from exc
        return parsed.isoformat()
    raise NoteValidationError(f"Note field {field} must be an ISO date string.")


def _normalize_yaml_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, dict):
        return {key: _normalize_yaml_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_yaml_value(item) for item in value]
    return value
