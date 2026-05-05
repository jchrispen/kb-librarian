"""Command line interface for KB Librarian."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

from kb_librarian import __version__
from kb_librarian.config import load_config, resolve_data_dir
from kb_librarian.errors import (
    AmbiguousNoteIdError,
    KBLibrarianError,
    MilestoneNotImplementedError,
    NoteNotFoundError,
    NoteValidationError,
)
from kb_librarian.indexing import ReindexResult, reindex_data_dir
from kb_librarian.init import initialize_data_dir
from kb_librarian.ingest import ingest, render_report
from kb_librarian.notes import KNOWLEDGE_TYPES, Note, body_template, generate_note_id, parse_note_text, write_note
from kb_librarian.search_index import query_candidates, score_document, tokenize_query
from kb_librarian.storage import (
    canonical_note_path,
    ensure_topic_layout,
    ensure_unique_note_ids,
    existing_note_ids,
    load_note_records,
    normalize_topic_for_path,
)

PHASE = "Phase 01c"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kb",
        description="KB Librarian local knowledge CLI.",
    )
    parser.add_argument("--version", action="version", version=f"kb-librarian {__version__}")

    subcommands = parser.add_subparsers(dest="command", metavar="<command>")
    _add_init_parser(subcommands)
    _add_add_parser(subcommands)
    _add_reindex_parser(subcommands)
    _add_search_parser(subcommands)
    _add_get_parser(subcommands)
    _add_ingest_parser(subcommands)
    _add_placeholder_parsers(subcommands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if not hasattr(args, "handler"):
            parser.print_help()
            return 0
        return int(args.handler(args))
    except KBLibrarianError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _add_init_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    init_parser = subcommands.add_parser(
        "init",
        help="Initialize a local KB data directory.",
        description="Initialize a local KB data directory without overwriting existing content.",
    )
    init_parser.add_argument(
        "--data-dir",
        help="KB data directory. Overrides KB_DATA_DIR and configured defaults.",
    )
    init_parser.add_argument(
        "--hooks",
        action="store_true",
        help="Record hook support in a newly created config.",
    )
    init_parser.set_defaults(handler=_handle_init)


def _add_placeholder_parsers(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    context_parser = _placeholder_parser(subcommands, "context", "Return task-shaped context.")
    context_parser.add_argument("task", nargs="?", help="Future task description.")
    context_parser.add_argument("--mode", help="Future context mode.")
    context_parser.add_argument("--budget", type=int, help="Future token budget.")
    context_parser.add_argument("--json", action="store_true", help="Future JSON output.")

    review_parser = _placeholder_parser(subcommands, "review", "Inspect review queues.")
    review_parser.add_argument(
        "action",
        nargs="?",
        choices=("list", "accept", "reject", "defer", "explain"),
        help="Future review action.",
    )


def _placeholder_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
) -> argparse.ArgumentParser:
    parser = subcommands.add_parser(
        name,
        help=help_text,
        description=f"{help_text} This command is not implemented in {PHASE}.",
    )
    parser.add_argument(
        "--data-dir",
        help="KB data directory. Overrides KB_DATA_DIR and configured defaults.",
    )
    parser.set_defaults(handler=_handle_placeholder)
    return parser


def _add_add_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "add",
        help="Create a note from direct input, or queue raw input when metadata is incomplete.",
        description="Create a note when --topic and --type are provided. Otherwise queue input under raw/.",
    )
    parser.add_argument("--data-dir", help="KB data directory. Overrides KB_DATA_DIR and configured defaults.")
    parser.add_argument("--topic", help="Canonical note topic value.")
    parser.add_argument("--type", dest="knowledge_type", help="Knowledge type for direct note creation.")
    parser.add_argument("--from-file", help="Read note body or raw input from this file path.")
    parser.set_defaults(handler=_handle_add)


def _add_reindex_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "reindex",
        help="Rebuild markdown and lexical indexes from note files.",
        description="Regenerate INDEX.md, topic indexes, backlinks, manifest, stats, and lexical index.",
    )
    parser.add_argument("--data-dir", help="KB data directory. Overrides KB_DATA_DIR and configured defaults.")
    parser.set_defaults(handler=_handle_reindex)


def _add_search_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "search",
        help="Search KB notes by lexical signals.",
        description="Search note titles, summaries, tags, retrieval phrases, and bodies.",
    )
    parser.add_argument("query", help="Search query text.")
    parser.add_argument("--data-dir", help="KB data directory. Overrides KB_DATA_DIR and configured defaults.")
    parser.add_argument("--topic", help="Topic filter.")
    parser.add_argument("--type", dest="knowledge_type", help="Knowledge type filter.")
    parser.add_argument("--budget", type=int, help="Approximate response token budget.")
    parser.add_argument("--json", action="store_true", help="Return machine-readable JSON output.")
    parser.set_defaults(handler=_handle_search)


def _add_get_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "get",
        help="Inspect one note by ID.",
        description="Print full note markdown or a compact summary by note ID.",
    )
    parser.add_argument("id", help="Note ID.")
    parser.add_argument("--data-dir", help="KB data directory. Overrides KB_DATA_DIR and configured defaults.")
    parser.add_argument("--summary", action="store_true", help="Print summary fields instead of full markdown.")
    parser.set_defaults(handler=_handle_get)


def _add_ingest_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "ingest",
        help="Ingest markdown or text into candidate notes.",
        description="Process one markdown/text file or pending markdown/text files under raw/.",
    )
    parser.add_argument("file", nargs="?", help="Optional markdown/text file to ingest.")
    parser.add_argument("--data-dir", help="KB data directory. Overrides KB_DATA_DIR and configured defaults.")
    parser.add_argument("--force", action="store_true", help="Reprocess even if the raw hash was previously successful.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the success report.")
    parser.set_defaults(handler=_handle_ingest)


def _handle_init(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    created = initialize_data_dir(data_dir, hooks=args.hooks)
    print(f"Initialized KB at {data_dir}")
    if created:
        print(f"Created {len(created)} files/directories.")
    else:
        print("Already initialized; no files changed.")
    return 0


def _handle_placeholder(args: argparse.Namespace) -> int:
    raise MilestoneNotImplementedError(f"kb {args.command} is not implemented in {PHASE}.")


def _handle_ingest(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    initialize_data_dir(data_dir)
    config = load_config(data_dir)
    report = ingest(
        data_dir,
        config=config,
        file_path=args.file,
        force=bool(args.force),
        quiet=bool(args.quiet),
    )
    if not args.quiet:
        print(render_report(report), end="")
    elif report.errors:
        print(f"error: ingest completed with {report.errors} errors; see {data_dir / '.kb' / 'errors.log'}", file=sys.stderr)
    return 1 if report.errors else 0


def _handle_add(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    initialize_data_dir(data_dir)
    source_text, source_name = _read_add_input(args.from_file)
    parsed_seed = _try_parse_seed_note(source_text)

    knowledge_type = args.knowledge_type.strip() if args.knowledge_type else None
    topic = args.topic.strip() if args.topic else None

    if knowledge_type and knowledge_type not in KNOWLEDGE_TYPES:
        allowed = ", ".join(sorted(KNOWLEDGE_TYPES))
        raise NoteValidationError(
            f"Unsupported knowledge_type {knowledge_type!r}; expected one of: {allowed}"
        )

    if not topic or not knowledge_type:
        queued_path = _queue_raw_input(data_dir, source_text, source_name=source_name)
        print(f"Queued raw input at {queued_path}")
        return 0

    records = load_note_records(data_dir, validate=True)
    ensure_unique_note_ids(records)
    note_id = generate_note_id(
        _extract_title(source_text, parsed_note=parsed_seed),
        date.today(),
        existing_ids=existing_note_ids(records),
    )

    created_date = date.today().isoformat()
    note = _build_note(
        source_text=source_text,
        parsed_note=parsed_seed,
        note_id=note_id,
        topic=topic,
        knowledge_type=knowledge_type,
        created_date=created_date,
    )
    ensure_topic_layout(data_dir, topic)
    path = canonical_note_path(data_dir, topic, note_id)
    write_note(path, note)

    result = reindex_data_dir(data_dir)
    print(f"Created note {note_id} at {path}")
    _print_reindex_result(result)
    return 0


def _handle_reindex(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    initialize_data_dir(data_dir)
    result = reindex_data_dir(data_dir)
    _print_reindex_result(result)
    return 0


def _handle_search(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    initialize_data_dir(data_dir)
    config = load_config(data_dir)
    fts_path = data_dir / ".kb" / "fts.sqlite"
    if not fts_path.exists():
        reindex_data_dir(data_dir)

    candidates = query_candidates(
        fts_path,
        query=args.query,
        topic=args.topic,
        knowledge_type=args.knowledge_type,
    )
    retrieval = config["retrieval"]
    tokens = tokenize_query(args.query)
    scored: list[dict[str, object]] = []
    for candidate in candidates:
        score = score_document(
            candidate,
            query=args.query,
            tokens=tokens,
            weights=retrieval,
        )
        if score <= 0:
            continue
        scored.append(
            {
                **candidate,
                "score": score,
            }
        )

    scored.sort(
        key=lambda item: (
            -float(item["score"]),
            str(item.get("updated", "")),
            str(item.get("id", "")),
        )
    )
    budget = args.budget if args.budget is not None else int(retrieval["default_budget_tokens"])
    limited = _apply_budget(scored, budget)

    if args.json:
        print(json.dumps(limited, indent=2, sort_keys=True))
        return 0

    if not limited:
        print("No results.")
        return 0

    for item in limited:
        print(f'{item["id"]} | {item["title"]}')
        print(
            "type={knowledge_type} status={status} confidence={confidence} topic={topic}".format(
                knowledge_type=item["knowledge_type"],
                status=item["status"],
                confidence=item["confidence"],
                topic=item["topic"],
            )
        )
        print(f'summary: {item["summary"]}')
        print(f'path: {item["path"]}')
        print("")
    return 0


def _handle_get(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    initialize_data_dir(data_dir)
    records = load_note_records(data_dir, validate=True)
    matches = [record for record in records if record.note_id == args.id]
    if not matches:
        raise NoteNotFoundError(f"Note ID {args.id!r} was not found.")
    if len(matches) > 1:
        paths = ", ".join(str(record.path) for record in sorted(matches, key=lambda item: str(item.path)))
        raise AmbiguousNoteIdError(f"Note ID {args.id!r} is ambiguous across: {paths}")

    record = matches[0]
    if args.summary:
        fm = record.note.frontmatter
        phrases = fm.get("retrieval_phrases") or []
        phrase_text = ", ".join(str(item) for item in phrases) if phrases else "(none)"
        print(f'title: {fm["title"]}')
        print(f'summary: {fm["summary"]}')
        print(f'type: {fm["knowledge_type"]}')
        print(f'status: {fm["status"]}')
        print(f'confidence: {fm["confidence"]}')
        print(f'topic: {fm["topic"]}')
        print(f"path: {record.path}")
        print(f"retrieval_phrases: {phrase_text}")
        return 0

    print(record.path.read_text(encoding="utf-8"), end="")
    return 0


def _read_add_input(from_file: str | None) -> tuple[str, str]:
    if from_file:
        path = Path(from_file).expanduser()
        try:
            return path.read_text(encoding="utf-8"), path.name
        except OSError as exc:
            raise KBLibrarianError(f"Could not read input file {path}: {exc}") from exc

    if sys.stdin.isatty():
        raise KBLibrarianError("No input provided. Use --from-file or pipe text to stdin.")
    return sys.stdin.read(), "stdin"


def _build_note(
    *,
    source_text: str,
    parsed_note: Note | None,
    note_id: str,
    topic: str,
    knowledge_type: str,
    created_date: str,
) -> Note:
    body = _extract_body(source_text, parsed_note=parsed_note, knowledge_type=knowledge_type)
    title = _extract_title(source_text, parsed_note=parsed_note)
    summary = _extract_summary(source_text, parsed_note=parsed_note)
    retrieval_phrases = _extract_string_list(parsed_note, "retrieval_phrases")
    tags = _extract_string_list(parsed_note, "tags")
    if not tags:
        tags = [normalize_topic_for_path(topic)]

    frontmatter = {
        "id": note_id,
        "title": title,
        "summary": summary,
        "topic": topic,
        "created": created_date,
        "updated": created_date,
        "knowledge_type": knowledge_type,
        "status": "active",
        "confidence": "medium",
        "retrieval_phrases": retrieval_phrases,
        "tags": tags,
    }
    note = Note(frontmatter=frontmatter, body=body)
    note.validate()
    return note


def _try_parse_seed_note(source_text: str) -> Note | None:
    try:
        return parse_note_text(source_text, validate=False)
    except KBLibrarianError:
        return None


def _extract_body(source_text: str, *, parsed_note: Note | None, knowledge_type: str) -> str:
    if parsed_note and parsed_note.body.strip():
        body = parsed_note.body
    elif source_text.strip():
        body = source_text.strip() + "\n"
    else:
        body = body_template(knowledge_type)

    if not body.endswith("\n"):
        body += "\n"
    return body


def _extract_title(source_text: str, *, parsed_note: Note | None = None) -> str:
    if parsed_note:
        title = parsed_note.frontmatter.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()

    for line in source_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading[:120]
        return stripped[:120]
    return "Untitled note"


def _extract_summary(source_text: str, *, parsed_note: Note | None = None) -> str:
    if parsed_note:
        summary = parsed_note.frontmatter.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()

    for line in source_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        sentence = stripped.split(".")[0].strip()
        if sentence:
            return sentence[:240]
    return "No summary provided."


def _extract_string_list(parsed_note: Note | None, field: str) -> list[str]:
    if not parsed_note:
        return []
    value = parsed_note.frontmatter.get(field)
    if not isinstance(value, list):
        return []
    extracted: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                extracted.append(stripped)
    return extracted


def _queue_raw_input(data_dir: Path, source_text: str, *, source_name: str) -> Path:
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    if source_name == "stdin":
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"stdin-{stamp}.md"
    else:
        filename = Path(source_name).name
    destination = _dedupe_path(raw_dir / filename)

    payload = source_text
    if payload and not payload.endswith("\n"):
        payload += "\n"
    destination.write_text(payload, encoding="utf-8")
    return destination


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _apply_budget(results: list[dict[str, object]], budget: int) -> list[dict[str, object]]:
    if budget <= 0:
        return results
    selected: list[dict[str, object]] = []
    used = 0
    for item in results:
        token_cost = _estimate_result_tokens(item)
        if selected and used + token_cost > budget:
            break
        selected.append(item)
        used += token_cost
    return selected


def _estimate_result_tokens(result: dict[str, object]) -> int:
    text = f'{result.get("title", "")} {result.get("summary", "")}'
    return max(1, len(text.split()) + 16)


def _print_reindex_result(result: ReindexResult) -> None:
    print(
        f"Reindexed {result.note_count} notes across {result.topic_count} topics using {result.index_backend}."
    )
    for artifact in result.artifacts:
        print(str(artifact))


if __name__ == "__main__":
    raise SystemExit(main())
