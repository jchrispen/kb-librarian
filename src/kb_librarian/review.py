"""Review queue summaries for Phase 01d."""

from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


ITEM_RE = re.compile(r"^##\s+item:\s+(.+?)\s*$", flags=re.MULTILINE)


@dataclass(frozen=True)
class ReviewItem:
    kind: str
    item_id: str
    summary: str


def collect_review_summary(
    data_dir: Path,
    *,
    max_items: int,
) -> tuple[dict[str, int], list[ReviewItem]]:
    classification = _parse_markdown_items(
        data_dir / "review" / "pending-classification.md",
        kind="classification",
        summary_patterns=(r"^-\s+title:\s+(.+)$", r"^Reason:\s+(.+)$"),
    )
    merge = _parse_markdown_items(
        data_dir / "review" / "pending-merge.md",
        kind="merge",
        summary_patterns=(r"^-\s+candidate_title:\s+(.+)$", r"^Rationale:\s+(.+)$"),
    )
    disputes = _parse_markdown_items(
        data_dir / "review" / "disputes.md",
        kind="dispute",
        summary_patterns=(r"^-\s+candidate_title:\s+(.+)$", r"^Rationale:\s+(.+)$"),
    )
    duplicates = _load_duplicate_items(data_dir)
    unsupported = _load_unsupported_items(data_dir)

    counts = {
        "classification": len(classification),
        "merge": len(merge),
        "dispute": len(disputes),
        "duplicate": len(duplicates),
        "unsupported_file": len(unsupported),
    }

    ordered = [
        *disputes,
        *merge,
        *classification,
        *duplicates,
        *unsupported,
    ]
    if max_items > 0:
        ordered = ordered[:max_items]
    return counts, ordered


def render_review_summary(
    counts: Mapping[str, int],
    items: Iterable[ReviewItem],
    *,
    max_items: int,
) -> str:
    total = int(sum(counts.values()))
    lines = [
        f"Review items: {total}",
        "",
        f"classification: {counts.get('classification', 0)}",
        f"merge: {counts.get('merge', 0)}",
        f"dispute: {counts.get('dispute', 0)}",
        f"duplicate: {counts.get('duplicate', 0)}",
        f"unsupported_file: {counts.get('unsupported_file', 0)}",
    ]

    materialized = list(items)
    if materialized:
        lines.extend(["", f"Showing up to {max_items} items:"])
        for item in materialized:
            lines.append(f"- [{item.kind}] {item.item_id} - {item.summary}")
    return "\n".join(lines) + "\n"


def _parse_markdown_items(path: Path, *, kind: str, summary_patterns: tuple[str, ...]) -> list[ReviewItem]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")

    matches = list(ITEM_RE.finditer(text))
    if not matches:
        return []

    items: list[ReviewItem] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end]
        item_id = match.group(1).strip()
        summary = _summary_from_block(block, summary_patterns)
        items.append(ReviewItem(kind=kind, item_id=item_id, summary=summary))

    items.reverse()
    return items


def _summary_from_block(block: str, patterns: tuple[str, ...]) -> str:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    for pattern in patterns:
        regex = re.compile(pattern)
        for line in lines:
            captured = regex.match(line)
            if captured:
                value = captured.group(1).strip()
                if value:
                    return value
    for line in lines:
        if not line.startswith("```"):
            return line
    return "(no summary)"


def _load_duplicate_items(data_dir: Path) -> list[ReviewItem]:
    path = data_dir / ".kb" / "ingested.json"
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []

    items: list[ReviewItem] = []
    for entry in loaded:
        if not isinstance(entry, dict) or entry.get("status") != "duplicate":
            continue
        source_name = str(entry.get("source_name", "unknown"))
        stamp = str(entry.get("processed_at", "unknown")).replace(":", "").replace("-", "")
        digest = str(entry.get("hash", ""))[:8]
        item_id = f"duplicate-{stamp}-{digest}".strip("-")
        items.append(ReviewItem(kind="duplicate", item_id=item_id, summary=source_name))

    items.reverse()
    return items


def _load_unsupported_items(data_dir: Path) -> list[ReviewItem]:
    path = data_dir / ".kb" / "errors.log"
    if not path.exists():
        return []
    items: list[ReviewItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        marker = "Unsupported ingest file extension for "
        if marker not in line:
            continue
        source = line.split(marker, maxsplit=1)[-1].strip()
        digest = hashlib.sha256(line.encode("utf-8")).hexdigest()[:8]
        item_id = f"unsupported-{digest}"
        items.append(ReviewItem(kind="unsupported_file", item_id=item_id, summary=source))

    items.reverse()
    return items
