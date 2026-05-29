"""Chat / session endpoints with SSE streaming."""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from src.core.logging import get_logger
from src.infrastructure.database import get_session
from src.infrastructure.models import (
    DocumentVersion,
    GeneratedFile,
    GenerationJob,
    ImageAttachment,
    JobStatus,
    Message,
    Session,
    SlideData,
    SlideVersion,
    Template,
)
from src.schemas.api import ChatMessage, GenerateRequest, ImageAttachmentResponse, SessionResponse

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


def _requested_base_version_number(
    query: str,
    selected_version_number: object,
    versions: list[DocumentVersion],
) -> int | None:
    """Resolve the parent version, giving explicit natural-language requests priority."""
    import re

    text = str(query or "").lower()
    explicit = re.search(
        r"(?:\bv\s*|version\s*|버전\s*)(\d{1,3})|(\d{1,3})\s*버전",
        text,
        re.IGNORECASE,
    )
    if explicit:
        return int(next(group for group in explicit.groups() if group))

    first_signals = (
        "최초", "처음 생성", "처음 만든", "첫 생성", "원본", "초안", "최초 생성 문서",
        "original", "initial", "first version", "first generated", "base document",
    )
    if any(signal in text for signal in first_signals):
        return 1

    latest_signals = ("최신", "latest", "last version", "최근 버전")
    if any(signal in text for signal in latest_signals):
        return versions[0].version_number if versions else None

    try:
        return int(selected_version_number) if selected_version_number is not None else None
    except (TypeError, ValueError):
        return None


