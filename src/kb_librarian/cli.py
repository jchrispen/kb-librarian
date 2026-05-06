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
from kb_librarian.context import CONTEXT_MODES, build_context, build_explore
from kb_librarian.errors import (
    AmbiguousNoteIdError,
    KBLibrarianError,
    NoteNotFoundError,
    NoteValidationError,
)
from kb_librarian.indexing import ReindexResult, reindex_data_dir
from kb_librarian.init import initialize_data_dir, render_preamble_guidance
from kb_librarian.ingest import ingest, ingest_report_payload, render_report
from kb_librarian.notes import KNOWLEDGE_TYPES, Note, body_template, generate_note_id, parse_note_text, write_note
from kb_librarian.review import (
    accept_review_item,
    collect_review_summary,
    defer_review_item,
    explain_review_item,
    reject_review_item,
    render_review_summary,
)
from kb_librarian.search_index import query_candidates, score_document, tokenize_query
from kb_librarian.storage import (
    canonical_note_path,
    ensure_topic_layout,
    ensure_unique_note_ids,
    existing_note_ids,
    load_note_records,
    normalize_topic_for_path,
)
from kb_librarian.usage import (
    log_note_use,
    log_retrieval,
    maybe_log_search_miss,
    render_usage_summary,
    summarize_usage,
)

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
    _add_review_parser(subcommands)
    _add_context_parser(subcommands)
    _add_explore_parser(subcommands)
    _add_log_use_parser(subcommands)
    _add_usage_parser(subcommands)
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


def _add_context_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "context",
        help="Return compact task-shaped context with source citations.",
        description="Retrieve high-precision context for a task and synthesize a compact cited response.",
    )
    parser.add_argument("task", help="Task description used for context retrieval.")
    parser.add_argument(
        "--mode",
        choices=CONTEXT_MODES,
        default="coding",
        help="Context mode: coding, architecture, debugging, writing, research, review.",
    )
    parser.add_argument("--budget", type=int, help="Context token budget.")
    parser.add_argument("--json", action="store_true", help="Return machine-readable JSON output.")
    parser.add_argument("--with-citations", action="store_true", help="Include the citation block. Enabled by default.")
    parser.add_argument("--report-miss", action="store_true", help="Record this retrieval as a poor-result search miss.")
    parser.add_argument(
        "--data-dir",
        help="KB data directory. Overrides KB_DATA_DIR and configured defaults.",
    )
    parser.set_defaults(handler=_handle_context)


def _add_explore_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "explore",
        help="Return broader associations and ideas with source citations.",
        description="Retrieve higher-recall exploration context for ideation, alternatives, and adjacent concepts.",
    )
    parser.add_argument("problem", help="Problem or idea space used for broad exploration.")
    parser.add_argument("--budget", type=int, help="Exploration token budget.")
    parser.add_argument("--json", action="store_true", help="Return machine-readable JSON output.")
    parser.add_argument("--with-citations", action="store_true", help="Include the citation block. Enabled by default.")
    parser.add_argument("--report-miss", action="store_true", help="Record this retrieval as a poor-result search miss.")
    parser.add_argument("--data-dir", help="KB data directory. Overrides KB_DATA_DIR and configured defaults.")
    parser.set_defaults(handler=_handle_explore)


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
    parser.add_argument("--with-citations", action="store_true", help="Include a citation block in human-readable output.")
    parser.add_argument("--report-miss", action="store_true", help="Record this search as a poor-result search miss.")
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
    parser.add_argument("--json", action="store_true", help="Return machine-readable ingest report output.")
    parser.set_defaults(handler=_handle_ingest)


