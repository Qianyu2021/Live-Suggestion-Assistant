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
from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from groq_client import (
    make_client,
    transcribe_audio,
    generate_suggestions,
    generate_suggestions_candidates,
    judge_suggestion_candidates,
    complete_text,
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


def _format_prompt_or_400(template: str, **values) -> str:
    """Return a formatted prompt or raise a clear 400 for bad override templates."""
    try:
        return template.format(**values)
    except KeyError as exc:
        field_name = str(exc).strip("'")
        raise HTTPException(
            status_code=400,
            detail=f"Prompt template references unknown placeholder '{field_name}'",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid prompt template: {exc}") from exc


def _provider_http_exception(exc: Exception) -> HTTPException:
    """Normalize Groq SDK failures into stable FastAPI errors."""
    if isinstance(exc, AuthenticationError):
        return HTTPException(status_code=401, detail="Groq authentication failed. Check the API key.")
    if isinstance(exc, PermissionDeniedError):
        return HTTPException(status_code=403, detail="Groq denied this request.")
    if isinstance(exc, RateLimitError):
        return HTTPException(status_code=429, detail="Groq rate limit exceeded. Please retry shortly.")
    if isinstance(exc, (BadRequestError, UnprocessableEntityError)):
        return HTTPException(status_code=400, detail=f"Invalid Groq request: {exc}")
    if isinstance(exc, APITimeoutError):
        return HTTPException(status_code=504, detail="Groq request timed out.")
    if isinstance(exc, APIConnectionError):
        return HTTPException(status_code=503, detail="Unable to reach Groq right now.")
    if isinstance(exc, APIStatusError):
        return HTTPException(status_code=502, detail=f"Groq API error: {exc}")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=502, detail=f"Groq error: {exc}")


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


def _derive_meeting_mode(new_lines: list[str], recent_lines: list[str]) -> str:
    """Infer a lightweight meeting mode to improve suggestion targeting."""
    text = (" ".join(recent_lines[-20:]) + " " + " ".join(new_lines)).lower()
    if any(tok in text for tok in ["incident", "outage", "sev", "root cause", "rollback"]):
        return "Incident / troubleshooting mode: prioritize verification, root-cause hypotheses, and mitigation next steps."
    if any(tok in text for tok in ["roadmap", "timeline", "quarter", "milestone", "priorit"]):
        return "Planning mode: prioritize trade-offs, sequencing, and decision-driving questions."
    if any(tok in text for tok in ["customer", "deal", "pricing", "objection", "renewal"]):
        return "Customer / go-to-market mode: prioritize objections, proof points, and clear business impact framing."
    if any(tok in text for tok in ["hiring", "interview", "candidate"]):
        return "Interview mode: prioritize targeted follow-up questions and concrete evidence."
    return "General technical discussion mode: prioritize concrete questions, actionable talking points, and direct answers."


def _derive_timing_objective(new_lines: list[str]) -> str:
    """Define what is most useful to surface in the next few seconds."""
    joined = " ".join(new_lines).lower()
    if "?" in joined:
        return "A question was just asked; include at least one card that helps answer it immediately."
    if any(tok in joined for tok in ["decision", "choose", "pick", "go with", "approve"]):
        return "A decision moment is near; include cards that reduce ambiguity quickly."
    if any(tok in joined for tok in ["blocked", "bottleneck", "issue", "problem", "stuck"]):
        return "There may be a blocker; include cards that identify root cause and next action."
    return "Surface one high-utility next question, one usable talking point, and one direct insight."


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


def _is_generic_preview(preview: str) -> bool:
    p = preview.strip().lower()
    if len(p) < 18:
        return True
    generic_markers = [
        "ask for more details",
        "consider tradeoffs",
        "clarify this point",
        "discuss next steps",
        "talk about risks",
        "gather more context",
    ]
    return any(m in p for m in generic_markers)


def _preview_needs_click_to_be_useful(preview: str) -> bool:
    """Detect teaser-style previews that do not stand on their own."""
    p = " ".join(preview.strip().lower().split())
    if not p:
        return True

    teaser_prefixes = (
        "ask about ",
        "ask whether ",
        "check whether ",
        "clarify whether ",
        "clarify why ",
        "discuss ",
        "explore ",
        "look into ",
        "follow up on ",
        "dig into ",
    )
    if any(p.startswith(prefix) for prefix in teaser_prefixes):
        return True

    teaser_phrases = (
        "for more details",
        "to learn more",
        "to understand better",
        "for clarification",
        "more context",
        "next steps",
    )
    return any(phrase in p for phrase in teaser_phrases)


def _detail_hint_is_thin(preview: str, detail_hint: str) -> bool:
    """Ensure click-through detail adds real value beyond the preview."""
    detail = " ".join(detail_hint.strip().lower().split())
    prev = " ".join(preview.strip().lower().split())
    if len(detail) < 50:
        return True
    if detail == prev:
        return True
    if detail.startswith(prev):
        suffix = detail[len(prev):].strip(" .:-")
        if len(suffix) < 24:
            return True
    return False


def _quality_issues(suggestions: list[dict]) -> list[str]:
    """Detect weak batches that should trigger a repair pass."""
    issues: list[str] = []
    if len(suggestions) != 3:
        issues.append("not_exactly_three")

    previews = [s.get("preview", "").strip().lower() for s in suggestions if s.get("preview")]
    if len(set(previews)) != len(previews):
        issues.append("duplicate_preview")

    types = [s.get("type", "") for s in suggestions]
    if len(set(types)) < 2:
        issues.append("low_type_diversity")

    generic_count = sum(1 for s in suggestions if _is_generic_preview(str(s.get("preview", ""))))
    if generic_count >= 2:
        issues.append("too_generic")

    preview_teaser_count = sum(
        1 for s in suggestions if _preview_needs_click_to_be_useful(str(s.get("preview", "")))
    )
    if preview_teaser_count >= 1:
        issues.append("preview_not_standalone")

    thin_detail_count = sum(
        1
        for s in suggestions
        if _detail_hint_is_thin(str(s.get("preview", "")), str(s.get("detail_hint", "")))
    )
    if thin_detail_count >= 1:
        issues.append("detail_hint_not_additive")
    return issues


def _build_suggestion_repair_prompt(
    recent_lines: list[str],
    new_lines: list[str],
    suggestions: list[dict],
    issues: list[str],
    context_signals: str,
    mix_policy: str,
    meeting_mode: str,
    timing_objective: str,
) -> str:
    """Ask the model to repair a weak suggestion set into a stronger one."""
    current = "\n".join(
        f"- [{s.get('type', '')}] {s.get('preview', '')} || hint: {s.get('detail_hint', '')}"
        for s in suggestions
    ) or "(none)"
    issue_text = ", ".join(issues) if issues else "unknown"

    return (
        "REPAIR THIS SUGGESTION SET.\n"
        "The current set is weak and must be rewritten.\n\n"
        "QUALITY ISSUES:\n"
        f"- {issue_text}\n\n"
        "RECENT CONTEXT:\n"
        + "\n".join(recent_lines[-20:])
        + "\n\nNEW LINES (primary trigger):\n"
        + "\n".join(new_lines[-10:])
        + "\n\nCONTEXT SIGNALS:\n"
        + context_signals
        + "\n\nSUGGESTION MIX POLICY:\n"
        + mix_policy
        + "\n\nMEETING MODE HINT:\n"
        + meeting_mode
        + "\n\nTIMING OBJECTIVE:\n"
        + timing_objective
        + "\n\nCURRENT WEAK SET:\n"
        + current
        + "\n\nReturn valid JSON only with exactly 3 improved suggestions using schema:\n"
        + '{"suggestions":[{"type":"ANSWER|FACT_CHECK|QUESTION|TALKING_POINT","preview":"...","detail_hint":"..."}]}'
    )


def _fallback_suggestions(new_lines: list[str], blocked_previews: set[str], needed: int) -> list[dict]:
    """
    Deterministic fallback to guarantee exactly 3 cards when model under-returns.
    These still provide useful, clickable value.
    """
    joined = " ".join(line.strip() for line in new_lines if line.strip())
    topic = joined[:160] + ("..." if len(joined) > 160 else "")
    if not topic:
        topic = "the latest discussion point"

    lower = joined.lower()
    mentions_guardrails = "guardrail" in lower or "prompt injection" in lower or "jailbreak" in lower
    mentions_runtime = "runtime" in lower
    mentions_policy = "policy" in lower or "pii" in lower or "unsafe" in lower

    if mentions_guardrails or mentions_runtime or mentions_policy:
        templates = [
            {
                "type": "QUESTION",
                "preview": "Which guardrails run before the model versus after the model?",
                "detail_hint": "Ask the speaker to separate inbound checks from outbound checks so the control points become concrete. This usually surfaces whether they mean moderation, policy checks, schema validation, or response filtering.",
            },
            {
                "type": "TALKING_POINT",
                "preview": "Guardrails are part of the runtime path, not just an after-the-fact safety review.",
                "detail_hint": "Use this to frame guardrails as operational infrastructure around the model request lifecycle. It reinforces that the team is discussing controls that act during execution, not only offline audits or policy docs.",
            },
            {
                "type": "ANSWER",
                "preview": "Guardrails sanitize what enters the agent and validate what leaves it.",
                "detail_hint": "Answer directly using only the transcript framing: inbound content is checked before the model acts, and outbound content is checked before it reaches users or tools. Expand by naming the risks already mentioned in the transcript, such as prompt injection, PII requests, and off-topic drift.",
            },
            {
                "type": "FACT_CHECK",
                "preview": "Guardrails reduce prompt-injection risk, but they are not the entire defense on their own.",
                "detail_hint": "Use this when the conversation risks overstating what sanitization can do. It gives the clicked answer room to explain layered defenses without inventing specific vendors or metrics.",
            },
        ]
    else:
        templates = [
            {
                "type": "QUESTION",
                "preview": "What is the most important unresolved point in the latest discussion?",
                "detail_hint": "Ask for the single open question or decision the group still needs to resolve. This keeps the conversation focused on the highest-value next step instead of broadening the topic.",
            },
            {
                "type": "TALKING_POINT",
                "preview": "Restate the core point in one sentence before the discussion branches further.",
                "detail_hint": "Use this when the transcript introduces a new concept quickly and the group needs a crisp shared framing. The clicked answer can turn it into concise meeting-ready wording tied to the current topic.",
            },
            {
                "type": "ANSWER",
                "preview": "Direct answer: summarize the latest point in plain language before adding nuance.",
                "detail_hint": f"Give a direct, grounded summary of this context: \"{topic}\". The clicked answer should explain the point clearly first, then add only transcript-supported nuance and the most relevant follow-up.",
            },
            {
                "type": "FACT_CHECK",
                "preview": "Separate what the transcript states clearly from what is still implied or unstated.",
                "detail_hint": "Use this when the conversation risks jumping from a real statement to an unsupported conclusion. The clicked answer can clarify exactly what is said, what is inferred, and what should be verified next.",
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


def _build_suggestion_judge_prompt(
    recent_lines: list[str],
    new_lines: list[str],
    candidates: list[list[dict]],
) -> str:
    """Create compact judge input with context + candidate sets."""
    blocks = []
    for idx, cand in enumerate(candidates):
        blocks.append(f"CANDIDATE {idx}:")
        for s in cand:
            blocks.append(
                f"- [{s.get('type', '')}] {s.get('preview', '')} || hint: {s.get('detail_hint', '')}"
            )
    return (
        "RECENT CONTEXT:\n"
        + "\n".join(recent_lines[-20:])
        + "\n\nNEW LINES:\n"
        + "\n".join(new_lines[-8:])
        + "\n\nCANDIDATE SETS:\n"
        + "\n".join(blocks)
        + "\n\nChoose the best candidate index."
    )


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
    except Exception as exc:
        raise _provider_http_exception(exc) from exc


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
    context_signals = _derive_context_signals(new_lines)
    mix_policy = _derive_mix_policy(new_lines)
    meeting_mode = _derive_meeting_mode(new_lines, recent)
    timing_objective = _derive_timing_objective(new_lines)

    # Fill placeholders for both the current prompt template and any legacy
    # user overrides saved in localStorage.
    user_prompt = _format_prompt_or_400(
        cfg.suggestion_user_prompt,
        context_count=len(context_lines),
        context_transcript="\n".join(context_lines) if context_lines else "(none)",
        new_count=len(new_lines),
        new_transcript="\n".join(new_lines) if new_lines else "(none)",
        context_signals=context_signals,
        mix_policy=mix_policy,
        meeting_mode=meeting_mode,
        timing_objective=timing_objective,
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
        suggestions: list[dict] = []

        if cfg.suggestion_agentic_enabled and int(cfg.suggestion_candidate_count) > 1:
            candidate_payloads = await generate_suggestions_candidates(
                client,
                system_prompt,
                user_prompt,
                cfg.suggestion_model,
                candidate_count=min(int(cfg.suggestion_candidate_count), 4),
            )
            candidate_sets = [
                _extract_unique_suggestions(payload, previous_preview_keys)
                for payload in candidate_payloads
            ]
            candidate_sets = [c for c in candidate_sets if c]

            if candidate_sets:
                judge_prompt = _build_suggestion_judge_prompt(recent, new_lines, candidate_sets)
                judge = await judge_suggestion_candidates(
                    client,
                    cfg.suggestion_judge_system_prompt,
                    judge_prompt,
                    cfg.suggestion_judge_model,
                )
                best_idx = int(judge.get("best_index", 0))
                if best_idx < 0 or best_idx >= len(candidate_sets):
                    best_idx = 0
                suggestions = candidate_sets[best_idx]

        if not suggestions:
            result = await generate_suggestions(client, system_prompt, user_prompt, cfg.suggestion_model)
            suggestions = _extract_unique_suggestions(result, previous_preview_keys)

        if cfg.suggestion_repair_enabled:
            issues_before = _quality_issues(suggestions[:3])
            if issues_before:
                repair_prompt = _build_suggestion_repair_prompt(
                    recent,
                    new_lines,
                    suggestions[:3],
                    issues_before,
                    context_signals,
                    mix_policy,
                    meeting_mode,
                    timing_objective,
                )
                repaired_raw = await generate_suggestions(
                    client,
                    system_prompt,
                    repair_prompt,
                    cfg.suggestion_model,
                )
                repaired = _extract_unique_suggestions(repaired_raw, previous_preview_keys)

                if repaired:
                    # Quality-first tie-break: let judge pick between original and repaired.
                    original_top = suggestions[:3]
                    repaired_top = repaired[:3]
                    original_with_fallback = list(original_top)
                    if len(original_with_fallback) < 3:
                        blocked = previous_preview_keys | {
                            s["preview"].strip().lower() for s in original_with_fallback
                        }
                        original_with_fallback.extend(
                            _fallback_suggestions(new_lines, blocked, 3 - len(original_with_fallback))
                        )
                    repaired_with_fallback = list(repaired_top)
                    if len(repaired_with_fallback) < 3:
                        blocked = previous_preview_keys | {
                            s["preview"].strip().lower() for s in repaired_with_fallback
                        }
                        repaired_with_fallback.extend(
                            _fallback_suggestions(new_lines, blocked, 3 - len(repaired_with_fallback))
                        )

                    duel_candidates = [original_with_fallback[:3], repaired_with_fallback[:3]]
                    try:
                        duel_prompt = _build_suggestion_judge_prompt(recent, new_lines, duel_candidates)
                        duel = await judge_suggestion_candidates(
                            client,
                            cfg.suggestion_judge_system_prompt,
                            duel_prompt,
                            cfg.suggestion_judge_model,
                        )
                        best_duel = int(duel.get("best_index", 0))
                        suggestions = duel_candidates[1] if best_duel == 1 else duel_candidates[0]
                    except Exception:
                        # If duel judge fails, keep whichever has fewer obvious issues.
                        repaired_issues = _quality_issues(repaired_with_fallback[:3])
                        suggestions = (
                            repaired_with_fallback
                            if len(repaired_issues) < len(issues_before)
                            else original_with_fallback
                        )

        # Fast path: skip an extra model round-trip; fill missing cards locally.
        if len(suggestions) < 3:
            blocked = previous_preview_keys | {s['preview'].strip().lower() for s in suggestions}
            suggestions.extend(_fallback_suggestions(new_lines, blocked, 3 - len(suggestions)))
    except Exception as exc:
        raise _provider_http_exception(exc) from exc

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
        transcript_block = _format_prompt_or_400(
            cfg.chat_context_injection,
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

    planner_notes = ""
    if cfg.chat_agentic_enabled:
        try:
            latest_user = ""
            for msg in reversed(req.messages):
                if msg.role == "user":
                    latest_user = msg.content
                    break
            if latest_user:
                planner_user_prompt = (
                    f"LATEST USER TEXT:\n{latest_user}\n\n"
                    f"RECENT TRANSCRIPT LINES:\n{chr(10).join(recent_lines[-20:])}"
                )
                client = make_client(key)
                planner_notes = await complete_text(
                    client,
                    cfg.chat_planner_system_prompt,
                    planner_user_prompt,
                    cfg.chat_planner_model,
                    max_tokens=180,
                    temperature=0.2,
                )
        except Exception:
            planner_notes = ""

    async def event_stream():
        try:
            client = make_client(key)
            system_prompt = cfg.chat_system_prompt
            if planner_notes:
                system_prompt += f"\n\nINTERNAL RESPONSE PLAN:\n{planner_notes}\nFollow this plan."
            async for delta in stream_chat_completion(
                client, messages, cfg.chat_model, system_prompt
            ):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            err = _provider_http_exception(exc)
            yield f"data: {json.dumps({'error': err.detail, 'status_code': err.status_code})}\n\n"

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
