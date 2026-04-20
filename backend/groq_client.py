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
    return (response.text or "").strip()
 
 
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
    completion = await client.chat.completions.create(
        model=model,
        max_tokens=800,
        temperature=0.4,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    )
    raw = completion.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Suggestions JSON parse failed: {raw[:200]}") from exc
 
 
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
        max_tokens=1024,
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
