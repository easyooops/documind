# DocuMind SDK

API 서버 없이 Python 메서드 호출만으로 AI 문서를 생성하는 PyPI 패키지입니다.

영문 문서: [README.md](README.md)

```bash
pip install documind
```

```python
import asyncio
from documind import init, generate_document

init(
    llm_provider="openai",
    openai_api_key="sk-...",
    default_llm_model="gpt-4o",
    default_vlm_model="gpt-4o",
    storage_local_path="./outputs",
)

async def main():
    result = await generate_document(
        query="AI 문서 자동화 제안서를 만들어줘.",
        document_type="pptx",
        template="./templates/company-template.pptx",
        images=["./inputs/reference.png"],
        locale="ko",
    )
    print(result.output_path)

asyncio.run(main())
```

지원 형식: `pptx`, `docx`, `pdf`, `md`, `xlsx`, `hwp`(HWPX)

## 소스 구조

런타임 원본 소스는 저장소 루트의 `src` 디렉터리입니다. `packages/documind/src/src` 트리는 배포 직전에 `python scripts/sync_runtime.py`로 생성하는 빌드용 미러이므로 직접 수정하지 않습니다.

## 스트리밍

```python
from documind import DocuMind, GenerationRequest

engine = DocuMind(llm_provider="openai", openai_api_key="sk-...")
request = GenerationRequest(query="주간 보고서를 만들어줘.", document_type="docx")

async for event in engine.generate_stream(request):
    print(event.to_sse())
```

배포 가이드는 [PUBLISHING.ko.md](PUBLISHING.ko.md)를 참고하세요.
