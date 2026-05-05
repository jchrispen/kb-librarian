"""Command line interface for KB Librarian."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from kb_librarian import __version__
from kb_librarian.config import resolve_data_dir
from kb_librarian.errors import KBLibrarianError, MilestoneNotImplementedError
from kb_librarian.init import initialize_data_dir

PHASE = "Phase 01a"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kb",
        description="KB Librarian local knowledge CLI.",
    )
    parser.add_argument("--version", action="version", version=f"kb-librarian {__version__}")

    subcommands = parser.add_subparsers(dest="command", metavar="<command>")
    _add_init_parser(subcommands)
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
    add_parser = _placeholder_parser(subcommands, "add", "Create a note from direct input.")
    add_parser.add_argument("--topic", help="Future note topic.")
    add_parser.add_argument("--type", dest="knowledge_type", help="Future note knowledge type.")
    add_parser.add_argument("--from-file", help="Future markdown source file.")

    ingest_parser = _placeholder_parser(subcommands, "ingest", "Ingest markdown or text into candidate notes.")
    ingest_parser.add_argument("file", nargs="?", help="Future input file.")
    ingest_parser.add_argument("--force", action="store_true", help="Future reprocess flag.")
    ingest_parser.add_argument("--quiet", action="store_true", help="Future quiet output flag.")

    reindex_parser = _placeholder_parser(subcommands, "reindex", "Rebuild generated indexes.")
    reindex_parser.add_argument("--all", action="store_true", help="Future full rebuild flag.")

    search_parser = _placeholder_parser(subcommands, "search", "Search KB notes.")
    search_parser.add_argument("query", nargs="?", help="Future search query.")
    search_parser.add_argument("--topic", help="Future topic filter.")
    search_parser.add_argument("--type", dest="knowledge_type", help="Future knowledge type filter.")
    search_parser.add_argument("--budget", type=int, help="Future token budget.")
    search_parser.add_argument("--json", action="store_true", help="Future JSON output.")

    context_parser = _placeholder_parser(subcommands, "context", "Return task-shaped context.")
    context_parser.add_argument("task", nargs="?", help="Future task description.")
    context_parser.add_argument("--mode", help="Future context mode.")
    context_parser.add_argument("--budget", type=int, help="Future token budget.")
    context_parser.add_argument("--json", action="store_true", help="Future JSON output.")

    get_parser = _placeholder_parser(subcommands, "get", "Inspect one note.")
    get_parser.add_argument("id", nargs="?", help="Future note ID.")
    get_parser.add_argument("--summary", action="store_true", help="Future summary-only output.")

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


if __name__ == "__main__":
    raise SystemExit(main())
