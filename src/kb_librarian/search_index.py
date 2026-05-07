"""Local lexical index build and query helpers."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Mapping

from kb_librarian.atomic import atomic_replace_path
from kb_librarian.errors import SearchIndexError

TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")

INDEX_FIELDS = (
    "id",
    "title",
    "summary",
    "topic",
    "knowledge_type",
    "tags",
    "retrieval_phrases",
    "agent_use",
    "applies_when",
    "does_not_apply_when",
    "body",
    "status",
    "confidence",
    "updated",
)

SEARCHABLE_FIELDS = ("title", "summary", "tags", "retrieval_phrases", "body")


def tokenize_query(text: str) -> list[str]:
    tokens = [token.lower() for token in TOKEN_PATTERN.findall(text.lower())]
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def build_lexical_index(db_path: Path, documents: list[Mapping[str, str]]) -> str:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{db_path.name}.",
        suffix=".tmp",
        dir=db_path.parent,
    )
    os.close(fd)
    Path(temp_name).unlink()
    # sqlite3 creates the database file itself; mkstemp only reserves a same-directory path.
    temp_path = Path(temp_name)

    conn = sqlite3.connect(temp_path)
    try:
        conn.execute(
            """
            CREATE TABLE notes (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                topic TEXT NOT NULL,
                knowledge_type TEXT NOT NULL,
                tags TEXT NOT NULL,
                retrieval_phrases TEXT NOT NULL,
                agent_use TEXT NOT NULL,
                applies_when TEXT NOT NULL,
                does_not_apply_when TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                updated TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )

        conn.executemany(
            """
            INSERT INTO notes (
                id, path, title, summary, topic, knowledge_type, tags, retrieval_phrases,
                agent_use, applies_when, does_not_apply_when, body, status, confidence, updated
            ) VALUES (
                :id, :path, :title, :summary, :topic, :knowledge_type, :tags, :retrieval_phrases,
                :agent_use, :applies_when, :does_not_apply_when, :body, :status, :confidence, :updated
            )
            """,
            documents,
        )

        backend = "plain"
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE notes_fts USING fts5(
                    id,
                    title,
                    summary,
                    topic,
                    knowledge_type,
                    tags,
                    retrieval_phrases,
                    agent_use,
                    applies_when,
                    does_not_apply_when,
                    body,
                    status,
                    confidence,
                    updated,
                    content='notes',
                    content_rowid='rowid'
                )
                """
            )
            conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')")
            backend = "fts5"
        except sqlite3.OperationalError:
            backend = "plain"

        conn.execute("INSERT INTO metadata(key, value) VALUES ('backend', ?)", (backend,))
        conn.commit()
        conn.close()
        atomic_replace_path(temp_path, db_path)
        return backend
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        if temp_path.exists():
            temp_path.unlink()


def load_backend(db_path: Path) -> str:
    if not db_path.exists():
        raise SearchIndexError(f"Search index not found at {db_path}. Run `kb reindex`.")

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'backend'").fetchone()
        if not row:
            return "plain"
        return str(row[0])
    except sqlite3.Error as exc:
        raise SearchIndexError(f"Invalid search index at {db_path}: {exc}") from exc
    finally:
        conn.close()


def query_candidates(
    db_path: Path,
    *,
    query: str,
    topic: str | None = None,
    knowledge_type: str | None = None,
) -> list[dict[str, str]]:
    tokens = tokenize_query(query)
    if not tokens:
        return []

    backend = load_backend(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if backend == "fts5":
            rows = _query_fts(conn, tokens, topic=topic, knowledge_type=knowledge_type)
        else:
            rows = _query_plain(conn, topic=topic, knowledge_type=knowledge_type)
        return [_row_to_document(row) for row in rows]
    finally:
        conn.close()


def score_document(
    document: Mapping[str, str],
    *,
    query: str,
    tokens: list[str],
    weights: Mapping[str, float],
) -> float:
    title_weight = float(weights.get("title_weight", 5))
    summary_weight = float(weights.get("summary_weight", 4))
    retrieval_phrase_weight = float(weights.get("retrieval_phrase_weight", 4))
    tag_weight = float(weights.get("tag_weight", 3))
    body_weight = float(weights.get("body_weight", 1))

    full_query = query.strip().lower()
    title = document["title"].lower()
    summary = document["summary"].lower()
    tags = document["tags"].lower()
    retrieval_phrases = document["retrieval_phrases"].lower()
    body = document["body"].lower()
    note_id = document["id"].lower()

    score = 0.0
    if full_query and full_query == note_id:
        score += 10_000.0
    if full_query and full_query in title:
        score += 600.0

    for token in tokens:
        score += title.count(token) * title_weight
        score += summary.count(token) * summary_weight
        score += retrieval_phrases.count(token) * retrieval_phrase_weight
        score += tags.count(token) * tag_weight
        score += body.count(token) * body_weight
    return score


def _query_fts(
    conn: sqlite3.Connection,
    tokens: list[str],
    *,
    topic: str | None,
    knowledge_type: str | None,
) -> list[sqlite3.Row]:
    query = " OR ".join(_escape_fts_token(token) for token in tokens)
    return conn.execute(
        """
        SELECT n.*
        FROM notes AS n
        JOIN notes_fts ON notes_fts.rowid = n.rowid
        WHERE notes_fts MATCH ?
          AND (? IS NULL OR n.topic = ?)
          AND (? IS NULL OR n.knowledge_type = ?)
        LIMIT 500
        """,
        (query, topic, topic, knowledge_type, knowledge_type),
    ).fetchall()


def _query_plain(
    conn: sqlite3.Connection,
    *,
    topic: str | None,
    knowledge_type: str | None,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM notes
        WHERE (? IS NULL OR topic = ?)
          AND (? IS NULL OR knowledge_type = ?)
        """,
        (topic, topic, knowledge_type, knowledge_type),
    ).fetchall()


def _escape_fts_token(token: str) -> str:
    escaped = token.replace('"', '""')
    return f'"{escaped}"'


def _row_to_document(row: sqlite3.Row) -> dict[str, str]:
    document: dict[str, str] = {}
    for key in row.keys():
        value = row[key]
        document[key] = "" if value is None else str(value)
    return document
