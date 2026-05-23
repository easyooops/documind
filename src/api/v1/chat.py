"""Chat / session endpoints with SSE streaming."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from src.core.logging import get_logger
from src.infrastructure.database import get_session
from src.infrastructure.models import (
    GeneratedFile,
    GenerationJob,
    JobStatus,
    Message,
    Session,
    SlideData,
)
from src.schemas.api import ChatMessage, GenerateRequest, SessionResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


def _compact_text(value: object, max_len: int = 90) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _summary_text(value: object) -> str:
    if isinstance(value, dict):
        parts = []
        for key in ("title", "metric", "value", "summary", "snippet", "source", "url"):
            item = value.get(key)
            if item:
                parts.append(str(item))
        return " | ".join(parts) if parts else json.dumps(value, ensure_ascii=False)
    return str(value or "")


def _summarize_node_output(node_name: str, node_output: dict, locale: str = "ko") -> list[str]:
    """Create user-facing summaries of each agent's completed result."""
    if not isinstance(node_output, dict):
        return []

    is_en = locale == "en"

    if node_name == "research":
        raw_research = node_output.get("research_data") or {}
        research = _as_dict(raw_research)
        facts = _as_list(research.get("facts"))
        stats = _as_list(research.get("statistics"))
        search_results = _as_list(research.get("search_results"))
        sources = _as_list(research.get("sources"))
        if not research and isinstance(raw_research, list):
            facts = raw_research
        detail_items = facts[:2] or search_results[:2] or sources[:2]
        prefix = (
            f"Collected {len(search_results)} source results, then organized {len(facts)} key facts and {len(stats)} numeric data points."
            if is_en
            else f"검색 결과 {len(search_results)}건을 수집하고, 핵심 근거 {len(facts)}건과 수치 데이터 {len(stats)}건을 정리했습니다."
        )
        return [prefix, *[_compact_text(_summary_text(item)) for item in detail_items]][:4]

    if node_name == "narrative":
        plan = _as_dict(node_output.get("narrative_plan"))
        if is_en:
            return [
                f"Designed a {plan.get('total_slides', 0)}-slide narrative flow.",
                f"Story arc: {_compact_text(plan.get('narrative_arc'))}",
            ]
        return [f"{plan.get('total_slides', 0)}장 구성의 흐름을 설계했습니다.", f"전개 방식: {_compact_text(plan.get('narrative_arc'))}"]

    if node_name == "content_writer":
        slides = _as_list(node_output.get("slide_contents"))
        titles = [_compact_text(_as_dict(slide).get("title") or _summary_text(slide)) for slide in slides[:3]]
        message = (
            f"Drafted titles, body copy, and data points for {len(slides)} slides."
            if is_en
            else f"{len(slides)}개 슬라이드의 제목/본문/데이터 포인트를 작성했습니다."
        )
        return [message, *titles]

    if node_name == "audience":
        audience = _as_dict(node_output.get("audience_profile"))
        if is_en:
            return [
                f"Audience type: {_compact_text(audience.get('audience_type'))}",
                f"Tone/complexity: {_compact_text(audience.get('tone'))} / {_compact_text(audience.get('complexity'))}",
                f"Persuasion style: {_compact_text(audience.get('persuasion_style'))}",
            ]
        return [f"청중 유형: {_compact_text(audience.get('audience_type'))}", f"톤/복잡도: {_compact_text(audience.get('tone'))} / {_compact_text(audience.get('complexity'))}", f"설득 방식: {_compact_text(audience.get('persuasion_style'))}"]

    if node_name == "template_analysis":
        template = _as_dict(node_output.get("template_profile"))
        if not template:
            return ["No template was provided, so a new design system will be used."] if is_en else ["업로드된 템플릿이 없어 새 디자인 기준으로 진행합니다."]
        keyword_text = _compact_text(", ".join(str(item) for item in _as_list(template.get("design_keywords"))[:5]))
        return ["Extracted template colors, layouts, and visual keywords.", f"Keywords: {keyword_text}"] if is_en else ["템플릿 색상/레이아웃/시각 키워드를 추출했습니다.", f"키워드: {keyword_text}"]

    if node_name == "layout_compose":
        layouts = _as_list(node_output.get("layout_specs"))
        types = sorted({_as_dict(layout).get("grid_type", "custom") for layout in layouts})
        return [f"Defined spatial layouts for {len(layouts)} slides.", f"Layouts: {_compact_text(', '.join(types))}"] if is_en else [f"{len(layouts)}개 슬라이드의 공간 배치를 정의했습니다.", f"사용 레이아웃: {_compact_text(', '.join(types))}"]

    if node_name == "style_direct":
        design = _as_dict(node_output.get("design_system"))
        colors = _as_dict(design.get("color_tokens"))
        typography = _as_list(design.get("typography_scale"))
        color_text = _compact_text(", ".join(str(v) for v in list(colors.values())[:4]))
        if is_en:
            return ["Created the deck-wide design system.", f"Key colors: {color_text}", f"Typography scale: {len(typography)} levels"]
        return ["전체 슬라이드에 적용할 디자인 시스템을 생성했습니다.", f"주요 색상: {color_text}", f"타이포그래피 단계: {len(typography)}개"]

    if node_name == "asset_plan":
        assets = _as_list(node_output.get("asset_requirements"))
        asset_types = sorted({_as_dict(asset).get("asset_type", "visual") for asset in assets})
        return [f"Planned {len(assets)} visual assets.", f"Types: {_compact_text(', '.join(asset_types))}"] if is_en else [f"{len(assets)}개 시각 요소를 계획했습니다.", f"유형: {_compact_text(', '.join(asset_types))}"]

    if node_name == "code_generate":
        slides = _as_list(node_output.get("slides_dsl"))
        return [f"Generated {len(slides)} slide drafts."] if is_en else [f"{len(slides)}개 슬라이드 초안을 생성했습니다."]

    if node_name == "consistency_check":
        report = _as_dict(node_output.get("consistency_report"))
        issues = _as_list(report.get("issues"))
        if report.get("is_consistent", True):
            return ["Checked cross-slide consistency for color, type, spacing, and alignment."] if is_en else ["색상, 폰트, 간격, 정렬의 슬라이드 간 일관성을 확인했습니다."]
        return [f"Found {len(issues)} consistency issues.", *[_compact_text(_summary_text(i)) for i in issues[:3]]] if is_en else [f"일관성 이슈 {len(issues)}건을 발견했습니다.", *[_compact_text(_summary_text(i)) for i in issues[:3]]]

    if node_name == "validate":
        result = _as_dict(node_output.get("validation_result"))
        issues = _as_list(result.get("issues"))
        score = result.get("overall_score")
        status = "passed" if result.get("passed") else "needs refinement"
        if is_en:
            return [f"Pre-export quality gate: {status}" + (f" (score {score:.1f})" if isinstance(score, (int, float)) else ""), *[_compact_text(_summary_text(issue)) for issue in issues[:3]]]
        status = "통과" if result.get("passed") else "보완 필요"
        return [f"생성 전 품질 기준: {status}" + (f" (점수 {score:.1f})" if isinstance(score, (int, float)) else ""), *[_compact_text(_summary_text(issue)) for issue in issues[:3]]]

    if node_name == "convert":
        path = node_output.get("output_path")
        return ["Completed PowerPoint file conversion." + (f" ({path})" if path else "")] if is_en else ["PowerPoint 파일 변환을 완료했습니다." + (f" ({path})" if path else "")]

    if node_name == "qa_critic":
        scores = _as_list(node_output.get("fidelity_scores"))
        feedback = _as_dict(node_output.get("qa_feedback"))
        issues = _as_list(feedback.get("issues"))
        latest = scores[-1] if scores else None
        if is_en:
            return ["Completed final quality assessment." + (f" (score {latest:.2f})" if isinstance(latest, (int, float)) else ""), *[_compact_text(_summary_text(issue)) for issue in issues[:3]]]
        return ["최종 품질 평가를 완료했습니다." + (f" (점수 {latest:.2f})" if isinstance(latest, (int, float)) else ""), *[_compact_text(_summary_text(issue)) for issue in issues[:3]]]

    if node_name == "export":
        return ["Final file is ready to export."] if is_en else ["최종 파일을 내보낼 준비를 완료했습니다."]

    return []


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
        from src.utils.language import detect_output_language

        request_options = _as_dict(request.options)

        initial_state: DocuMindState = {
            "user_query": request.query,
            "session_id": session_id,
            "template_id": request.template_id,
            "conversation_history": [],
            "document_format": request.format,
            "locale": str(request_options.get("locale", "ko")),
            "output_language": detect_output_language(request.query),
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

        locale = str(request_options.get("locale", "ko"))
        NODE_DESCRIPTIONS_BY_LOCALE = {
            "ko": {
                "research": "웹 리서치 및 데이터 수집",
                "narrative": "내러티브 구조 설계",
                "content_writer": "슬라이드 콘텐츠 작성",
                "audience": "청중 분석 및 톤 결정",
                "template_analysis": "템플릿 분석",
                "layout_compose": "레이아웃 구성",
                "style_direct": "디자인 시스템 생성",
                "asset_plan": "비주얼 에셋 계획",
                "code_generate": "슬라이드 생성",
                "consistency_check": "일관성 검증",
                "validate": "생성 전 구조/품질 검증",
                "convert": "PPTX 파일 변환",
                "qa_critic": "품질 평가 (QA)",
                "export": "최종 내보내기",
            },
            "en": {
                "research": "Web Research and Data Collection",
                "narrative": "Narrative Structure",
                "content_writer": "Slide Content Writing",
                "audience": "Audience and Tone Analysis",
                "template_analysis": "Template Analysis",
                "layout_compose": "Layout Composition",
                "style_direct": "Design System Creation",
                "asset_plan": "Visual Asset Planning",
                "code_generate": "Slide Generation",
                "consistency_check": "Consistency Check",
                "validate": "Pre-Export Structure and Quality Check",
                "convert": "PowerPoint File Conversion",
                "qa_critic": "Quality Assessment (QA)",
                "export": "Final Export",
            },
        }
        NODE_DESCRIPTIONS = NODE_DESCRIPTIONS_BY_LOCALE.get(locale, NODE_DESCRIPTIONS_BY_LOCALE["ko"])

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

                    event_data = _as_dict(event.get("data"))
                    node_output = event_data.get("output", {})
                    has_errors = bool(
                        node_output.get("errors")
                        if isinstance(node_output, dict) else False
                    )
                    if isinstance(node_output, dict):
                        final_state = {**final_state, **node_output}

                    try:
                        summary_items = _summarize_node_output(node_name, node_output, locale)
                    except Exception as exc:
                        logger.warning(
                            "stream_generation.summary_failed",
                            node=node_name,
                            error_type=type(exc).__name__,
                            error=str(exc)[:300],
                        )
                        summary_items = []

                    yield {
                        "event": "node_complete",
                        "data": json.dumps({
                            "node": node_name,
                            "phase": phase,
                            "description": description,
                            "summary_items": summary_items,
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
                                "phase": NODE_PHASE_MAP.get(active_node, current_phase),
                                "activity": "llm_call_start",
                                "description": f"{description} - LLM 호출 중",
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
                                "phase": NODE_PHASE_MAP.get(active_node, current_phase),
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
                            "phase": NODE_PHASE_MAP.get(active_node, current_phase),
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
                slides_html = _as_list(final_state.get("slides_html"))
                slides_dsl = _as_list(final_state.get("slides_dsl"))
                dsl_by_index = {
                    dsl.get("index"): dsl
                    for dsl in (_as_dict(item) for item in slides_dsl)
                    if dsl.get("index") is not None
                }
                for slide_item in slides_html:
                    slide = _as_dict(slide_item)
                    if not slide:
                        logger.warning(
                            "stream_generation.skip_invalid_slide_html",
                            slide_type=type(slide_item).__name__,
                        )
                        continue
                    idx = slide.get("index", 0)
                    metadata = _as_dict(slide.get("metadata"))
                    dsl_data = dsl_by_index.get(idx)
                    slide_data = SlideData(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        slide_number=idx,
                        slide_type=metadata.get("slide_type", metadata.get("layout", "content")),
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
                error_type=type(e).__name__,
                error=str(e)[:500],
                session_id=session_id,
            )
            yield {"event": "error", "data": json.dumps({"message": str(e)})}

    return EventSourceResponse(event_generator(), ping=5)
