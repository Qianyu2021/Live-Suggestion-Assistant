"""
groq_client.py — Thin wrappers around the Groq Python SDK.
 
Models (per spec):
  Transcription : whisper-large-v3
  Suggestions   : openai/gpt-oss-120b
  Chat          : openai/gpt-oss-120b
 
Note: The Groq Python SDK uses max_tokens for ALL models including GPT-OSS.
reasoning_effort is also not yet supported in the SDK — omit it.
"""
 
import json
import re
from typing import AsyncGenerator
 
from groq import AsyncGroq
 
 
def make_client(api_key: str) -> AsyncGroq:
    """Create a one-off async Groq client for the given key."""
    if not api_key:
        raise ValueError("Groq API key is required")
    return AsyncGroq(api_key=api_key)
 
 
async def transcribe_audio(
    client: AsyncGroq,
    audio_bytes: bytes,
    filename: str = "audio.webm",
    mime_type: str = "audio/webm",
) -> str:
    """
    Transcribe raw audio bytes using Whisper Large V3.
    Returns the transcript as a plain string.
    """
    response = await client.audio.transcriptions.create(
        model="whisper-large-v3",
        file=(filename, audio_bytes, mime_type),
        response_format="verbose_json",
        language="en",
    )
    text = (response.text or "").strip()
    if not text:
        return ""

    # Guard 1: suppress likely silence from Whisper segment metadata.
    segments = getattr(response, "segments", None)
    no_speech_probs: list[float] = []
    if segments:
        for seg in segments:
            p = getattr(seg, "no_speech_prob", None)
            if isinstance(p, (int, float)):
                no_speech_probs.append(float(p))
    if no_speech_probs:
        avg_no_speech = sum(no_speech_probs) / len(no_speech_probs)
        if avg_no_speech >= 0.82:
            return ""
        if avg_no_speech >= 0.65 and len(text) <= 48:
            return ""

    # Guard 2: suppress common silence hallucinations.
    normalized = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    silence_hallucinations = {
        "thank you",
        "thanks",
        "thank you for watching",
        "thanks for watching",
        "see you next time",
        "bye",
        "goodbye",
        "you",
    }
    if normalized in silence_hallucinations:
        return ""
    if normalized.startswith("thank you for watching"):
        return ""

    return text
 
 
async def generate_suggestions(
    client: AsyncGroq,
    system_prompt: str,
    user_prompt: str,
    model: str,
) -> dict:
    """
    Run a single JSON-mode completion to produce 3 suggestion cards.
    Returns the parsed dict { "suggestions": [...] }.
    """
    def _parse_json_object(text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("Empty suggestion response")

        # Fast path: direct JSON object.
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # Fallback: extract first JSON object block from mixed text.
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Suggestions JSON parse failed: {text[:240]}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # Primary path: strict JSON mode.
    try:
        completion = await client.chat.completions.create(
            model=model,
            max_tokens=800,
            temperature=0.4,
            response_format={"type": "json_object"},
            messages=messages,
        )
        raw = completion.choices[0].message.content or "{}"
        return _parse_json_object(raw)
    except Exception as strict_exc:
        # Recovery path for `json_validate_failed` and other strict-mode errors.
        retry_completion = await client.chat.completions.create(
            model=model,
            max_tokens=900,
            temperature=0.35,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"{user_prompt}\n\n"
                        "Return ONLY a valid JSON object with this schema:\n"
                        '{"suggestions":[{"type":"ANSWER|FACT_CHECK|QUESTION|TALKING_POINT","preview":"...","detail_hint":"..."}]}'
                    ),
                },
            ],
        )
        retry_raw = retry_completion.choices[0].message.content or "{}"
        try:
            return _parse_json_object(retry_raw)
        except ValueError as parse_exc:
            raise ValueError(
                f"{parse_exc}. Strict JSON mode failed first with: {strict_exc}"
            ) from parse_exc


async def generate_suggestions_candidates(
    client: AsyncGroq,
    system_prompt: str,
    user_prompt: str,
    model: str,
    candidate_count: int = 3,
) -> list[dict]:
    """
    Produce multiple suggestion candidate sets by varying temperature.
    Returns a list of parsed dicts, each shaped like { "suggestions": [...] }.
    """
    temps = [0.25, 0.45, 0.65, 0.85]
    wanted = max(1, min(candidate_count, len(temps)))
    out: list[dict] = []

    for i in range(wanted):
        completion = await client.chat.completions.create(
            model=model,
            max_tokens=900,
            temperature=temps[i],
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{user_prompt}\n\nCANDIDATE_ID: {i + 1}"},
            ],
        )
        raw = completion.choices[0].message.content or "{}"
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            # Keep going; caller handles empty/invalid candidates.
            continue
    return out


async def judge_suggestion_candidates(
    client: AsyncGroq,
    judge_system_prompt: str,
    judge_user_prompt: str,
    model: str,
) -> dict:
    """
    Ask a judge model to select the best candidate index.
    Returns parsed judge JSON.
    """
    completion = await client.chat.completions.create(
        model=model,
        max_tokens=600,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": judge_system_prompt},
            {"role": "user", "content": judge_user_prompt},
        ],
    )
    raw = completion.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge JSON parse failed: {raw[:200]}") from exc
 
 
async def stream_chat_completion(
    client: AsyncGroq,
    messages: list[dict],
    model: str,
    system_prompt: str,
) -> AsyncGenerator[str, None]:
    """
    Yield string deltas from a streaming chat completion.
    """
    stream = await client.chat.completions.create(
        model=model,
        max_tokens=512,
        temperature=0.5,
        stream=True,
        messages=[
            {"role": "system", "content": system_prompt},
            *messages,
        ],
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