def _mime_type_for_format(format_id: str) -> str:
    return {
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "md": "text/markdown",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "hwp": "application/hwp+zip",
    }.get(format_id, "application/octet-stream")


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

    # v2 pipeline nodes
    if node_name == "init_context":
        return ["Initialized design context and constraints."] if is_en else ["디자인 컨텍스트 및 제약 조건을 초기화했습니다."]

    if node_name == "plan":
        blueprints = _as_list(node_output.get("slide_blueprints"))
        title = node_output.get("title", "")
        design = _as_dict(node_output.get("design_system"))
        items = []
        items.append(
            f"Planned {len(blueprints)} slides: \"{_compact_text(title)}\""
            if is_en else
            f"{len(blueprints)}장 슬라이드를 설계했습니다: \"{_compact_text(title)}\""
        )
        items.append("── 콘텐츠 ──" if not is_en else "── Content ──")
        for bp in blueprints[:6]:
            bp_dict = _as_dict(bp)
            s_type = bp_dict.get("slide_type", "content")
            s_title = _compact_text(bp_dict.get("title", ""))
            s_key = _compact_text(bp_dict.get("key_message", ""))
            elements = bp_dict.get("suggested_elements", [])
            el_text = f" — {', '.join(elements[:3])}" if elements else ""
            line = f"  {bp_dict.get('index', '?')}. [{s_type}] {s_title}"
            if s_key:
                line += f" — {s_key}"
            line += el_text
            items.append(line)
        if design:
            colors = f"{design.get('primary', '')} / {design.get('accent', '')} / {design.get('background', '')}"
            font = design.get("font_heading", "Pretendard")
            items.append("── 디자인 ──" if not is_en else "── Design ──")
            items.append(f"  팔레트: {colors} | 폰트: {font}" if not is_en else f"  Palette: {colors} | Font: {font}")
        layout_hints = [f"{_as_dict(bp).get('slide_type', '?')}:{_as_dict(bp).get('layout_hint', '?')}" for bp in blueprints[:6]]
        if layout_hints:
            items.append("── 레이아웃 ──" if not is_en else "── Layout ──")
            items.append(f"  {', '.join(layout_hints)}")
        return items

    if node_name == "generate_html":
        slides = _as_list(node_output.get("slides_html"))
        usage = _as_dict(node_output.get("element_usage"))
        used = _as_list(usage.get("used"))
        if is_en:
            return [f"Generated HTML for {len(slides)} slides.", f"Elements used: {_compact_text(', '.join(used[:6]))}"]
        return [f"{len(slides)}개 슬라이드 HTML을 생성했습니다.", f"사용 요소: {_compact_text(', '.join(used[:6]))}"]

    if node_name == "init_document_context":
        return (
            ["Initialized native-format rules and document context."]
            if is_en
            else ["\ubb38\uc11c \uc11c\uc2dd \uaddc\uce59\uacfc \uc791\uc131 \ud658\uacbd\uc744 \uc900\ube44\ud588\uc2b5\ub2c8\ub2e4."]
        )

    if node_name == "interpret_request":
        intent = _as_dict(node_output.get("document_intent"))
        if is_en:
            return [
                f"Document type: {_compact_text(intent.get('template_family'))}",
                f"Template market: {_compact_text(intent.get('locale_market'))} / "
                f"{_compact_text(intent.get('institutional_style'))}",
            ]
        return [
            f"\ubb38\uc11c \uc720\ud615: {_compact_text(intent.get('template_family'))}",
            f"\uc11c\uc2dd \uae30\uc900: {_compact_text(intent.get('locale_market'))} / "
            f"{_compact_text(intent.get('institutional_style'))}",
        ]

    if node_name == "template_design":
        design = _as_dict(node_output.get("design_system"))
        if is_en:
            return [
                f"Designed native template: {_compact_text(design.get('template_name'))}",
                f"Design rationale: {_compact_text(design.get('design_rationale'))}",
            ]
        return [
            f"\uc801\uc6a9 \uc11c\uc2dd: {_compact_text(design.get('template_name'))}",
            f"\uc11c\uc2dd \uae30\uc900: {_compact_text(design.get('design_rationale'))}",
        ]

    if node_name == "document_plan":
        spec = _as_dict(node_output.get("document_spec"))
        sections = _as_list(spec.get("sections"))
        message = (
            f"Planned {len(sections)} designed document sections."
            if is_en
            else f"\ubb38\uc11c \uad6c\uc131 {len(sections)}\uac1c \ud56d\ubaa9\uc744 \uacc4\ud68d\ud588\uc2b5\ub2c8\ub2e4."
        )
        return [message, *[_compact_text(_as_dict(section).get("title")) for section in sections[:3]]]

    if node_name == "native_render":
        return (
            [f"Rendered native document file: {node_output.get('output_path', '')}"]
            if is_en
            else ["\uc6cc\ub4dc \ubb38\uc11c \ud30c\uc77c \uc0dd\uc131\uc744 \uc644\ub8cc\ud588\uc2b5\ub2c8\ub2e4."]
        )

    if node_name == "quality_evaluate":
        feedback = _as_dict(node_output.get("qa_feedback"))
        score = feedback.get("score")
        if not isinstance(score, (int, float)):
            return []
        return (
            [f"Format-specific quality score: {score:.0%}"]
            if is_en
            else [f"\uc11c\uc2dd \ubc0f \ud488\uc9c8 \uc810\uac80 \uc810\uc218: {score:.0%}"]
        )

    if node_name == "render_convert":
        screenshots = node_output.get("screenshots_count", 0)
        if is_en:
            return [
                "Rendered and converted to PowerPoint."
                + (f" ({screenshots} screenshots)" if screenshots else "")
            ]
        return [
            "PowerPoint로 렌더링 및 변환을 완료했습니다."
            + (f" (스크린샷 {screenshots}장)" if screenshots else "")
        ]

    if node_name in {"quality_assessment", "vlm_qa"}:
        scores = _as_list(
            node_output.get("fidelity_scores") or node_output.get("rule_based_scores")
        )
        feedback = _as_dict(node_output.get("qa_feedback"))
        passed = feedback.get("passed", False)
        latest = scores[-1] if scores else None
        issues = list(
            dict.fromkeys(
                _as_list(feedback.get("fix_instructions"))
                + _as_list(feedback.get("issues"))
            )
        )
        category_scores = _as_dict(feedback.get("category_scores"))
        status = "passed" if passed else "refinement needed"
        status_ko = "\ud1b5\uacfc" if passed else "\ubcf4\uc644 \ud544\uc694"
        rule_score = feedback.get("rule_score")
        visual_score = feedback.get("visual_score")
        evaluated = feedback.get("evaluated_slide_count")
        total = feedback.get("total_slide_count")
        per_slide = _as_list(feedback.get("visual_per_slide"))
        items = []
        if is_en:
            items.append(f"Quality assessment: {status}" + (f" ({latest:.2f})" if isinstance(latest, (int, float)) else ""))
        else:
            items.append(f"\ud488\uc9c8 \ud3c9\uac00: {status_ko}" + (f" ({latest:.2f})" if isinstance(latest, (int, float)) else ""))
        if isinstance(rule_score, (int, float)) and isinstance(visual_score, (int, float)):
            if is_en:
                coverage = (
                    f", {evaluated}/{total} slides"
                    if isinstance(evaluated, int) and isinstance(total, int) else ""
                )
                items.append(f"Parallel slide QA: rule {rule_score:.2f} | VLM {visual_score:.2f}{coverage}")
            else:
                coverage = (
                    f" ({evaluated}/{total} \uc2ac\ub77c\uc774\ub4dc)"
                    if isinstance(evaluated, int) and isinstance(total, int) else ""
                )
                items.append(f"\uc804\uccb4 \uc2ac\ub77c\uc774\ub4dc \ubcd1\ub82c \ud3c9\uac00: \uaddc\uce59 {rule_score:.2f} | VLM {visual_score:.2f}{coverage}")
        if per_slide:
            slide_parts = []
            for result in per_slide[:6]:
                result_dict = _as_dict(result)
                score = result_dict.get("score")
                if not isinstance(score, (int, float)):
                    continue
                label = result_dict.get("index", "?")
                verdict = "pass" if result_dict.get("passed") else "review"
                verdict_ko = "\ud1b5\uacfc" if result_dict.get("passed") else "\uac80\ud1a0"
                slide_parts.append(
                    f"S{label}: {score:.2f} {verdict if is_en else verdict_ko}"
                )
            if slide_parts:
                prefix = "Slide scores" if is_en else "\uc2ac\ub77c\uc774\ub4dc\ubcc4 \uc810\uc218"
                items.append(f"{prefix}: " + " | ".join(slide_parts))
        if category_scores:
            cat_parts = []
            cat_labels = {
                "layout_compliance": "Layout" if is_en else "레이아웃",
                "typography_compliance": "Typo" if is_en else "타이포",
                "color_compliance": "Color" if is_en else "색상",
                "element_completeness": "Elements" if is_en else "요소",
                "visual_balance": "Balance" if is_en else "밸런스",
                "ooxml_validity": "OOXML",
            }
            for cat, label in cat_labels.items():
                val = category_scores.get(cat)
                if isinstance(val, (int, float)):
                    cat_parts.append(f"{label}: {val:.2f}")
            if cat_parts:
                items.append("  " + " | ".join(cat_parts))
        for issue in issues[:2]:
            items.append(f"  - {_compact_text(issue)}")
        return items

    return []


