"""SQLAlchemy ORM models for DocuMind."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobPhase(str, enum.Enum):
    PLANNING = "planning"
    DESIGNING = "designing"
    GENERATING = "generating"
    VALIDATING = "validating"
    CONVERTING = "converting"
    QA = "qa"
    EXPORTING = "exporting"
    DONE = "done"


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True)
    document_id = Column(String(36), ForeignKey("generation_jobs.id"), nullable=True)
    title = Column(String(500), nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")
    jobs = relationship("GenerationJob", back_populates="session", foreign_keys="GenerationJob.session_id")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(String, nullable=False)
    generation_job_id = Column(String(36), ForeignKey("generation_jobs.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="messages")


class ImageAttachment(Base):
    __tablename__ = "image_attachments"

    id = Column(String(36), primary_key=True)
    message_id = Column(String(36), ForeignKey("messages.id"), nullable=True)
    filename = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=False)
    mime_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    analysis = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Template(Base):
    __tablename__ = "templates"

    id = Column(String(36), primary_key=True)
    name = Column(String(200), nullable=False)
    filename = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    analysis = Column(JSON, nullable=True)
    status = Column(String(20), default="uploaded")
    created_at = Column(DateTime, default=datetime.utcnow)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id = Column(String(36), primary_key=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=True)
    template_id = Column(String(36), ForeignKey("templates.id"), nullable=True)
    query = Column(String, nullable=False)
    format = Column(String(20), default="pptx")
    options = Column(JSON, default=dict)
    status = Column(String(20), default=JobStatus.QUEUED.value)
    phase = Column(String(20), nullable=True)
    progress = Column(Float, default=0.0)
    slide_plan = Column(JSON, nullable=True)
    design_system = Column(JSON, nullable=True)
    error = Column(JSON, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="jobs", foreign_keys=[session_id])
    template = relationship("Template")
    file = relationship("GeneratedFile", back_populates="job", uselist=False)
    steps = relationship("GenerationStep", back_populates="job", cascade="all, delete-orphan")
    slides = relationship("SlideData", back_populates="job", cascade="all, delete-orphan")
    versions = relationship("DocumentVersion", back_populates="document", cascade="all, delete-orphan")


class GenerationStep(Base):
    __tablename__ = "generation_steps"

    id = Column(String(36), primary_key=True)
    job_id = Column(String(36), ForeignKey("generation_jobs.id"), nullable=False)
    step_name = Column(String(50), nullable=False)
    step_order = Column(Integer, nullable=False)
    agent_name = Column(String(50), nullable=False)
    status = Column(String(20), default="pending")
    input_summary = Column(JSON, nullable=True)
    output_summary = Column(JSON, nullable=True)
    llm_model_used = Column(String(100), nullable=True)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("GenerationJob", back_populates="steps")


class SlideData(Base):
    __tablename__ = "slide_data"

    id = Column(String(36), primary_key=True)
    job_id = Column(String(36), ForeignKey("generation_jobs.id"), nullable=False)
    slide_number = Column(Integer, nullable=False)
    slide_type = Column(String(30), nullable=True)
    html_content = Column(String, nullable=True)
    dsl_json = Column(String, nullable=True)
    design_spec = Column(JSON, nullable=True)
    validation_result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("GenerationJob", back_populates="slides")


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(String(36), primary_key=True)
    document_id = Column(String(36), ForeignKey("generation_jobs.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    parent_version_id = Column(String(36), ForeignKey("document_versions.id"), nullable=True)
    trigger = Column(String(20), nullable=False)
    user_instruction = Column(String, nullable=True)
    slide_plan = Column(JSON, nullable=True)
    design_system = Column(JSON, nullable=True)
    pipeline_data = Column(JSON, nullable=True)
    file_path = Column(String(1000), nullable=True)
    fidelity_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("GenerationJob", back_populates="versions")
    parent = relationship("DocumentVersion", remote_side=[id])
    slide_versions = relationship("SlideVersion", back_populates="version", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("document_id", "version_number"),)


class SlideVersion(Base):
    __tablename__ = "slide_versions"

    id = Column(String(36), primary_key=True)
    version_id = Column(String(36), ForeignKey("document_versions.id"), nullable=False)
    slide_index = Column(Integer, nullable=False)
    content = Column(JSON, nullable=False)
    html = Column(String, nullable=False)
    design_spec = Column(JSON, nullable=True)
    changed_from_parent = Column(Integer, default=0)
    change_type = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    version = relationship("DocumentVersion", back_populates="slide_versions")

    __table_args__ = (UniqueConstraint("version_id", "slide_index"),)


class GeneratedFile(Base):
    __tablename__ = "generated_files"

    id = Column(String(36), primary_key=True)
    job_id = Column(String(36), ForeignKey("generation_jobs.id"), nullable=False)
    filename = Column(String(500), nullable=False)
    storage_backend = Column(String(20), nullable=False)
    storage_path = Column(String(1000), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    mime_type = Column(String(100), nullable=False)
    fidelity_score = Column(Float, nullable=True)
    slide_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    job = relationship("GenerationJob", back_populates="file")


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id = Column(String(36), primary_key=True)
    provider = Column(String(30), nullable=False)
    model_name = Column(String(100), nullable=False)
    role = Column(String(30), nullable=False)
    api_endpoint = Column(String(500), nullable=True)
    api_key_env_var = Column(String(100), nullable=True)
    parameters = Column(JSON, default=dict)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"

    key = Column(String(200), primary_key=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String(100), nullable=True)