def _add_review_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "review",
        help="Inspect review queues.",
        description="Print bounded review counts and queue excerpts.",
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("list", "explain", "accept", "reject", "defer"),
        help="Optional action. `list` is equivalent to `kb review`.",
    )
    parser.add_argument("item_id", nargs="?", help="Review item ID used by explain/accept/reject/defer.")
    parser.add_argument("--days", type=int, help="Days to defer an item; required for `kb review defer`.")
    parser.add_argument("--topic", help="Topic for classification acceptance when creating a note.")
    parser.add_argument("--type", dest="knowledge_type", help="Knowledge type for classification acceptance.")
    parser.add_argument("--note-id", help="Existing note ID for source-append acceptance.")
    parser.add_argument(
        "--append-body",
        action="store_true",
        help="Required for merge acceptance; explicitly approves candidate body append.",
    )
    parser.add_argument("--resolution-note", help="Resolution text for search-miss acceptance.")
    parser.add_argument("--data-dir", help="KB data directory. Overrides KB_DATA_DIR and configured defaults.")
    parser.set_defaults(handler=_handle_review)


def _add_log_use_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "log-use",
        help="Record that an agent used or cited a note.",
        description="Append an explicit note-use signal to .kb/usage.log.",
    )
    parser.add_argument("id", help="Note ID that was used or cited.")
    parser.add_argument("--task", help="Optional short task summary.")
    parser.add_argument("--data-dir", help="KB data directory. Overrides KB_DATA_DIR and configured defaults.")
    parser.set_defaults(handler=_handle_log_use)


def _add_usage_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "usage",
        help="Summarize retrieval and note-use signals.",
        description="Print a concise operational summary from .kb/usage.log and .kb/search-misses.log.",
    )
    parser.add_argument("--since", help="Duration such as 7d, 24h, 30m, 2w, or an ISO date.")
    parser.add_argument("--note", help="Filter usage summary to one note ID.")
    parser.add_argument("--data-dir", help="KB data directory. Overrides KB_DATA_DIR and configured defaults.")
    parser.set_defaults(handler=_handle_usage)