async def _analyze_image_intent(query: str, attachments: list[dict], format_id: str) -> dict:
    """Interpret user-provided reference images together with their instruction."""
    import base64

    from langchain_core.messages import HumanMessage, SystemMessage

    from src.agents.loader import get_llm_for_agent
    from src.infrastructure.storage import create_storage_backend
    from src.utils.json_repair import parse_llm_json

    content: list[dict] = [{
        "type": "text",
        "text": (
            "Analyze the attached image(s) as evidence for the user's document request. "
            "Identify usable content, facts, fields, or explicitly requested visual constraints. "
            "Do not treat the image as the document template unless the user explicitly asks to "
            "copy its layout or style. For reports, the document pipeline must independently "
            "select an appropriate report template for the user's language and institutional "
            "context. Return JSON with keys summary, visible_evidence, requested_changes, "
            "style_constraints, and content_inputs.\n\n"
            f"Target output format: {format_id}\n"
            f"User request: {query or 'Interpret the attached reference image.'}"
        ),
    }]

    try:
        storage = create_storage_backend()
        for attachment in attachments:
            image_bytes = await storage.load(attachment["file_path"])
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{attachment['mime_type']};base64,{encoded}",
                },
            })
        llm = get_llm_for_agent("vlm_qa", format_id="pptx")
        response = await llm.ainvoke([
            SystemMessage(content="You are a precise visual-evidence analyst for document generation."),
            HumanMessage(content=content),
        ])
        result = parse_llm_json(response.content)
        if isinstance(result, dict):
            return result
        return {"summary": str(result), "requested_changes": []}
    except Exception as exc:
        logger.warning("chat.image_intent_failed", error=str(exc)[:200])
        return {
            "summary": "Attached image supplied as visual reference.",
            "requested_changes": [],
        }


class CreateSessionRequest(BaseModel):
    user_id: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str


