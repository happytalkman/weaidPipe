from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    display_name: str
    gemini_configured: bool = False


class SessionView(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserView


class GeminiKeyRequest(BaseModel):
    api_key: str = Field(min_length=20, max_length=500)
    verify_key: bool = Field(default=True, alias="validate")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)


class ChatResponse(BaseModel):
    response: str


class TripleProposal(BaseModel):
    question: str = Field(min_length=1, max_length=12000)
    answer: str = Field(default="", max_length=12000)
    proposed_triples: list[dict] = Field(default_factory=list, max_length=50)


class ContactCreate(BaseModel):
    friend_did: str = Field(min_length=8, max_length=300)
    display_name: str = Field(min_length=1, max_length=120)


class ThreadCreate(BaseModel):
    participant_dids: list[str] = Field(min_length=1, max_length=50)
    topic: str = Field(default="", max_length=300)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class ScheduleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    scheduled_at: datetime
    participant_dids: list[str] = Field(default_factory=list, max_length=50)


class CallCreate(BaseModel):
    recipient_did: str = Field(min_length=8, max_length=300)
    media: str = Field(default="voice", pattern="^(voice|video)$")


class CallEnd(BaseModel):
    duration_seconds: int = Field(default=0, ge=0, le=86400)
