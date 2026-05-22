"""Chat / session endpoints with SSE streaming."""

from __future__ import annotations

import asyncio
import json
import time
import traceback
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from src.core.logging import get_logger
from src.infrastructure.database import get_session
from src.infrastructure.models import GeneratedFile, GenerationJob, JobStatus, Message, Session, SlideData
from src.schemas.api import ChatMessage, GenerateRequest, SessionResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class CreateSessionRequest(BaseModel):
    user_id: str | None = None


@router.post("/sessions")
async def create_session(
    request: CreateSessionRequest | None = None,
    db: AsyncSession = Depends(get_session),
):
    """Create a new conversation session."""
    session_id = str(uuid.uuid4())
    user_id = request.user_id if request else None

    logger.info("chat.create_session", session_id=session_id, user_id=user_id)

    session = Session(
        id=session_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        metadata_={"user_id": user_id} if user_id else {},
    )
    db.add(session)
    return {"id": session_id, "created_at": session.created_at}


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session_info(
    session_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Get session details with message history."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msgs_result = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
    )
    messages = [
        ChatMessage(role=m.role, content=m.content, generation_job_id=m.generation_job_id)
        for m in msgs_result.scalars()
    ]

    return SessionResponse(
        id=session.id,
        title=session.title,
        messages=messages,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.post("/sessions/{session_id}/messages/stream")
async def stream_generation(
    session_id: str,
    request: GenerateRequest,
    db: AsyncSession = Depends(get_session),
):
    """Stream document generation progress via SSE."""
    logger.info(
        "chat.stream_generation.request",
        session_id=session_id,
        query=request.query[:100],
        format=request.format,
    )
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    user_msg = Message(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role="user",
        content=request.query,
        created_at=datetime.utcnow(),
    )
    db.add(user_msg)

    if not session.title:
        session.title = request.query[:80]
        session.updated_at = datetime.utcnow()

    await db.commit()

    async def event_generator():
        from src.engine import _get_format_pipeline
        from src.infrastructure.database import get_session_factory
        from src.schemas.agents import DocuMindState

        initial_state: DocuMindState = {
            "user_query": request.query,
            "session_id": session_id,
            "template_id": request.template_id,
            "conversation_history": [],
            "document_format": request.format,
            "needs_research": True,
            "template_provided": request.template_id is not None,
            "current_phase": "planning",
            "errors": [],
            "retry_count": 0,
            "qa_iterations": 0,
            "slides_dsl": [],
        }

        NODE_PHASE_MAP = {
            "research": "planning",
            "narrative": "planning",
            "content_writer": "planning",
            "audience": "planning",
            "template_analysis": "designing",
            "layout_compose": "designing",
            "style_direct": "designing",
            "asset_plan": "designing",
            "code_generate": "generating",
            "consistency_check": "generating",
            "validate": "validating",
            "convert": "converting",
            "qa_critic": "qa",
            "export": "exporting",
        }

        NODE_DESCRIPTIONS = {
            "research": "웹 리서치 및 데이터 수집",
            "narrative": "내러티브 구조 설계",
            "content_writer": "슬라이드 콘텐츠 작성",
            "audience": "청중 분석 및 톤 결정",
            "template_analysis": "템플릿 분석",
            "layout_compose": "레이아웃 구성",
            "style_direct": "디자인 시스템 생성",
            "asset_plan": "비주얼 에셋 계획",
            "code_generate": "슬라이드 OOXML-DSL 생성",
            "consistency_check": "일관성 검증",
            "validate": "코드 유효성 검사",
            "convert": "PPTX 파일 변환",
            "qa_critic": "품질 평가 (QA)",
            "export": "최종 내보내기",
        }

        yield {"event": "phase_start", "data": json.dumps({"phase": "planning"})}

        try:
            pipeline = _get_format_pipeline(request.format)
            final_state = initial_state
            current_phase = "planning"
            completed_count = 0

            total_nodes = 0
            try:
                graph_nodes = list(pipeline.nodes.keys())
                total_nodes = len([n for n in graph_nodes if n not in ("__start__", "__end__")])
            except Exception:
                total_nodes = 14

            node_start_times: dict[str, float] = {}
            active_node: str | None = None
            last_heartbeat: float = time.time()

            async for event in pipeline.astream_events(
                initial_state,
                version="v2",
                config={"recursion_limit": 80},
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                tags = event.get("tags", [])
                now = time.time()

                # --- LangGraph node lifecycle ---
                if kind == "on_chain_start" and name in NODE_DESCRIPTIONS:
                    node_name = name
                    phase = NODE_PHASE_MAP.get(node_name, current_phase)
                    description = NODE_DESCRIPTIONS.get(node_name, node_name)
                    node_start_times[node_name] = now
                    active_node = node_name

                    if phase != current_phase:
                        current_phase = phase
                        yield {
                            "event": "phase_start",
                            "data": json.dumps({"phase": phase}),
                        }

                    yield {
                        "event": "node_start",
                        "data": json.dumps({
                            "node": node_name,
                            "phase": phase,
                            "description": description,
                        }),
                    }
                    last_heartbeat = now

                elif kind == "on_chain_end" and name in NODE_DESCRIPTIONS:
                    node_name = name
                    phase = NODE_PHASE_MAP.get(node_name, current_phase)
                    description = NODE_DESCRIPTIONS.get(node_name, node_name)
                    completed_count += 1
                    elapsed = now - node_start_times.get(node_name, now)

                    node_output = event.get("data", {}).get("output", {})
                    has_errors = bool(
                        node_output.get("errors")
                        if isinstance(node_output, dict) else False
                    )
                    if isinstance(node_output, dict):
                        final_state = {**final_state, **node_output}

                    yield {
                        "event": "node_complete",
                        "data": json.dumps({
                            "node": node_name,
                            "phase": phase,
                            "description": description,
                            "has_errors": has_errors,
                            "progress": min(round(completed_count / total_nodes, 2), 1.0),
                            "elapsed_seconds": round(elapsed, 1),
                        }),
                    }
                    active_node = None
                    last_heartbeat = now

                # --- LLM call lifecycle (real-time activity within nodes) ---
                elif kind == "on_chat_model_start":
                    if active_node:
                        description = NODE_DESCRIPTIONS.get(active_node, active_node)
                        elapsed = now - node_start_times.get(active_node, now)
                        yield {
                            "event": "node_activity",
                            "data": json.dumps({
                                "node": active_node,
                                "activity": "llm_call_start",
                                "description": f"{description} - LLM 호출 중...",
                                "elapsed_seconds": round(elapsed, 1),
                            }),
                        }
                        last_heartbeat = now

                elif kind == "on_chat_model_end":
                    if active_node:
                        elapsed = now - node_start_times.get(active_node, now)
                        yield {
                            "event": "node_activity",
                            "data": json.dumps({
                                "node": active_node,
                                "activity": "llm_call_end",
                                "description": f"{NODE_DESCRIPTIONS.get(active_node, active_node)} - 응답 수신 완료",
                                "elapsed_seconds": round(elapsed, 1),
                            }),
                        }
                        last_heartbeat = now

                # Periodic heartbeat so the client knows we're alive during long ops
                if active_node and (now - last_heartbeat) > 5.0:
                    elapsed = now - node_start_times.get(active_node, now)
                    yield {
                        "event": "node_activity",
                        "data": json.dumps({
                            "node": active_node,
                            "activity": "processing",
                            "description": f"{NODE_DESCRIPTIONS.get(active_node, active_node)} - 처리 중... ({round(elapsed, 0):.0f}s)",
                            "elapsed_seconds": round(elapsed, 1),
                        }),
                    }
                    last_heartbeat = now

            # Persist results using a fresh DB session (outside request scope)
            job_id = str(uuid.uuid4())
            output_path = final_state.get("output_path", "")

            async with get_session_factory()() as fresh_db:
                gen_job = GenerationJob(
                    id=job_id,
                    session_id=session_id,
                    query=request.query,
                    format=request.format,
                    status=JobStatus.COMPLETED.value,
                    phase="done",
                    progress=1.0,
                    started_at=datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                    created_at=datetime.utcnow(),
                )
                fresh_db.add(gen_job)

                if output_path:
                    import os
                    file_path = output_path
                    file_size = 0
                    try:
                        file_size = os.path.getsize(file_path)
                    except OSError:
                        pass

                    gen_file = GeneratedFile(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        filename=os.path.basename(file_path),
                        storage_backend="local",
                        storage_path=file_path,
                        size_bytes=file_size,
                        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        created_at=datetime.utcnow(),
                    )
                    fresh_db.add(gen_file)

                # Save slide HTML for preview (derived from DSL)
                slides_html = final_state.get("slides_html", [])
                slides_dsl = final_state.get("slides_dsl", [])
                for slide in slides_html:
                    idx = slide.get("index", 0)
                    dsl_data = next((d for d in slides_dsl if d.get("index") == idx), None)
                    slide_data = SlideData(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        slide_number=idx,
                        slide_type=slide.get("metadata", {}).get("slide_type", slide.get("metadata", {}).get("layout", "content")),
                        html_content=slide.get("html", ""),
                        dsl_json=json.dumps(dsl_data, ensure_ascii=False) if dsl_data else None,
                        created_at=datetime.utcnow(),
                    )
                    fresh_db.add(slide_data)

                assistant_msg = Message(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    role="assistant",
                    content="문서가 성공적으로 생성되었습니다.",
                    generation_job_id=job_id,
                    created_at=datetime.utcnow(),
                )
                fresh_db.add(assistant_msg)
                await fresh_db.commit()

            # Cleanup intermediate HTML preview file (final PPTX is the deliverable)
            html_preview_path = final_state.get("html_preview_path")
            if html_preview_path:
                try:
                    import os
                    if os.path.exists(html_preview_path):
                        os.unlink(html_preview_path)
                except OSError:
                    pass

            yield {
                "event": "complete",
                "data": json.dumps({
                    "output_path": output_path,
                    "fidelity_scores": final_state.get("fidelity_scores", []),
                    "qa_iterations": final_state.get("qa_iterations", 0),
                    "job_id": job_id,
                }),
            }
        except Exception as e:
            logger.error(
                "stream_generation.error",
                error=str(e),
                session_id=session_id,
                traceback=traceback.format_exc(),
            )
            yield {"event": "error", "data": json.dumps({"message": str(e)})}

    return EventSourceResponse(event_generator(), ping=5)
