"""
routes.py — All three API route handlers.

Endpoints:
  POST /api/transcribe  — multipart audio → Whisper Large V3 → { text }
  POST /api/suggest     — transcript lines → 3 suggestion cards (JSON mode)
  POST /api/chat        — streaming chat with full transcript context (SSE)
"""

import json
import os
from dataclasses import replace

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from groq_client import (
    make_client,
    transcribe_audio,
    generate_suggestions,
    stream_chat_completion,
)
from models import ChatRequest, SuggestRequest, SuggestResponse
from prompts import DEFAULT_SETTINGS, Settings

router = APIRouter(prefix="/api")
ALLOWED_TYPES = {"ANSWER", "FACT_CHECK", "QUESTION", "TALKING_POINT"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_settings(override) -> Settings:
    """Merge per-request overrides on top of DEFAULT_SETTINGS."""
    updates = {k: v for k, v in override.model_dump().items() if v is not None}
    return replace(DEFAULT_SETTINGS, **updates)


def _get_api_key(request_key: str) -> str:
    """Use request-supplied key; fall back to .env GROQ_API_KEY."""
    key = request_key or os.getenv("GROQ_API_KEY", "")
    if not key:
        raise HTTPException(status_code=400, detail="Groq API key is required")
    return key


def _derive_context_signals(new_lines: list[str]) -> str:
    """Extract lightweight signals from new lines to guide suggestion mix."""
    joined = " ".join(new_lines).lower()
    has_question = "?" in joined
    has_numbers = any(ch.isdigit() for ch in joined)
    claim_markers = ["is", "are", "caused", "due to", "always", "never", "outage", "incident", "percent", "%"]
    has_claim = any(tok in joined for tok in claim_markers)
    tech_markers = ["latency", "p99", "websocket", "shard", "memory", "cpu", "throughput", "queue"]
    has_tech = any(tok in joined for tok in tech_markers)

    signals = [
        f"- question_detected: {'yes' if has_question else 'no'}",
        f"- claim_detected: {'yes' if has_claim else 'no'}",
        f"- numbers_or_metrics_detected: {'yes' if has_numbers else 'no'}",
        f"- technical_topic_detected: {'yes' if has_tech else 'no'}",
    ]
    return "\n".join(signals)


def _derive_mix_policy(new_lines: list[str]) -> str:
    """Recommend a type mix based on what was just said."""
    joined = " ".join(new_lines).lower()
    has_question = "?" in joined
    has_claim = any(tok in joined for tok in ["outage", "caused", "claim", "percent", "always", "never"])

    if has_question and has_claim:
        return "Prefer QUESTION + ANSWER + FACT_CHECK."
    if has_question:
        return "Prefer QUESTION + ANSWER + TALKING_POINT."
    if has_claim:
        return "Prefer FACT_CHECK + TALKING_POINT + QUESTION."
    return "Prefer QUESTION + TALKING_POINT + ANSWER."


def _normalize_suggestion(raw: dict) -> dict | None:
    """Coerce model output into the expected suggestion card shape."""
    if not isinstance(raw, dict):
        return None
    t = str(raw.get("type", "")).strip().upper()
    if t not in ALLOWED_TYPES:
        t = "QUESTION"
    preview = str(raw.get("preview", "")).strip()
    detail_hint = str(raw.get("detail_hint", "")).strip()
    if not preview:
        return None
    if not detail_hint:
        detail_hint = "Explain why this matters now and what to say next in the meeting."
    return {"type": t, "preview": preview, "detail_hint": detail_hint}


def _extract_unique_suggestions(payload: dict, blocked_previews: set[str]) -> list[dict]:
    """Parse suggestions and drop duplicates/blocked previews."""
    out: list[dict] = []
    seen = set(blocked_previews)
    for raw in payload.get("suggestions", []):
        s = _normalize_suggestion(raw)
        if not s:
            continue
        key = s["preview"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _fallback_suggestions(new_lines: list[str], blocked_previews: set[str], needed: int) -> list[dict]:
    """
    Deterministic fallback to guarantee exactly 3 cards when model under-returns.
    These still provide useful, clickable value.
    """
    topic = " ".join(new_lines).strip()
    if len(topic) > 160:
        topic = topic[:157] + "..."
    if not topic:
        topic = "the latest discussion point"

    templates = [
        {
            "type": "QUESTION",
            "preview": "What metric is currently the bottleneck in this discussion?",
            "detail_hint": "Ask for one concrete metric (for example p99 latency, error rate, or queue depth) and the current value. This quickly reveals whether the team is blocked by capacity, contention, or configuration.",
        },
        {
            "type": "TALKING_POINT",
            "preview": "Propose a 2-step plan: isolate the hotspot, then validate with a controlled rollout.",
            "detail_hint": "Frame a practical plan the team can execute today: identify the dominant hotspot with profiling, then verify improvements behind a staged rollout. This keeps progress measurable and reduces risk.",
        },
        {
            "type": "ANSWER",
            "preview": "Direct answer: prioritize the highest-impact bottleneck before adding architecture complexity.",
            "detail_hint": f"Give a concrete answer tied to this context: \"{topic}\". Name the likely primary bottleneck and one specific mitigation to test first, then explain why it should move the metric.",
        },
        {
            "type": "FACT_CHECK",
            "preview": "Fact-check whether this issue is truly capacity-related or caused by configuration/process.",
            "detail_hint": "Separate what is known from what is assumed. Compare recent symptoms with known failure modes (capacity saturation vs misconfiguration) to prevent solving the wrong problem.",
        },
    ]

    out: list[dict] = []
    seen = set(blocked_previews)
    for t in templates:
        key = t["preview"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= needed:
            break
    return out


def _trim_chat_messages(messages: list[dict], max_messages: int, max_chars: int) -> list[dict]:
    """
    Keep the most recent chat turns within message-count and character budgets.
    Prevents oversized follow-up requests from failing on provider limits.
    """
    if max_messages > 0 and len(messages) > max_messages:
        messages = messages[-max_messages:]

    if max_chars <= 0:
        return messages

    kept: list[dict] = []
    used = 0
    for m in reversed(messages):
        content = str(m.get("content", ""))
        if used + len(content) > max_chars and kept:
            break
        used += len(content)
        kept.append(m)
    return list(reversed(kept))


# ── POST /api/transcribe ──────────────────────────────────────────────────────

@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    api_key: str = Form(default=""),
):
    """
    Accept a multipart audio chunk.
    Returns { "text": "..." } with the Whisper transcript.
    """
    key = _get_api_key(api_key)

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    try:
        client = make_client(key)
        text = await transcribe_audio(
            client,
            audio_bytes,
            filename=audio.filename or "audio.webm",
            mime_type=audio.content_type or "audio/webm",
        )
        return {"text": text}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groq error: {exc}") from exc


# ── POST /api/suggest ─────────────────────────────────────────────────────────

@router.post("/suggest", response_model=SuggestResponse)
async def suggest(req: SuggestRequest):
    """
    Accept recent transcript lines, return exactly 3 suggestion cards.

    Each card has:
      type        — ANSWER | FACT_CHECK | QUESTION | TALKING_POINT
      preview     — punchy one-liner, useful on its own
      detail_hint — 2-3 sentences of richer context used when card is clicked
    """
    key = _get_api_key(req.api_key)
    cfg = _resolve_settings(req.settings)

    if not req.transcript_lines:
        raise HTTPException(status_code=400, detail="No transcript lines provided")

    recent = req.transcript_lines[-cfg.suggestion_context_lines:]
    new_count = max(1, min(cfg.suggestion_new_lines, len(recent)))
    context_lines = recent[:-new_count]
    new_lines = recent[-new_count:]

    # Fill placeholders for both the current prompt template and any legacy
    # user overrides saved in localStorage.
    user_prompt = cfg.suggestion_user_prompt.format(
        context_count=len(context_lines),
        context_transcript="\n".join(context_lines) if context_lines else "(none)",
        new_count=len(new_lines),
        new_transcript="\n".join(new_lines) if new_lines else "(none)",
        context_signals=_derive_context_signals(new_lines),
        mix_policy=_derive_mix_policy(new_lines),
        context_lines=cfg.suggestion_context_lines,
        transcript="\n".join(recent),
    )

    # Append previous suggestion previews so the model avoids repeating them
    system_prompt = cfg.suggestion_system_prompt
    previous_preview_keys: set[str] = set()
    if req.previous_suggestions:
        flat = [s for batch in req.previous_suggestions for s in batch]
        # Keep prompt lean for long sessions to reduce JSON-mode failures.
        flat_recent = flat[-24:]
        prev_text = "\n".join(f"- {s.get('preview', '')}" for s in flat_recent if s.get('preview'))
        previous_preview_keys = {str(s.get("preview", "")).strip().lower() for s in flat if s.get("preview")}
        if prev_text:
            system_prompt += f"\n\nPREVIOUS SUGGESTIONS (do not repeat these):\n{prev_text}"

    try:
        client = make_client(key)
        result = await generate_suggestions(client, system_prompt, user_prompt, cfg.suggestion_model)

        suggestions = _extract_unique_suggestions(result, previous_preview_keys)

        # If model returned fewer than 3, ask once more for only the missing cards.
        if len(suggestions) < 3:
            missing = 3 - len(suggestions)
            avoid = "\n".join(f"- {s['preview']}" for s in suggestions)
            retry_prompt = (
                f"{user_prompt}\n\n"
                f"You must return exactly {missing} additional suggestions only.\n"
                "Do not repeat any preview from previous suggestions or this avoid-list:\n"
                f"{avoid if avoid else '(none)'}"
            )
            retry = await generate_suggestions(client, system_prompt, retry_prompt, cfg.suggestion_model)
            retry_cards = _extract_unique_suggestions(
                retry,
                previous_preview_keys | {s['preview'].strip().lower() for s in suggestions},
            )
            suggestions.extend(retry_cards)

        # Deterministic fallback: always return exactly 3 cards.
        if len(suggestions) < 3:
            blocked = previous_preview_keys | {s['preview'].strip().lower() for s in suggestions}
            suggestions.extend(_fallback_suggestions(new_lines, blocked, 3 - len(suggestions)))
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groq error: {exc}") from exc

    suggestions = suggestions[:3]
    if not suggestions:
        raise HTTPException(status_code=502, detail="Model returned no suggestions")

    return {"suggestions": suggestions}


# ── POST /api/chat ────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(req: ChatRequest):
    """
    Stream a chat completion as Server-Sent Events.

    The transcript is prepended once as a synthetic first exchange so the model
    has full session context without us repeating it on every turn.

    SSE format:
      data: {"delta": "..."}   — token chunk
      data: [DONE]             — stream complete
      data: {"error": "..."}   — on failure
    """
    key = _get_api_key(req.api_key)
    cfg = _resolve_settings(req.settings)

    if not req.messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    messages = [m.model_dump() for m in req.messages]
    messages = _trim_chat_messages(
        messages,
        max_messages=max(2, int(cfg.chat_history_messages)),
        max_chars=max(2000, int(cfg.chat_history_chars)),
    )
    recent_lines = req.transcript_lines[-cfg.chat_context_lines:]

    # Inject the transcript once at the top of the conversation as a
    # synthetic user→assistant exchange. Check if already injected by
    # looking at the very first message we'd have added previously.
    if recent_lines:
        transcript_block = cfg.chat_context_injection.format(
            line_count=len(recent_lines),
            transcript="\n".join(recent_lines),
        )
        # Always prepend fresh — the frontend sends the full apiMessages list
        # each time, so we just add the transcript header in front.
        # We do NOT check for "already injected" here because the frontend
        # never puts the transcript block in apiMessages itself.
        messages = [
            {"role": "user",      "content": transcript_block},
            {"role": "assistant", "content": "Understood. I have the full session transcript. Ask me anything."},
            *messages,
        ]

    async def event_stream():
        try:
            client = make_client(key)
            async for delta in stream_chat_completion(
                client, messages, cfg.chat_model, cfg.chat_system_prompt
            ):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── GET /api/health ───────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"ok": True}
