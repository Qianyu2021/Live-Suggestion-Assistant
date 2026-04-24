from __future__ import annotations

from collections.abc import AsyncGenerator

from groq import APITimeoutError

import routes


def test_transcribe_rejects_empty_audio(client) -> None:
    response = client.post(
        "/api/transcribe",
        data={"api_key": "test-key"},
        files={"audio": ("empty.webm", b"", "audio/webm")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Empty audio file"


def test_transcribe_maps_provider_timeout_to_504(client, monkeypatch) -> None:
    async def fake_transcribe_audio(*args, **kwargs) -> str:
        raise APITimeoutError(request=None)

    monkeypatch.setattr(routes, "transcribe_audio", fake_transcribe_audio)

    response = client.post(
        "/api/transcribe",
        data={"api_key": "test-key"},
        files={"audio": ("sample.webm", b"123", "audio/webm")},
    )

    assert response.status_code == 504
    assert response.json()["detail"] == "Groq request timed out."


def test_suggest_returns_fallback_cards_when_model_under_returns(client, monkeypatch) -> None:
    async def fake_generate_suggestions(*args, **kwargs) -> dict:
        return {
            "suggestions": [
                {
                    "type": "QUESTION",
                    "preview": "What metric is currently the bottleneck?",
                    "detail_hint": "Ask for the current number.",
                }
            ]
        }

    monkeypatch.setattr(routes, "generate_suggestions", fake_generate_suggestions)

    response = client.post(
        "/api/suggest",
        json={
            "api_key": "test-key",
            "transcript_lines": ["We are seeing a spike in p99 latency after the rollout."],
            "previous_suggestions": [],
            "settings": {
                "suggestion_agentic_enabled": False,
                "suggestion_repair_enabled": False,
            },
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert len(body["suggestions"]) == 3


def test_suggest_rejects_unknown_prompt_placeholder(client) -> None:
    response = client.post(
        "/api/suggest",
        json={
            "api_key": "test-key",
            "transcript_lines": ["We should compare CPU saturation against queue depth."],
            "settings": {
                "suggestion_user_prompt": "Unknown: {missing_placeholder}",
                "suggestion_agentic_enabled": False,
                "suggestion_repair_enabled": False,
            },
        },
    )

    assert response.status_code == 400
    assert "unknown placeholder" in response.json()["detail"]


def test_chat_streams_sse_chunks(client, monkeypatch) -> None:
    async def fake_stream_chat_completion(*args, **kwargs) -> AsyncGenerator[str, None]:
        yield "hello"
        yield " world"

    monkeypatch.setattr(routes, "stream_chat_completion", fake_stream_chat_completion)

    response = client.post(
        "/api/chat",
        json={
            "api_key": "test-key",
            "messages": [{"role": "user", "content": "What should I say next?"}],
            "transcript_lines": ["The customer asked whether the rollout risk is contained."],
            "settings": {"chat_agentic_enabled": False},
        },
    )

    assert response.status_code == 200
    assert 'data: {"delta": "hello"}' in response.text
    assert 'data: {"delta": " world"}' in response.text
    assert "data: [DONE]" in response.text


def test_chat_streams_structured_errors(client, monkeypatch) -> None:
    async def fake_stream_chat_completion(*args, **kwargs) -> AsyncGenerator[str, None]:
        raise APITimeoutError(request=None)
        yield ""

    monkeypatch.setattr(routes, "stream_chat_completion", fake_stream_chat_completion)

    response = client.post(
        "/api/chat",
        json={
            "api_key": "test-key",
            "messages": [{"role": "user", "content": "What should I say next?"}],
            "transcript_lines": ["The team is blocked on shard rebalancing."],
            "settings": {"chat_agentic_enabled": False},
        },
    )

    assert response.status_code == 200
    assert '"error": "Groq request timed out."' in response.text
    assert '"status_code": 504' in response.text
