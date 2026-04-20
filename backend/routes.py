"""
routes.py — All three API route handlers in one file.

Endpoints:
  POST /api/transcribe  — multipart audio → Whisper Large V3 → { text }
  POST /api/suggest     — transcript lines → 3 suggestion cards
  POST /api/chat        — streaming chat with full transcript context (SSE)
"""

import json
import os
from dataclasses import replace

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.groq_client import (
    make_client,
    transcribe_audio,
    generate_suggestions,
    stream_chat_completion,
)
from backend.models import ChatRequest, SuggestRequest, SuggestResponse
from backend.prompts import DEFAULT_SETTINGS, Settings

router = APIRouter(prefix="/api")


# ── Helper ────────────────────────────────────────────────────────────────────

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


# ── POST /api/transcribe ──────────────────────────────────────────────────────

@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    api_key: str = Form(default=""),
):
    """
    Accept a multipart audio file upload.
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
        # Surface Groq API errors clearly
        raise HTTPException(status_code=502, detail=f"Groq error: {exc}") from exc


# ── POST /api/suggest ─────────────────────────────────────────────────────────

@router.post("/suggest", response_model=SuggestResponse)
async def suggest(req: SuggestRequest):
    """
    Accept recent transcript lines and return exactly 3 suggestion cards.
    """
    key = _get_api_key(req.api_key)
    cfg = _resolve_settings(req.settings)

    if not req.transcript_lines:
        raise HTTPException(status_code=400, detail="No transcript lines provided")

    # Slice to the configured context window
    recent = req.transcript_lines[-cfg.suggestion_context_lines:]
    transcript = "\n".join(recent)

    # Build user prompt
    user_prompt = cfg.suggestion_user_prompt.format(
        context_lines=cfg.suggestion_context_lines,
        transcript=transcript,
    )

    # Append previous suggestions to system prompt to avoid repeats
    system_prompt = cfg.suggestion_system_prompt
    if req.previous_suggestions:
        flat = [s for batch in req.previous_suggestions for s in batch]
        prev_text = "\n".join(f"- {s.get('preview', '')}" for s in flat)
        system_prompt += f"\n\nPREVIOUS SUGGESTIONS (do not repeat):\n{prev_text}"

    try:
        client = make_client(key)
        result = await generate_suggestions(client, system_prompt, user_prompt, cfg.suggestion_model)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groq error: {exc}") from exc

    suggestions = result.get("suggestions", [])[:3]
    if not suggestions:
        raise HTTPException(status_code=502, detail="Model returned no suggestions")

    return {"suggestions": suggestions}


# ── POST /api/chat ────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(req: ChatRequest):
    """
    Stream a chat completion as Server-Sent Events.
    Each SSE event: data: {"delta": "..."}
    Final event:    data: [DONE]
    """
    key = _get_api_key(req.api_key)
    cfg = _resolve_settings(req.settings)

    if not req.messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    # Build message list with transcript injected once at the top
    messages = [m.model_dump() for m in req.messages]
    recent_lines = req.transcript_lines[-cfg.chat_context_lines:]

    if recent_lines:
        already_injected = messages[0].get("content", "").startswith("[SESSION TRANSCRIPT")
        if not already_injected:
            transcript_block = cfg.chat_context_injection.format(
                line_count=len(recent_lines),
                transcript="\n".join(recent_lines),
            )
            messages = [
                {"role": "user",      "content": transcript_block},
                {"role": "assistant", "content": "Understood. I have the session transcript. How can I help?"},
                *messages,
            ]

    async def event_stream():
        try:
            client = make_client(key)
            async for delta in stream_chat_completion(client, messages, cfg.chat_model, cfg.chat_system_prompt):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering if behind proxy
        },
    )


# ── GET /api/health ───────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"ok": True}
