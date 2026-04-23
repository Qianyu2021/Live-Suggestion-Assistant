"""
models.py — Pydantic request/response schemas for all three endpoints.
"""

from typing import Optional
from pydantic import BaseModel


# ── Shared ────────────────────────────────────────────────────────────────────

class SettingsOverride(BaseModel):
    """Optional per-request overrides for prompts and model settings."""
    suggestion_model: Optional[str] = None
    suggestion_judge_model: Optional[str] = None
    chat_model: Optional[str] = None
    suggestion_context_lines: Optional[int] = None
    suggestion_new_lines: Optional[int] = None
    suggestion_candidate_count: Optional[int] = None
    chat_context_lines: Optional[int] = None
    chat_history_messages: Optional[int] = None
    chat_history_chars: Optional[int] = None
    suggestion_system_prompt: Optional[str] = None
    suggestion_user_prompt: Optional[str] = None
    suggestion_judge_system_prompt: Optional[str] = None
    chat_system_prompt: Optional[str] = None
    chat_context_injection: Optional[str] = None


# ── /api/suggest ──────────────────────────────────────────────────────────────

class SuggestRequest(BaseModel):
    api_key: str
    transcript_lines: list[str]
    previous_suggestions: list[list[dict]] = []   # list of past batches
    settings: SettingsOverride = SettingsOverride()


class Suggestion(BaseModel):
    type: str
    preview: str
    detail_hint: str


class SuggestResponse(BaseModel):
    suggestions: list[Suggestion]


# ── /api/chat ─────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str     # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    api_key: str
    messages: list[ChatMessage]
    transcript_lines: list[str] = []
    settings: SettingsOverride = SettingsOverride()
