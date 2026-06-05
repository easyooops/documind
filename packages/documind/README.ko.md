# DocuMind SDK

**English:** [`README.md`](README.md)

DocuMind SDK는 Python 코드에서 바로 문서를 생성하기 위한 경량 PyPI 패키지입니다.

Python 앱이나 백엔드에서 `documind`를 import해 PPTX, Word, Excel, Markdown, HWPX 파일을 생성할 수 있습니다.

## 설치

```bash
pip install documind
```

OpenAI, Bedrock, Anthropic, Gemini, Vertex AI provider 연동 의존성은 기본
설치에 포함됩니다.

## 빠른 시작

```python
import asyncio
from documind import generate_document, init

init(
    llm_provider="openai",
    openai_api_key="sk-...",
    default_llm_model="gpt-4o",
    storage_local_path="./outputs",
)

async def main():
    result = await generate_document(
        query="AI 문서 자동화 제안서를 만들어줘.",
        document_type="pptx",
        locale="ko",
    )
    print(result.output_path)

asyncio.run(main())
```

## SDK API Reference

공개 import:

```python
from documind import (
    DocuMind,
    GenerationRequest,
    GenerationResult,
    ImageAttachment,
    TemplateInput,
    configure,
    generate_document,
    init,
    stream_document,
)
```

### 엔진 설정

`init(**config)`는 프로세스 전역 기본값을 설정하고, `DocuMind(...)`에는 동일한
값을 엔진 인스턴스 단위로 전달할 수 있습니다.

| 옵션 | 타입 / 예시 | 설명 |
|------|-------------|------|
| `llm_provider` | `"openai"` | provider route. `openai`, `anthropic`, `azure`, `bedrock`, `gcp_vertex`, `gemini`, `ollama`, `vllm`, `custom` 등을 사용할 수 있습니다. |
| `use_default_models` | `True` | true면 agent들이 아래 기본 모델명을 사용합니다. |
| `default_llm_model` | `"gpt-4o"` | 텍스트 생성 모델입니다. |
| `default_vlm_model` | `"gpt-4o"` | vision 모델 설정과의 호환성을 위해 받습니다. |
| `default_image_model` | `"dall-e-3"` | 이미지 asset 생성 모델입니다. 미설정/실패 시 이미지 asset 없이 문서 생성을 계속합니다. |
| `storage_local_path` | `"./outputs"` | 생성 문서, HTML preview, 이미지 asset 저장 경로입니다. |
| `log_level` / `log_file` | `"INFO"` / path | 로그 설정입니다. |
| `openai_api_key` | `"sk-..."` | OpenAI 인증 값입니다. |
| `openai_base_url` | URL | OpenAI-compatible endpoint override입니다. |
| `anthropic_api_key` | string | Anthropic 인증 값입니다. |
| `google_api_key` | string | Gemini API key입니다. |
| `gcp_project_id`, `gcp_location`, `google_application_credentials` | strings | Vertex AI 설정입니다. |
| `aws_profile`, `aws_region`, `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_role_arn` | strings | Bedrock 설정입니다. |
| `azure_openai_api_key`, `azure_openai_endpoint`, `azure_openai_api_version`, `azure_openai_deployment` | strings | Azure OpenAI 설정입니다. |
| `custom_llm_base_url`, `custom_llm_api_key`, `custom_llm_model_name` | strings | OpenAI-compatible custom provider 설정입니다. |
| `preload_icons` | `False` | 선택 아이콘 asset warm-up입니다. |
| `icon_preload_limit` | integer | 선택 아이콘 warm-up 개수 제한입니다. |

### 문서 생성

단발성 호출은 `generate_document(...)`가 가장 단순합니다. 반복 호출은
`DocuMind(...)`를 한 번 만들고 `engine.generate(...)`를 호출하는 방식을 권장합니다.

```python
from documind import DocuMind, ImageAttachment, TemplateInput

engine = DocuMind(
    llm_provider="openai",
    openai_api_key="sk-...",
    storage_local_path="./outputs",
)

result = await engine.generate(
    query="고객 온보딩 발표 자료를 만들어줘.",
    format="pptx",
    locale="ko",
    template=TemplateInput(path="./template.pptx"),
    images=[
        ImageAttachment(path="./product.png", description="제품 화면"),
    ],
    needs_research=False,
)
```

