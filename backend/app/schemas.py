"""Pydantic request/response schemas for the public API."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


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


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    content: str
    model: str | None
    provider: str | None


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


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


class MemoryItemResponse(BaseModel):
    id: str
    kind: str
    content: str
    created_at: str
