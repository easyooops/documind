"""Model configuration and settings endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database import get_session
from src.infrastructure.models import ModelConfig
from src.schemas.api import ModelConfigRequest, ModelConfigResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/models")
async def list_model_configs(
    db: AsyncSession = Depends(get_session),
):
    """List all model configurations."""
    result = await db.execute(select(ModelConfig).order_by(ModelConfig.role))
    configs = result.scalars().all()

    return [
        ModelConfigResponse(
            id=c.id,
            provider=c.provider,
            model_name=c.model_name,
            role=c.role,
            api_endpoint=c.api_endpoint,
            parameters=c.parameters or {},
            is_active=bool(c.is_active),
            updated_at=c.updated_at or c.created_at,
        )
        for c in configs
    ]


@router.post("/models", response_model=ModelConfigResponse)
async def create_model_config(
    request: ModelConfigRequest,
    db: AsyncSession = Depends(get_session),
):
    """Create or update a model configuration for a specific role."""
    # Deactivate existing configs for this role
    existing = await db.execute(
        select(ModelConfig).where(ModelConfig.role == request.role, ModelConfig.is_active == 1)
    )
    for config in existing.scalars():
        config.is_active = 0

    config_id = str(uuid.uuid4())
    now = datetime.utcnow()

    config = ModelConfig(
        id=config_id,
        provider=request.provider,
        model_name=request.model_name,
        role=request.role,
        api_endpoint=request.api_endpoint,
        api_key_env_var=request.api_key_env_var,
        parameters=request.parameters,
        is_active=1,
        created_at=now,
        updated_at=now,
    )
    db.add(config)

    return ModelConfigResponse(
        id=config_id,
        provider=config.provider,
        model_name=config.model_name,
        role=config.role,
        api_endpoint=config.api_endpoint,
        parameters=config.parameters or {},
        is_active=True,
        updated_at=now,
    )


@router.delete("/models/{config_id}")
async def delete_model_config(
    config_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Delete a model configuration."""
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == config_id))
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="Model config not found")

    await db.delete(config)
    return {"deleted": True}
