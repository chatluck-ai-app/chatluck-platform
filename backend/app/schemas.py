"""Pydantic request/response schemas for the public API."""
from __future__ import annotations

import unicodedata

from pydantic import BaseModel, EmailStr, Field, field_validator


def _clean_email(value: str) -> str:
    """Strip invisible bidi/format characters and whitespace that mobile
    keyboards (especially Arabic layouts) insert, then lowercase."""
    cleaned = "".join(
        ch for ch in value.strip() if unicodedata.category(ch) != "Cf"
    )
    return cleaned.strip().lower()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=120)

    _email_clean = field_validator("email", mode="before")(_clean_email)

    @field_validator("display_name", mode="before")
    @classmethod
    def _name_strip(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    _email_clean = field_validator("email", mode="before")(_clean_email)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    display_name: str


class ChatMessage(BaseModel):
    role: str
    content: str


class AttachmentIn(BaseModel):
    mime_type: str = Field(pattern=r"^(image/(png|jpeg|webp|gif))$")
    data_base64: str = Field(min_length=8, max_length=14_000_000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32000)
    conversation_id: str | None = None
    model: str | None = None  # manual model selection
    stream: bool = False
    attachments: list[AttachmentIn] | None = None
    regenerate: bool = False  # re-answer the last user message
    edit_message_id: str | None = None  # replace this user message, drop later ones
    title_hint: str | None = None  # preferred title when creating a conversation
    assistant_id: str | None = None  # bind a custom assistant to a NEW conversation


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    content: str
    model: str | None
    provider: str | None
    sources: list[dict] | None = None


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    archived: bool = False
    shared: bool = False
    assistant_id: str | None = None


class ModelInfoResponse(BaseModel):
    id: str
    provider: str
    capabilities: list[str]
    context_window: int
    free: bool
    available: bool


class MemoryItemRequest(BaseModel):
    kind: str = Field(pattern="^(preference|project|file_ref|task_history)$")
    content: str = Field(min_length=1, max_length=8000)


class MemoryItemUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class UserSettingsOut(BaseModel):
    memory_enabled: bool


class UserSettingsIn(BaseModel):
    memory_enabled: bool


class MemoryItemResponse(BaseModel):
    id: str
    kind: str
    content: str
    created_at: str
