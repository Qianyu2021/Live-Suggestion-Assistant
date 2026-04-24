"""
models.py — Pydantic request/response schemas for all three endpoints.
"""

from typing import Optional
from pydantic import BaseModel, Field


# ── Shared ────────────────────────────────────────────────────────────────────

class SettingsOverride(BaseModel):
    """Optional per-request overrides for prompts and model settings."""
    suggestion_model: Optional[str] = None
    suggestion_judge_model: Optional[str] = None
    chat_model: Optional[str] = None
    chat_planner_model: Optional[str] = None
    suggestion_context_lines: Optional[int] = None
    suggestion_new_lines: Optional[int] = None
    suggestion_candidate_count: Optional[int] = None
    suggestion_agentic_enabled: Optional[bool] = None
    suggestion_repair_enabled: Optional[bool] = None
    chat_context_lines: Optional[int] = None
    chat_history_messages: Optional[int] = None
    chat_history_chars: Optional[int] = None
    chat_agentic_enabled: Optional[bool] = None
    suggestion_system_prompt: Optional[str] = None
    suggestion_user_prompt: Optional[str] = None
    suggestion_judge_system_prompt: Optional[str] = None
    chat_planner_system_prompt: Optional[str] = None
    chat_system_prompt: Optional[str] = None
    chat_context_injection: Optional[str] = None


# ── /api/suggest ──────────────────────────────────────────────────────────────

class SuggestRequest(BaseModel):
    api_key: str
    transcript_lines: list[str]
    previous_suggestions: list[list[dict]] = Field(default_factory=list)   # list of past batches
    settings: SettingsOverride = Field(default_factory=SettingsOverride)


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
    transcript_lines: list[str] = Field(default_factory=list)
    settings: SettingsOverride = Field(default_factory=SettingsOverride)
