from __future__ import annotations

import routes


def test_normalize_suggestion_applies_defaults() -> None:
    result = routes._normalize_suggestion(  # noqa: SLF001
        {"type": "not-a-real-type", "preview": "Track p99 latency during rollout", "detail_hint": ""}
    )

    assert result == {
        "type": "QUESTION",
        "preview": "Track p99 latency during rollout",
        "detail_hint": "Explain why this matters now and what to say next in the meeting.",
    }


def test_extract_unique_suggestions_filters_duplicates_and_blocked() -> None:
    payload = {
        "suggestions": [
            {"type": "QUESTION", "preview": "Ask about queue depth", "detail_hint": "a"},
            {"type": "QUESTION", "preview": "Ask about queue depth", "detail_hint": "b"},
            {"type": "ANSWER", "preview": "Share the rollback plan", "detail_hint": "c"},
        ]
    }

    result = routes._extract_unique_suggestions(payload, {"share the rollback plan"})  # noqa: SLF001

    assert result == [
        {"type": "QUESTION", "preview": "Ask about queue depth", "detail_hint": "a"},
    ]


def test_quality_issues_flags_weak_batches() -> None:
    suggestions = [
        {"type": "QUESTION", "preview": "Ask for more details", "detail_hint": "a"},
        {"type": "QUESTION", "preview": "Ask for more details", "detail_hint": "b"},
    ]

    issues = routes._quality_issues(suggestions)  # noqa: SLF001

    assert "not_exactly_three" in issues
    assert "duplicate_preview" in issues
    assert "low_type_diversity" in issues
    assert "too_generic" in issues


def test_quality_issues_flags_teaser_preview_and_thin_detail() -> None:
    suggestions = [
        {"type": "QUESTION", "preview": "Ask about the rollback timing", "detail_hint": "Talk about rollback timing."},
        {"type": "ANSWER", "preview": "The queue spike came after the deploy, not before.", "detail_hint": "Explain the timeline using the deploy and queue depth change, then say what signal confirms causality."},
        {"type": "FACT_CHECK", "preview": "The outage was not caused by raw traffic growth alone.", "detail_hint": "Separate the traffic claim from the config regression and point to the evidence that makes the config issue more likely."},
    ]

    issues = routes._quality_issues(suggestions)  # noqa: SLF001

    assert "preview_not_standalone" in issues
    assert "detail_hint_not_additive" in issues


def test_fallback_suggestions_follow_guardrail_context() -> None:
    suggestions = routes._fallback_suggestions(  # noqa: SLF001
        [
            "Guardrails sanitize the input coming into the agent and the output coming out.",
            "This helps defend against prompt injection and PII requests.",
        ],
        set(),
        3,
    )

    previews = [s["preview"] for s in suggestions]
    assert any("Guardrails" in preview or "guardrails" in preview for preview in previews)
    assert not any("bottleneck" in preview.lower() for preview in previews)


def test_trim_chat_messages_enforces_count_and_char_budget() -> None:
    messages = [
        {"role": "user", "content": "alpha"},
        {"role": "assistant", "content": "bravo"},
        {"role": "user", "content": "charlie"},
        {"role": "assistant", "content": "delta"},
    ]

    trimmed = routes._trim_chat_messages(messages, max_messages=3, max_chars=11)  # noqa: SLF001

    assert trimmed == [
        {"role": "assistant", "content": "delta"},
    ]
