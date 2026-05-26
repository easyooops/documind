"""Database engine factory and session management."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        if settings.database_type == "sqlite":
            db_path = Path(settings.database_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

        _engine = create_async_engine(
            settings.db_url,
            echo=False,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency injection for FastAPI — yields an async session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables (for development / first-run)."""
    from src.infrastructure.models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_schema)


async def close_db() -> None:
    """Close the database engine."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def _migrate_schema(connection) -> None:
    """Add missing columns to existing tables (safe for repeated calls)."""
    import sqlalchemy as sa

    inspector = sa.inspect(connection)
    migrations = {
        "slide_data": [("dsl_json", "TEXT")],
        "sessions": [("document_id", "VARCHAR(36)")],
        "document_versions": [("pipeline_data", "JSON")],
    }
    for table, columns_to_add in migrations.items():
        if not inspector.has_table(table):
            continue
        columns = {col["name"] for col in inspector.get_columns(table)}
        for name, definition in columns_to_add:
            if name not in columns:
                connection.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