| 인자 | 타입 / 기본값 | 설명 |
|------|---------------|------|
| `query` | required string | 자연어 문서 생성 내용입니다. |
| `format` | `"pptx"` | `engine.generate`의 출력 형식입니다. `pptx`, `docx`, `xlsx`, `md`, `hwp` 중 하나입니다. |
| `document_type` | optional | `format` alias입니다. 외부 입력 모델과 맞출 때 유용합니다. |
| `template_id` | optional string | template 식별자를 함께 전달할 때 사용하는 호환 hook입니다. |
| `template` | path, bytes, dict, `TemplateInput` | 선택 native/template 파일입니다. |
| `images` | list of paths, bytes, dicts, `ImageAttachment` | 계획/시각 참고에 사용할 이미지 근거입니다. |
| `session_id` | optional string | 호출자가 지정하는 correlation/session ID입니다. |
| `locale` | `"ko"` | locale hint입니다. 출력 언어는 `query`에서도 추론됩니다. |
| `needs_research` | `None` | `True`는 research 강제, `False`는 생략, `None`은 DocuMind가 의도를 추론합니다. |
| `preload_icons` | optional bool | 호출 단위 아이콘 warm-up toggle입니다. |
| `**options` | dict | forward-compatible pipeline option입니다. 안정적인 SDK 사용자는 위 명시 인자를 우선 사용하세요. |

### 구조화 입력

```python
from documind import GenerationRequest

generation_input = GenerationRequest(
    query="주간 보고서를 만들어줘.",
    document_type="docx",
    locale="ko",
    needs_research=False,
    options={"template_id": "internal-template-id"},
)

result = await engine.generate_from_request(generation_input)
```

| `GenerationRequest` field | 타입 / 기본값 | 설명 |
|---------------------------|---------------|------|
| `query` | required string | 자연어 문서 생성 내용입니다. |
| `document_type` | `"pptx"` | `pptx`, `docx`, `xlsx`, `md`, `hwp` 중 하나입니다. |
| `template` | `None` | path, bytes, dict, `TemplateInput`을 받을 수 있습니다. |
| `images` | `[]` | 이미지 근거 목록입니다. |
| `session_id` | `None` | correlation/session ID입니다. |
| `locale` | `"ko"` | locale hint입니다. |
| `needs_research` | `None` | research routing toggle입니다. |
| `stream` | `False` | 외부 adapter와 맞추기 위한 streaming marker입니다. SDK streaming은 `generate_stream`을 사용합니다. |
| `options` | `{}` | `template_id` 등을 포함하는 추가 pipeline option입니다. |

| helper type | fields |
|-------------|--------|
| `TemplateInput` | `path`, `content`, `filename` |
| `ImageAttachment` | `path`, `content`, `filename`, `mime_type`, `role="content_reference"`, `description` |

`GenerationResult`는 `success`, `output_path`, `output_bytes`, `document_type`,
`mime_type`, `fidelity_scores`, `slide_count`, `errors`, `metadata`, `to_dict()`를
제공합니다.

## 지원 형식

| 형식 | 출력 |
|------|------|
| `pptx` | PowerPoint 네이티브 파일 |
| `docx` | Word 네이티브 파일 |
| `xlsx` | Excel 네이티브 워크북 |
| `md` | Markdown 문서 |
| `hwp` | HWPX 문서 |

## 스트리밍

```python
from documind import DocuMind, GenerationRequest

engine = DocuMind(llm_provider="openai", openai_api_key="sk-...")
generation_input = GenerationRequest(query="주간 보고서를 만들어줘.", document_type="docx")

async for event in engine.generate_stream(generation_input):
    print(event.to_sse())
```

## 소스에서 빌드

```bash
cd packages/documind
python scripts/sync_runtime.py
python -m build
```

배포 절차는 [`PUBLISHING.ko.md`](PUBLISHING.ko.md)와
[`PUBLISHING.md`](PUBLISHING.md)를 참고하세요.

## 라이선스

Apache-2.0