@router.post("/images/upload", response_model=ImageAttachmentResponse)
async def upload_message_image(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
):
    """Store an image that can be submitted with a chat generation request."""
    from PIL import Image

    filename = Path(file.filename or "image").name
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image is too large (max 10MB)")

    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
        with Image.open(BytesIO(content)) as image:
            width, height = image.size
            image_format = (image.format or "").lower()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image file") from exc

    if image_format not in {"png", "jpeg", "jpg", "webp", "gif"}:
        raise HTTPException(status_code=400, detail="Only PNG, JPEG, WEBP, or GIF images are supported")

    content_type = f"image/{'jpeg' if image_format == 'jpg' else image_format}"
    attachment_id = str(uuid.uuid4())
    storage_path = f"chat-images/{attachment_id}/{filename}"
    from src.infrastructure.storage import create_storage_backend

    await create_storage_backend().save(content, storage_path, content_type)
    attachment = ImageAttachment(
        id=attachment_id,
        filename=filename,
        file_path=storage_path,
        mime_type=content_type,
        size_bytes=len(content),
        width=width,
        height=height,
        created_at=datetime.utcnow(),
    )
    db.add(attachment)
    return ImageAttachmentResponse(
        id=attachment.id,
        filename=attachment.filename,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        width=attachment.width,
        height=attachment.height,
        created_at=attachment.created_at,
    )


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


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    request: UpdateSessionRequest,
    db: AsyncSession = Depends(get_session),
):
    """Rename a conversation session."""
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Session title cannot be empty")
    if len(title) > 120:
        raise HTTPException(status_code=400, detail="Session title is too long")

    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.title = title
    session.updated_at = datetime.utcnow()
    logger.info("chat.update_session", session_id=session_id)
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Delete a conversation session and its message history."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    jobs_result = await db.execute(
        select(GenerationJob).where(GenerationJob.session_id == session_id)
    )
    for job in jobs_result.scalars():
        job.session_id = None

    await db.delete(session)
    logger.info("chat.delete_session", session_id=session_id)
    return Response(status_code=204)


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

    from sqlalchemy.orm import selectinload

    request_options = _as_dict(request.options)
    attachment_ids = list(dict.fromkeys(request.image_attachment_ids))[:4]
    attachments: list[ImageAttachment] = []
    if attachment_ids:
        attachment_result = await db.execute(
            select(ImageAttachment).where(ImageAttachment.id.in_(attachment_ids))
        )
        attachment_by_id = {item.id: item for item in attachment_result.scalars().all()}
        if len(attachment_by_id) != len(attachment_ids):
            raise HTTPException(status_code=404, detail="One or more image attachments were not found")
        attachments = [attachment_by_id[attachment_id] for attachment_id in attachment_ids]

    document = None
    if session.document_id:
        document_result = await db.execute(
            select(GenerationJob).where(GenerationJob.id == session.document_id)
        )
        document = document_result.scalar_one_or_none()

    if document and request.template_id:
        raise HTTPException(
            status_code=409,
            detail="A template can be attached only when the document is first created",
        )
    if document and document.format != request.format:
        raise HTTPException(
            status_code=409,
            detail="The output format cannot be changed within an existing document session",
        )

    if not document:
        if request.template_id:
            template_result = await db.execute(
                select(Template).where(Template.id == request.template_id)
            )
            if not template_result.scalar_one_or_none():
                raise HTTPException(status_code=404, detail="Template not found")
        document = GenerationJob(
            id=str(uuid.uuid4()),
            session_id=session_id,
            template_id=request.template_id,
            query=request.query,
            format=request.format,
            options=request_options,
            status=JobStatus.PROCESSING.value,
            phase="planning",
            progress=0.0,
            started_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        db.add(document)
        session.document_id = document.id

    versions_result = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .options(selectinload(DocumentVersion.slide_versions))
        .order_by(DocumentVersion.version_number.desc())
    )
    versions = list(versions_result.scalars().all())
    latest_version = versions[0] if versions else None
    selected_version_number = _requested_base_version_number(
        request.query,
        request_options.get("base_version_number"),
        versions,
    )
    base_version = next(
        (version for version in versions if version.version_number == selected_version_number),
        latest_version,
    )
    if versions and selected_version_number and base_version.version_number != selected_version_number:
        logger.warning(
            "chat.base_version_not_found_fallback_latest",
            requested=selected_version_number,
            latest=latest_version.version_number if latest_version else None,
        )
    version_number = (latest_version.version_number + 1) if latest_version else 1

    template_bytes = None
    template_analysis: dict = {}
    template_filename = "template.pptx"
    if document.template_id:
        template_result = await db.execute(select(Template).where(Template.id == document.template_id))
        template = template_result.scalar_one_or_none()
        if template:
            from src.infrastructure.storage import create_storage_backend

            template_bytes = await create_storage_backend().load(template.file_path)
            template_filename = template.filename
            template_analysis = _as_dict(template.analysis)
            if Path(template.filename).suffix.lower() in {".docx", ".hwpx", ".xlsx", ".md", ".pdf"}:
                from src.formats.rich_document.template_analysis import analyze_template

                template_analysis = {
                    **template_analysis,
                    **analyze_template(template_bytes, template.filename),
                }

    base_pipeline_data = _as_dict(base_version.pipeline_data) if base_version else {}
    base_slides_html = [
        {
            "index": slide.slide_index,
            "html": slide.html,
            "elements_used": _as_list(_as_dict(slide.content).get("elements_used")),
        }
        for slide in (base_version.slide_versions if base_version else [])
    ]

    user_msg = Message(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role="user",
        content=(
            request.query
            + (
                "\n[Attached images: "
                + ", ".join(attachment.filename for attachment in attachments)
                + "]"
                if attachments else ""
            )
        ),
        created_at=datetime.utcnow(),
    )
    db.add(user_msg)
    for attachment in attachments:
        attachment.message_id = user_msg.id

    attachment_payload = [
        {
            "id": attachment.id,
            "filename": attachment.filename,
            "file_path": attachment.file_path,
            "mime_type": attachment.mime_type,
        }
        for attachment in attachments
    ]

    if not session.title:
        session.title = request.query[:80]
        session.updated_at = datetime.utcnow()

    await db.commit()

    async def event_generator():
        from src.agents.research_intent import analyze_research_intent
        from src.engine import _get_format_pipeline
        from src.infrastructure.database import get_session_factory
        from src.schemas.agents import DocuMindState
        from src.utils.language import detect_output_language

        locale = str(request_options.get("locale", "ko"))
        yield {"event": "phase_start", "data": json.dumps({"phase": "planning"})}

        active_template_analysis = dict(template_analysis)
        stored_visual_analysis = _as_dict(active_template_analysis.get("visual_analysis"))
        should_analyze_template = (
            document.format == "pptx"
            and bool(document.template_id and template_bytes)
            and not base_version
            and stored_visual_analysis.get("status", "pending") == "pending"
        )
        if should_analyze_template:
            description = (
                "템플릿 렌더 이미지 시각 분석"
                if locale != "en"
                else "Rendered template visual analysis"
            )
            yield {
                "event": "node_start",
                "data": json.dumps({
                    "node": "template_visual_analysis",
                    "phase": "designing",
                    "description": description,
                }),
            }
            try:
                from src.formats.pptx.template_visual import analyze_template_visuals

                active_template_analysis["visual_analysis"] = await asyncio.wait_for(
                    analyze_template_visuals(
                        template_bytes,
                        template_filename,
                        active_template_analysis,
                    ),
                    timeout=120,
                )
            except TimeoutError:
                logger.warning("template.visual_analysis_timeout", template_id=document.template_id)
                active_template_analysis["visual_analysis"] = {
                    "status": "timeout",
                    "summary": "Visual template analysis timed out; OOXML profile will be used.",
                }
            except Exception as exc:
                logger.warning(
                    "template.visual_analysis_generation_failed",
                    template_id=document.template_id,
                    error=str(exc)[:200],
                )
                active_template_analysis["visual_analysis"] = {
                    "status": "failed",
                    "summary": "Visual template analysis failed; OOXML profile will be used.",
                }

            async with get_session_factory()() as fresh_db:
                template_row = await fresh_db.get(Template, document.template_id)
                if template_row:
                    template_row.analysis = active_template_analysis
                    await fresh_db.commit()

            visual_analysis = _as_dict(active_template_analysis.get("visual_analysis"))
            yield {
                "event": "node_complete",
                "data": json.dumps({
                    "node": "template_visual_analysis",
                    "phase": "designing",
                    "description": description,
                    "summary_items": [
                        str(visual_analysis.get("summary", "Template visual analysis completed."))
                    ],
                    "has_errors": visual_analysis.get("status") != "analyzed",
                    "progress": 0.04,
                    "elapsed_seconds": 0,
                }),
            }

        visual_intent = {}
        if attachment_payload:
            image_node_description = (
                "이미지와 요청 의도 분석"
                if str(request_options.get("locale", "ko")) != "en"
                else "Image and request intent analysis"
            )
            yield {
                "event": "node_start",
                "data": json.dumps({
                    "node": "image_intent",
                    "phase": "planning",
                    "description": image_node_description,
                }),
            }
            visual_intent = await _analyze_image_intent(
                request.query, attachment_payload, request.format
            )
            async with get_session_factory()() as fresh_db:
                image_rows = await fresh_db.execute(
                    select(ImageAttachment).where(ImageAttachment.id.in_(attachment_ids))
                )
                for image in image_rows.scalars().all():
                    image.analysis = visual_intent
                await fresh_db.commit()
            yield {
                "event": "node_complete",
                "data": json.dumps({
                    "node": "image_intent",
                    "phase": "planning",
                    "description": image_node_description,
                    "summary_items": [str(visual_intent.get("summary", "Image intent analyzed."))],
                    "has_errors": False,
                    "progress": 0.05,
                    "elapsed_seconds": 0,
                }),
            }

        intent_description = (
            "\uc694\uccad \uc758\ub3c4 \ubd84\uc11d"
            if locale != "en"
            else "Request intent analysis"
        )
        intent_started_at = time.time()
        yield {
            "event": "node_start",
            "data": json.dumps({
                "node": "research_intent",
                "phase": "planning",
                "description": intent_description,
            }),
        }
        research_intent = await analyze_research_intent(request.query)
        logger.info(
            "chat.research_intent",
            needs_research=research_intent.needs_research,
            intent=research_intent.intent_label,
            reason=research_intent.reason,
        )
        yield {
            "event": "node_complete",
            "data": json.dumps({
                "node": "research_intent",
                "phase": "planning",
                "description": intent_description,
                "summary_items": [research_intent.reason],
                "has_errors": False,
                "progress": 0.1,
                "elapsed_seconds": round(time.time() - intent_started_at, 1),
            }),
        }
        initial_state: DocuMindState = {
            "user_query": request.query,
            "session_id": session_id,
            "template_id": document.template_id,
            "conversation_history": [],
            "document_format": request.format,
            "locale": str(request_options.get("locale", "ko")),
            "output_language": detect_output_language(request.query),
            "needs_research": research_intent.needs_research,
            "template_provided": document.template_id is not None,
            "current_phase": "planning",
            "errors": [],
            "retry_count": 0,
            "qa_iterations": 0,
            "slides_dsl": [],
            "_template_bytes": template_bytes,
            "_template_filename": template_filename,
            "_template_analysis": active_template_analysis,
            "_locked_master_context": base_pipeline_data.get("master_context", {}) if base_version else {},
            "_locked_design_system": base_version.design_system or {} if base_version else {},
            "_base_version": {
                "version_number": base_version.version_number,
                "slide_plan": base_version.slide_plan or [],
            } if base_version else {},
            "_base_slides_html": base_slides_html,
            "_base_document_spec": base_pipeline_data.get("document_spec", {}) if base_version else {},
            "visual_intent": visual_intent,
            "image_attachment_ids": attachment_ids,
        }

        NODE_PHASE_MAP = {  # noqa: N806 - node constants are local to the stream lifecycle.
            # v2 pipeline nodes
            "init_context": "planning",
            "research": "planning",
            "plan": "planning",
            "generate_html": "generating",
            "render_convert": "converting",
            "quality_assessment": "qa",
            "design_evaluate": "qa",
            "export": "exporting",
            "init_document_context": "planning",
            "interpret_request": "planning",
            "template_design": "designing",
            "document_plan": "planning",
            "native_render": "converting",
            "quality_evaluate": "qa",
            "export_document": "exporting",
            # v1 pipeline nodes (legacy)
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
            "vlm_qa": "qa",
        }

        NODE_DESCRIPTIONS_BY_LOCALE = {  # noqa: N806 - node constants are local to the stream lifecycle.
            "ko": {
                # v2 pipeline nodes
                "init_context": "컨텍스트 초기화",
                "research": "웹 리서치 및 데이터 수집",
                "plan": "슬라이드 구조 및 디자인 계획",
                "generate_html": "슬라이드 HTML 생성",
                "render_convert": "PPTX 렌더링 및 변환",
                "quality_assessment": "\ud488\uc9c8 \ud3c9\uac00",
                "design_evaluate": "디자인 품질 평가",
                "export": "최종 내보내기",
                # v1 pipeline nodes (legacy)
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
                "vlm_qa": "시각 품질 검증",
            },
            "en": {
                # v2 pipeline nodes
                "init_context": "Context Initialization",
                "research": "Web Research and Data Collection",
                "plan": "Slide Structure and Design Planning",
                "generate_html": "Slide HTML Generation",
                "render_convert": "PPTX Rendering and Conversion",
                "quality_assessment": "Quality Assessment",
                "design_evaluate": "Design Quality Evaluation",
                "export": "Final Export",
                "init_document_context": "Native Document Context Initialization",
                "interpret_request": "Document Intent and Template Market Analysis",
                "template_design": "Template Research and Design",
                "document_plan": "Structured Document Planning",
                "native_render": "Native Document Rendering",
                "quality_evaluate": "Format-specific Quality Evaluation",
                "export_document": "Final Export",
                # v1 pipeline nodes (legacy)
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
                "vlm_qa": "Visual Quality Verification",
            },
        }
        NODE_DESCRIPTIONS_BY_LOCALE["ko"].update({
            "init_document_context": "\ubb38\uc11c \uc0dd\uc131 \ud658\uacbd \ucd08\uae30\ud654",
            "interpret_request": "\ubb38\uc11c \uc720\ud615 \ubc0f \uc11c\uc2dd \uae30\uc900 \ubd84\uc11d",
            "template_design": "\ubb38\uc11c \uc11c\uc2dd \uc801\uc6a9",
            "document_plan": "\ubb38\uc11c \ub0b4\uc6a9 \ubc0f \ud45c \uad6c\uc131 \uacc4\ud68d",
            "native_render": "\uc6cc\ub4dc \ubb38\uc11c \uc0dd\uc131",
            "quality_evaluate": "\uc11c\uc2dd \ubc0f \ud488\uc9c8 \uc810\uac80",
            "export_document": "\ucd5c\uc885 \ud30c\uc77c \ucd9c\ub825",
        })
        NODE_DESCRIPTIONS = NODE_DESCRIPTIONS_BY_LOCALE.get(  # noqa: N806
            locale, NODE_DESCRIPTIONS_BY_LOCALE["ko"]
        )

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

                elif kind == "on_chain_end" and name not in NODE_DESCRIPTIONS and name not in ("__start__", "__end__", ""):
                    event_data = _as_dict(event.get("data"))
                    node_output = event_data.get("output", {})
                    if isinstance(node_output, dict):
                        final_state = {**final_state, **node_output}

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

            # Persist a new immutable version under this session's document root.
            document_id = document.id
            output_path = final_state.get("output_path", "")
            stored_file_path = output_path or None

            async with get_session_factory()() as fresh_db:
                gen_result = await fresh_db.execute(
                    select(GenerationJob).where(GenerationJob.id == document_id)
                )
                gen_job = gen_result.scalar_one()
                gen_job.query = request.query
                gen_job.options = request_options
                gen_job.status = JobStatus.COMPLETED.value
                gen_job.phase = "done"
                gen_job.progress = 1.0
                gen_job.slide_plan = (
                    final_state.get("slide_blueprints") or final_state.get("section_blueprints")
                )
                gen_job.design_system = final_state.get("design_system")
                gen_job.completed_at = datetime.utcnow()

                if output_path:
                    import os

                    from src.infrastructure.storage import persist_local_file

                    file_path = output_path
                    mime_type = _mime_type_for_format(request.format)
                    storage_backend, stored_file_path = await persist_local_file(
                        file_path,
                        f"documents/{document_id}/v{version_number}/{os.path.basename(file_path)}",
                        mime_type,
                    )
                    file_size = 0
                    try:
                        file_size = os.path.getsize(file_path)
                    except OSError:
                        pass

                    generated_result = await fresh_db.execute(
                        select(GeneratedFile).where(GeneratedFile.job_id == document_id)
                    )
                    gen_file = generated_result.scalar_one_or_none()
                    if not gen_file:
                        gen_file = GeneratedFile(
                            id=str(uuid.uuid4()),
                            job_id=document_id,
                            filename=os.path.basename(file_path),
                            storage_backend=storage_backend,
                            storage_path=stored_file_path,
                            size_bytes=file_size,
                            mime_type=mime_type,
                            created_at=datetime.utcnow(),
                        )
                        fresh_db.add(gen_file)
                    else:
                        gen_file.filename = os.path.basename(file_path)
                        gen_file.storage_backend = storage_backend
                        gen_file.storage_path = stored_file_path
                        gen_file.size_bytes = file_size
                        gen_file.mime_type = mime_type

                fidelity_scores = _as_list(final_state.get("fidelity_scores"))
                fidelity_score = final_state.get("fidelity_score")
                if fidelity_score is None and fidelity_scores:
                    fidelity_score = fidelity_scores[-1]

                version = DocumentVersion(
                    id=str(uuid.uuid4()),
                    document_id=document_id,
                    version_number=version_number,
                    parent_version_id=base_version.id if base_version else None,
                    trigger="created" if version_number == 1 else "refinement",
                    user_instruction=request.query,
                    slide_plan=(
                        final_state.get("slide_blueprints") or final_state.get("section_blueprints")
                    ),
                    design_system=final_state.get("design_system"),
                    pipeline_data={
                        "master_context": final_state.get("master_context"),
                        "document_spec": final_state.get("document_spec"),
                        "document_intent": final_state.get("document_intent"),
                        "template_profile": final_state.get("template_profile"),
                        "template_references": final_state.get("template_references", []),
                        "format_rules": final_state.get("format_rules"),
                        "research_data": final_state.get("research_data"),
                        "slide_blueprints": final_state.get("slide_blueprints"),
                        "element_usage": final_state.get("element_usage"),
                        "qa_feedback": final_state.get("qa_feedback"),
                        "pptx_screenshots": final_state.get("pptx_screenshots", []),
                        "native_template_output": bool(document.template_id),
                        "template_id": document.template_id,
                        "image_attachment_ids": attachment_ids,
                        "visual_intent": final_state.get("visual_intent"),
                        "visual_asset_plan": final_state.get("visual_asset_plan"),
                        "visual_assets": final_state.get("visual_assets", []),
                        "parent_version_number": base_version.version_number if base_version else None,
                    },
                    file_path=stored_file_path,
                    fidelity_score=fidelity_score,
                    created_at=datetime.utcnow(),
                )
                fresh_db.add(version)
                await fresh_db.flush()

                # Save the latest convenience projection and immutable per-version HTML.
                slides_html = _as_list(final_state.get("slides_html"))
                slides_dsl = _as_list(final_state.get("slides_dsl"))
                slide_plan = _as_list(final_state.get("slide_blueprints"))
                blueprints_by_index = {
                    _as_dict(blueprint).get("index"): _as_dict(blueprint)
                    for blueprint in slide_plan
                }
                dsl_by_index = {
                    dsl.get("index"): dsl
                    for dsl in (_as_dict(item) for item in slides_dsl)
                    if dsl.get("index") is not None
                }
                from sqlalchemy import delete

                await fresh_db.execute(delete(SlideData).where(SlideData.job_id == document_id))
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
                        job_id=document_id,
                        slide_number=idx,
                        slide_type=metadata.get("slide_type", metadata.get("layout", "content")),
                        html_content=slide.get("html", ""),
                        dsl_json=json.dumps(dsl_data, ensure_ascii=False) if dsl_data else None,
                        created_at=datetime.utcnow(),
                    )
                    fresh_db.add(slide_data)
                    fresh_db.add(
                        SlideVersion(
                            id=str(uuid.uuid4()),
                            version_id=version.id,
                            slide_index=idx,
                            content={
                                "blueprint": blueprints_by_index.get(idx, {}),
                                "elements_used": slide.get("elements_used", []),
                            },
                            html=slide.get("html", ""),
                            design_spec=final_state.get("design_system"),
                            changed_from_parent=(
                                1 if version_number > 1
                                and idx in final_state.get("changed_slide_indices", []) else 0
                            ),
                            change_type="generated" if version_number == 1 else (
                                "modified" if idx in final_state.get("changed_slide_indices", []) else "unchanged"
                            ),
                            created_at=datetime.utcnow(),
                        )
                    )

                output_language = str(final_state.get("output_language") or detect_output_language(request.query))
                completion_message = (
                    f"Document v{version_number} was generated successfully."
                    if output_language == "en"
                    else f"문서 v{version_number}이(가) 성공적으로 생성되었습니다."
                )

                assistant_msg = Message(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    role="assistant",
                    content=completion_message,
                    generation_job_id=document_id,
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
                    "job_id": document_id,
                    "document_id": document_id,
                    "version_number": version_number,
                    "assistant_message": completion_message,
                }),
            }
        except Exception as e:
            logger.error(
                "stream_generation.error",
                error_type=type(e).__name__,
                error=str(e)[:500],
                session_id=session_id,
            )
            async with get_session_factory()() as fresh_db:
                prior_result = await fresh_db.execute(
                    select(DocumentVersion).where(DocumentVersion.document_id == document.id)
                )
                if not prior_result.scalars().first():
                    failed_result = await fresh_db.execute(
                        select(GenerationJob).where(GenerationJob.id == document.id)
                    )
                    failed_document = failed_result.scalar_one_or_none()
                    if failed_document:
                        failed_document.status = JobStatus.FAILED.value
                        failed_document.error = {"message": str(e), "type": type(e).__name__}
                        await fresh_db.commit()
            yield {"event": "error", "data": json.dumps({"message": str(e)})}

    return EventSourceResponse(event_generator(), ping=5)
