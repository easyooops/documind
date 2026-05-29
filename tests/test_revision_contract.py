from types import SimpleNamespace

from src.api.v1.chat import _requested_base_version_number
from src.formats.rich_document.spec import (
    is_document_replacement_request,
    merge_revision_spec,
    revision_guidance,
)


def test_natural_language_base_version_overrides_latest_selection() -> None:
    versions = [
        SimpleNamespace(version_number=3),
        SimpleNamespace(version_number=2),
        SimpleNamespace(version_number=1),
    ]

    assert _requested_base_version_number(
        "최초 생성 문서 기준으로 문구를 수정해줘",
        3,
        versions,
    ) == 1
    assert _requested_base_version_number("v2 기준으로 다시 수정", 3, versions) == 2
    assert _requested_base_version_number("최신 버전 기준으로 수정", 1, versions) == 3


def test_rich_document_replacement_request_replaces_sections() -> None:
    base = {
        "title": "기존 문서",
        "executive_summary": "기존 요약",
        "metadata": [],
        "sections": [
            {"title": "아키텍처", "blocks": [{"type": "paragraph", "text": "기존 아키텍처"}]},
            {"title": "로드맵", "blocks": [{"type": "paragraph", "text": "기존 로드맵"}]},
        ],
    }
    revised = {
        "title": "일반 보고서",
        "executive_summary": "새 요약",
        "metadata": [],
        "sections": [
            {"title": "개요", "blocks": [{"type": "paragraph", "text": "일반 내용"}]},
            {"title": "핵심 내용", "blocks": [{"type": "paragraph", "text": "수정 반영"}]},
        ],
    }

    merged = merge_revision_spec(base, revised, replace_all_sections=True)

    assert [section["title"] for section in merged["sections"]] == ["개요", "핵심 내용"]
    assert "아키텍처" not in {section["title"] for section in merged["sections"]}


def test_replacement_guidance_detects_user_change_priority() -> None:
    query = "전체적으로 아키텍처 말고 일반 내용으로 변경해줘"

    assert is_document_replacement_request(query) is True
    assert "USER CHANGE OVERRIDES PRESERVATION" in revision_guidance(query)
