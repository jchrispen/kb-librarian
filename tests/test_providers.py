from __future__ import annotations

from pathlib import Path

import pytest

from kb_librarian.errors import ProviderError
from kb_librarian.providers import (
    MockProvider,
    validate_integration_payload,
    parse_json_response,
    validate_classification_payload,
    validate_extraction_payload,
)


def test_parse_json_response_accepts_json_fence():
    payload = parse_json_response('```json\n{"candidates": []}\n```')

    assert payload == {"candidates": []}


def test_validate_extraction_payload_builds_candidate():
    result = validate_extraction_payload(
        {
            "candidates": [
                {
                    "title": "Use task-shaped context",
                    "summary": "Agents benefit from compact context for the current task.",
                    "knowledge_type": "heuristic",
                    "body": "## Judgment\n\nUse task-shaped context.\n",
                    "retrieval_phrases": ["task-shaped context"],
                    "tags": ["agent-context"],
                    "confidence": "high",
                    "utility_score": "high",
                }
            ]
        }
    )

    assert result.candidates[0].title == "Use task-shaped context"
    assert result.candidates[0].knowledge_type == "heuristic"


def test_validate_extraction_payload_rejects_invalid_candidate_type():
    with pytest.raises(ProviderError, match="knowledge_type"):
        validate_extraction_payload(
            {
                "candidates": [
                    {
                        "title": "Bad",
                        "summary": "Bad candidate.",
                        "knowledge_type": "article",
                        "body": "Body",
                        "retrieval_phrases": [],
                        "tags": [],
                        "confidence": "high",
                        "utility_score": "high",
                    }
                ]
            }
        )


def test_validate_classification_payload():
    result = validate_classification_payload(
        {
            "topic": "agent-systems",
            "knowledge_type": "technique",
            "confidence": "medium",
            "reason": "fits agent systems",
        }
    )

    assert result.topic == "agent-systems"
    assert result.knowledge_type == "technique"


def test_mock_provider_is_deterministic():
    provider = MockProvider()

    extraction = provider.extract_candidates(
        text="# CLI context\n\nPrefer task-shaped context for coding agents.",
        source_path=Path(__file__),
        max_notes=7,
        model="mock",
    )
    classification = provider.classify_candidate(
        candidate=extraction.candidates[0],
        text="# CLI context\n\nPrefer task-shaped context for coding agents.",
        source_path=Path(__file__),
        model="mock",
    )

    assert extraction.candidates[0].title == "CLI context"
    assert classification.topic == "agent-systems"
    assert classification.confidence == "high"


def test_mock_provider_synthesize_context_is_grounded():
    provider = MockProvider()

    result = provider.synthesize_context(
        task="review this architecture",
        mode="architecture",
        budget=1800,
        selected_notes=[
            {
                "note_id": "2026-05-04-agent-context-cli-contract",
                "title": "Use a CLI as the stable contract",
                "summary": "A stable CLI lets multiple agents access the same artifact.",
                "trust_flags": [],
            }
        ],
    )

    assert "## Directly relevant techniques" in result
    assert "[2026-05-04-agent-context-cli-contract]" in result
    assert "## Suggested agent behavior" in result


def test_mock_provider_synthesize_exploration_has_explore_sections():
    provider = MockProvider()

    result = provider.synthesize_exploration(
        problem="reduce token burn while preserving agent access",
        budget=3000,
        selected_notes=[
            {
                "note_id": "2026-05-04-token-budget-pattern",
                "title": "Token budget pattern",
                "summary": "Use retrieval budgets to preserve useful agent access.",
                "trust_flags": ["confidence:low"],
            }
        ],
    )

    assert "## Directly relevant concepts" in result
    assert "## Adjacent patterns" in result
    assert "## Tensions / tradeoffs" in result
    assert "## Open questions" in result
    assert "[2026-05-04-token-budget-pattern]" in result


def test_validate_integration_payload():
    result = validate_integration_payload(
        {
            "verdict": "identical",
            "target_note_ids": ["2026-05-04-agent-context-cli-contract"],
            "rationale": "same idea",
        }
    )

    assert result.verdict == "identical"
    assert result.target_note_ids == ["2026-05-04-agent-context-cli-contract"]


def test_validate_integration_payload_rejects_invalid_targets():
    with pytest.raises(ProviderError, match="requires at least one target note ID"):
        validate_integration_payload(
            {
                "verdict": "adds_nuance",
                "target_note_ids": [],
                "rationale": "missing target",
            }
        )
