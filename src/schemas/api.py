"""API request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ═══ Generation Requests ═══

class GenerateRequest(BaseModel):
    query: str = Field(description="Natural language document generation request")
    format: Literal["pptx", "docx", "pdf", "md", "xlsx", "hwp"] = Field(
        default="pptx", description="pptx|docx|pdf|md|xlsx|hwp (hwp produces open HWPX)"
    )
    template_id: str | None = None
    session_id: str | None = None
    image_attachment_ids: list[str] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)


class RefineRequest(BaseModel):
    document_id: str
    instructions: str = Field(description="Natural language refinement request")
    target_slides: list[int] | None = None


# ═══ Session ═══

class ChatMessage(BaseModel):
    role: str = Field(description="user|assistant|system")
    content: str
    generation_job_id: str | None = None


class SessionResponse(BaseModel):
    id: str
    title: str | None
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ═══ Document / Job ═══

class JobStatusResponse(BaseModel):
    id: str
    status: str
    phase: str | None
    progress: float
    slide_count: int | None = None
    fidelity_score: float | None = None
    error: dict | None = None
    download_url: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class DocumentVersionResponse(BaseModel):
    id: str
    version_number: int
    trigger: str
    user_instruction: str | None
    fidelity_score: float | None
    slide_count: int | None = None
    download_url: str | None = None
    created_at: datetime
    is_latest: bool = False


# ═══ Template ═══

class TemplateUploadResponse(BaseModel):
    id: str
    name: str
    filename: str
    status: str
    size_bytes: int
    created_at: datetime


class TemplateAnalysisResponse(BaseModel):
    id: str
    status: str
    analysis: dict | None = None


class ImageAttachmentResponse(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    created_at: datetime


# ═══ Settings ═══

class ModelConfigRequest(BaseModel):
    provider: str
    model_name: str
    role: str = Field(description="planning|design|code|validation|qa")
    api_endpoint: str | None = None
    api_key_env_var: str | None = None
    parameters: dict = Field(default_factory=dict)


class ModelConfigResponse(BaseModel):
    id: str
    provider: str
    model_name: str
    role: str
    api_endpoint: str | None
    parameters: dict
    is_active: bool
    updated_at: datetime


# ═══ SSE Events ═══

class SSEEvent(BaseModel):
    event: str
    data: dict = Field(default_factory=dict)
    phase: str | None = None
    agent: str | None = None
