"""Retrieval candidate source seams.

Embedding retrieval is intentionally unsupported for now; this module makes the
active lexical source explicit so future sources can be composed without changing
CLI contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from kb_librarian.errors import KBLibrarianError
from kb_librarian.search_index import query_candidates, score_document, tokenize_query


@dataclass(frozen=True)
class CandidateQuery:
    text: str
    topic: str | None = None
    knowledge_type: str | None = None


class CandidateSource(Protocol):
    name: str

    def candidates(self, query: CandidateQuery) -> list[dict[str, str]]:
        """Return raw candidate documents for a retrieval query."""


class CandidateRanker(Protocol):
    def rank(self, candidates: list[dict[str, str]], *, query: str) -> list[dict[str, object]]:
        """Return scored candidates in descending relevance order."""


@dataclass(frozen=True)
class LexicalCandidateSource:
    db_path: Path
    name: str = "lexical"

    def candidates(self, query: CandidateQuery) -> list[dict[str, str]]:
        return query_candidates(
            self.db_path,
            query=query.text,
            topic=query.topic,
            knowledge_type=query.knowledge_type,
        )


@dataclass(frozen=True)
class LexicalRanker:
    weights: Mapping[str, Any]

    def rank(self, candidates: list[dict[str, str]], *, query: str) -> list[dict[str, object]]:
        tokens = tokenize_query(query)
        scored: list[dict[str, object]] = []
        for candidate in candidates:
            score = score_document(candidate, query=query, tokens=tokens, weights=self.weights)
            if score <= 0:
                continue
            scored.append({**candidate, "score": score})
        scored.sort(
            key=lambda item: (
                -float(item["score"]),
                str(item.get("updated", "")),
                str(item.get("id", "")),
            )
        )
        return scored


def candidate_source_from_config(data_dir: Path, config: Mapping[str, Any]) -> CandidateSource:
    retrieval = config.get("retrieval")
    if not isinstance(retrieval, Mapping):
        raise KBLibrarianError("Config section retrieval must be a mapping.")
    if retrieval.get("lexical_index") is not True:
        raise KBLibrarianError("No supported retrieval source is enabled; set retrieval.lexical_index: true.")
    return LexicalCandidateSource(data_dir / ".kb" / "fts.sqlite")


def ranker_from_config(config: Mapping[str, Any]) -> CandidateRanker:
    retrieval = config.get("retrieval")
    if not isinstance(retrieval, Mapping):
        raise KBLibrarianError("Config section retrieval must be a mapping.")
    return LexicalRanker(retrieval)


def embedding_seam_status(config: Mapping[str, Any]) -> dict[str, object]:
    retrieval = config.get("retrieval")
    if not isinstance(retrieval, Mapping):
        return {"configured": False, "enabled": False, "supported": False}
    configured = any(
        retrieval.get(key) not in (None, "")
        for key in ("embedding_provider", "embedding_model", "embedding_dimensions")
    )
    return {
        "configured": configured,
        "enabled": bool(retrieval.get("embeddings")),
        "supported": False,
        "index_path": retrieval.get("embedding_index_path"),
    }