def _handle_init(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    created = initialize_data_dir(data_dir, hooks=args.hooks)
    print(f"Initialized KB at {data_dir}")
    if created:
        print(f"Created {len(created)} files/directories.")
    else:
        print("Already initialized; no files changed.")
    if args.hooks:
        print(render_preamble_guidance(data_dir), end="")
    return 0


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
    if args.json and not args.quiet:
        print(json.dumps(ingest_report_payload(report), indent=2, sort_keys=True))
    elif not args.quiet:
        print(render_report(report), end="")
    elif report.errors:
        print(f"error: ingest completed with {report.errors} errors; see {data_dir / '.kb' / 'errors.log'}", file=sys.stderr)
    return 1 if report.errors else 0


def _handle_review(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    initialize_data_dir(data_dir)
    config = load_config(data_dir)
    action = str(args.action or "list")

    if action in {"list"}:
        max_items = int(config.get("review", {}).get("max_review_items_per_run", 10))
        counts, items = collect_review_summary(data_dir, max_items=max_items)
        print(render_review_summary(counts, items, max_items=max_items), end="")
        return 0

    item_id = str(args.item_id or "").strip()
    if not item_id:
        raise KBLibrarianError(f"`kb review {action}` requires <item-id>.")

    if action == "explain":
        print(explain_review_item(data_dir, item_id), end="")
        return 0

    if action == "accept":
        result = accept_review_item(
            data_dir,
            item_id,
            topic=args.topic,
            knowledge_type=args.knowledge_type,
            note_id=args.note_id,
            append_body=bool(args.append_body),
            resolution_note=args.resolution_note,
        )
        print(result.message)
        return 0

    if action == "reject":
        result = reject_review_item(data_dir, item_id)
        print(result.message)
        return 0

    if action == "defer":
        if args.days is None:
            raise KBLibrarianError("`kb review defer <item-id>` requires --days <n>.")
        result = defer_review_item(data_dir, item_id, days=int(args.days))
        print(result.message)
        return 0

    raise KBLibrarianError(f"Unsupported review action: {action!r}")


def _handle_context(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    initialize_data_dir(data_dir)
    config = load_config(data_dir)

    mode = str(args.mode or "coding")
    if mode not in CONTEXT_MODES:
        allowed = ", ".join(CONTEXT_MODES)
        raise KBLibrarianError(f"Unsupported mode {mode!r}; expected one of: {allowed}")

    budget = args.budget if args.budget is not None else int(config["retrieval"]["context_budget_tokens"])
    if budget <= 0:
        raise KBLibrarianError("Context budget must be a positive integer.")

    result = build_context(
        data_dir,
        config=config,
        task=str(args.task).strip(),
        mode=mode,
        budget=budget,
    )
    top_score = result.selected_notes[0].score if result.selected_notes else None
    log_retrieval(
        data_dir,
        command="context",
        query=result.task,
        task=result.task,
        mode=result.mode,
        budget=result.budget,
        returned_note_ids=[item.note_id for item in result.selected_notes],
        result_count=len(result.selected_notes),
        top_score=top_score,
        synthesis_succeeded=bool(result.synthesis_markdown),
    )
    maybe_log_search_miss(
        data_dir,
        command="context",
        query=result.task,
        task=result.task,
        result_count=len(result.selected_notes),
        top_score=top_score,
        filters={"mode": result.mode},
        report_miss=bool(args.report_miss),
    )

    if args.json:
        payload = {
            "task": result.task,
            "mode": result.mode,
            "budget": result.budget,
            "message": result.message,
            "synthesis_markdown": result.synthesis_markdown,
            "selected_notes": [
                {
                    "note_id": item.note_id,
                    "title": item.title,
                    "summary": item.summary,
                    "topic": item.topic,
                    "knowledge_type": item.knowledge_type,
                    "status": item.status,
                    "confidence": item.confidence,
                    "updated": item.updated,
                    "path": item.path,
                    "excerpt": item.excerpt,
                    "score": item.score,
                    "reasons": item.reasons,
                    "trust_flags": item.trust_flags,
                }
                for item in result.selected_notes
            ],
            "citations": result.citations,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if result.message:
        print(result.message)
        print(f"Try: kb search {json.dumps(result.task)}")
        return 0

    print("# KB Context")
    print("")
    if result.synthesis_markdown:
        print(result.synthesis_markdown)
        print("")
    _print_citation_block(result.citations)
    return 0


def _handle_explore(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    initialize_data_dir(data_dir)
    config = load_config(data_dir)

    budget = args.budget if args.budget is not None else int(config["retrieval"]["explore_budget_tokens"])
    if budget <= 0:
        raise KBLibrarianError("Explore budget must be a positive integer.")

    result = build_explore(
        data_dir,
        config=config,
        problem=str(args.problem).strip(),
        budget=budget,
    )
    top_score = result.selected_notes[0].score if result.selected_notes else None
    log_retrieval(
        data_dir,
        command="explore",
        query=result.problem,
        task=result.problem,
        budget=result.budget,
        returned_note_ids=[item.note_id for item in result.selected_notes],
        result_count=len(result.selected_notes),
        top_score=top_score,
        synthesis_succeeded=bool(result.synthesis_markdown),
    )
    maybe_log_search_miss(
        data_dir,
        command="explore",
        query=result.problem,
        task=result.problem,
        result_count=len(result.selected_notes),
        top_score=top_score,
        filters={},
        report_miss=bool(args.report_miss),
    )

    if args.json:
        payload = {
            "problem": result.problem,
            "budget": result.budget,
            "message": result.message,
            "synthesis_markdown": result.synthesis_markdown,
            "selected_notes": [
                {
                    "note_id": item.note_id,
                    "title": item.title,
                    "summary": item.summary,
                    "topic": item.topic,
                    "knowledge_type": item.knowledge_type,
                    "status": item.status,
                    "confidence": item.confidence,
                    "updated": item.updated,
                    "path": item.path,
                    "excerpt": item.excerpt,
                    "score": item.score,
                    "reasons": item.reasons,
                    "trust_flags": item.trust_flags,
                }
                for item in result.selected_notes
            ],
            "citations": result.citations,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if result.message:
        print(result.message)
        print(f"Try: kb search {json.dumps(result.problem)}")
        return 0

    print("# Exploration")
    print("")
    if result.synthesis_markdown:
        print(result.synthesis_markdown)
        print("")
    _print_citation_block(result.citations)
    return 0


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
    if budget <= 0:
        raise KBLibrarianError("Search budget must be a positive integer.")
    limited = _apply_budget(scored, budget)
    citations = [_search_citation(item, data_dir=data_dir) for item in limited]
    top_score = float(limited[0]["score"]) if limited else None
    filters = {"topic": args.topic, "knowledge_type": args.knowledge_type}
    log_retrieval(
        data_dir,
        command="search",
        query=str(args.query),
        budget=budget,
        returned_note_ids=[str(item.get("id", "")) for item in limited],
        result_count=len(limited),
        top_score=top_score,
        filters=filters,
        synthesis_succeeded=None,
    )
    maybe_log_search_miss(
        data_dir,
        command="search",
        query=str(args.query),
        result_count=len(limited),
        top_score=top_score,
        filters=filters,
        report_miss=bool(args.report_miss),
    )

    if args.json:
        payload = [
            {
                **item,
                "citation": _search_citation(item, data_dir=data_dir),
            }
            for item in limited
        ]
        print(json.dumps(payload, indent=2, sort_keys=True))
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
    if args.with_citations:
        _print_citation_block(citations)
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


def _handle_log_use(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    initialize_data_dir(data_dir)
    _require_note_id(data_dir, str(args.id))
    log_note_use(data_dir, note_id=str(args.id), task=args.task)
    print(f"Logged use of note {args.id}.")
    return 0


def _handle_usage(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    initialize_data_dir(data_dir)
    note_id = str(args.note).strip() if args.note else None
    if note_id:
        _require_note_id(data_dir, note_id)
    summary = summarize_usage(data_dir, since=args.since, note_id=note_id)
    print(render_usage_summary(summary), end="")
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


def _search_citation(item: dict[str, object], *, data_dir: Path) -> dict[str, str]:
    path = Path(str(item.get("path", "")))
    try:
        rendered_path = path.relative_to(data_dir).as_posix()
    except ValueError:
        rendered_path = path.as_posix()
    return {
        "note_id": str(item.get("id", "")),
        "path": rendered_path,
        "title": str(item.get("title", "")),
        "status": str(item.get("status", "")),
        "confidence": str(item.get("confidence", "")),
    }


def _print_citation_block(citations: list[dict[str, str]]) -> None:
    print("## Source notes")
    for citation in citations:
        print(
            "- [{note_id}]({path}) — title: {title}; confidence: {confidence}; status: {status}".format(
                note_id=citation["note_id"],
                path=citation["path"],
                title=citation["title"],
                confidence=citation["confidence"],
                status=citation["status"],
            )
        )
    print("")
    source_refs = ", ".join(
        "[{note_id}]({path}) (confidence: {confidence}, status: {status})".format(
            note_id=citation["note_id"],
            path=citation["path"],
            confidence=citation["confidence"],
            status=citation["status"],
        )
        for citation in citations
    )
    print("---")
    print(f"**KB sources:** {source_refs}")


def _require_note_id(data_dir: Path, note_id: str) -> None:
    records = load_note_records(data_dir, validate=True)
    matches = [record for record in records if record.note_id == note_id]
    if not matches:
        raise NoteNotFoundError(f"Note ID {note_id!r} was not found.")
    if len(matches) > 1:
        paths = ", ".join(str(record.path) for record in sorted(matches, key=lambda item: str(item.path)))
        raise AmbiguousNoteIdError(f"Note ID {note_id!r} is ambiguous across: {paths}")


def _print_reindex_result(result: ReindexResult) -> None:
    print(
        f"Reindexed {result.note_count} notes across {result.topic_count} topics using {result.index_backend}."
    )
    for artifact in result.artifacts:
        print(str(artifact))


if __name__ == "__main__":
    raise SystemExit(main())
