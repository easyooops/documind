"""User management endpoints."""

from __future__ import annotations

import hashlib
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database import get_session
from src.infrastructure.models import Session

logger = get_logger(__name__)
router = APIRouter(prefix="/users", tags=["users"])


class IdentifyRequest(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)


class UserResponse(BaseModel):
    id: str
    name: str
    email: str


class SessionSummary(BaseModel):
    id: str
    title: str | None
    last_message: str | None = None
    format: str | None = None
    created_at: datetime
    updated_at: datetime


@router.post("/identify", response_model=UserResponse)
async def identify_user(request: IdentifyRequest):
    """Identify a user by name + email. Returns a stable user ID derived from email."""
    user_id = hashlib.sha256(request.email.lower().encode()).hexdigest()[:16]
    return UserResponse(id=user_id, name=request.name, email=request.email)


@router.get("/{user_id}/sessions", response_model=list[SessionSummary])
async def list_user_sessions(
    user_id: str,
    db: AsyncSession = Depends(get_session),
):
    """List all sessions belonging to a user, ordered by most recent."""
    from src.core.config import settings
    from sqlalchemy.orm import selectinload

    if settings.database_type == "sqlite":
        stmt = (
            select(Session)
            .where(text("json_extract(metadata, '$.user_id') = :uid"))
            .params(uid=user_id)
            .options(selectinload(Session.jobs))
            .order_by(Session.updated_at.desc())
            .limit(50)
        )
    else:
        stmt = (
            select(Session)
            .where(Session.metadata_["user_id"].as_string() == user_id)
            .options(selectinload(Session.jobs))
            .order_by(Session.updated_at.desc())
            .limit(50)
        )

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    summaries = []
    for s in sessions:
        meta = s.metadata_ or {}
        last_format = meta.get("format")
        if not last_format and s.jobs:
            last_job = sorted(s.jobs, key=lambda j: j.created_at or datetime.min, reverse=True)
            if last_job:
                last_format = last_job[0].format
        summaries.append(
            SessionSummary(
                id=s.id,
                title=s.title,
                last_message=meta.get("last_message"),
                format=last_format,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
        )
    return summaries
