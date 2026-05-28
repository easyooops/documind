# DocuMind

**Agentic AI 문서 생성 플랫폼**  
자연어 요청 -> 에이전트 기반 기획/디자인/생성 -> 실사용 가능한 네이티브 문서 출력

English version: [`README.md`](README.md)

DocuMind는 다음 두 가지 사용 방식을 함께 제공합니다.
- 협업형 문서 생성을 위한 풀스택 앱 (`FastAPI` + `Next.js`)
- API 서버 없이도 사용할 수 있는 패키지형 SDK/엔진 API

![DocuMind UI 및 생성 슬라이드](docs/images/documind-agentic-workflow.png)

---

## 서비스 소개

DocuMind는 비즈니스 문서를 빠르게 고품질로 만들어야 하는 팀을 위한 서비스입니다.  
전문 역할을 가진 에이전트가 의도 해석, 문서 구조 설계, 포맷별 생성, 품질 평가, 최종 산출까지 자동으로 연결합니다.

### 이런 작업에 강합니다

- 짧은 아이디어/요청을 발표/보고서 형태의 구조화된 문서로 변환
- 템플릿 스타일을 유지한 상태에서 내용 보강/수정
- HTML 시안이 아닌 네이티브 파일(PPTX/DOCX/PDF 등) 직접 생성
- 버전 관리와 프리뷰를 통한 반복 개선

### 핵심 기능

- 멀티 포맷 생성: `PPTX`, `DOCX`, `PDF`, `Markdown`, `XLSX`, `HWPX`
- 웹 검색 포함 가능 Agentic 오케스트레이션 + QA 피드백 루프
- 템플릿 업로드 기반 생성(기존 서식 채우기 모드 지원)
- API + Web UI + Python 엔진/CLI를 단일 저장소에서 제공

---

## Agentic 단계별 흐름

DocuMind는 포맷별 파이프라인(`src/formats/pptx/orchestrator.py`, `src/formats/rich_document/orchestrator.py` 등)으로 요청을 처리합니다.

### 1) 의도 해석 및 기획
- 사용자 의도와 출력 언어를 해석
- 문서 유형과 리서치 필요 여부 판단
- `document_spec`(섹션/블록/메타데이터) 초안 생성

### 2) 디자인 시스템 수립
- 템플릿 계열/비주얼 톤 선택 또는 추론
- 포맷 특화 디자인 시스템(색상/타이포/레이아웃) 구성
- 업로드한 템플릿이 있으면 해당 서식을 우선 보존

### 3) 네이티브 문서 생성
- 포맷별 렌더러/오케스트레이터로 실제 파일 생성
- 결과 파일과 메타데이터(점수, 섹션 수, 파이프라인 상태) 저장
- 스트리밍 진행 이벤트 제공(SDK/API 연동 가능)

### 4) 품질 검증 및 내보내기
- 품질 점검 후 필요 시 재생성 루프 수행
- 최종 파일 다운로드/프리뷰/버전 API 제공
- SDK에서는 `GenerationResult` 객체로 결과 반환

---

## 생성되는 문서

지원 출력 형식:
- `pptx`: 발표자료/전략 덱
- `docx`: 제안서/보고서/공문형 문서
- `pdf`: 배포용 리포트/브로셔 스타일 문서
- `md`: 기술 문서/가이드/요약 노트
- `xlsx`: 표/시트 중심 산출물
- `hwp`: 한글 워크플로우 연동 문서

각 생성 작업은 품질 점수, 기획/디자인 문맥, 버전 정보를 함께 저장하여 이후 수정/감사를 쉽게 만듭니다.

---

## 로컬 구동 방법

### 요구사항
- Python `3.11+`
- Node.js `18+`

### 설치

**Windows (PowerShell)**
```powershell
copy .env.example .env
npm run install:all
```

**Linux/macOS**
```bash
cp .env.example .env
npm run install:all
```

### 환경 변수

`.env`에 LLM 공급자 키를 설정하고,  
웹 UI용 `web/.env.local`에 아래를 지정합니다.

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 실행

```bash
npm run dev
```

- Web: `http://localhost:3000`
- API: `http://localhost:8000`
- OpenAPI: `http://localhost:8000/docs`

자주 쓰는 명령:

```bash
npm run dev:api
npm run dev:web
python -m src.cli generate "2026 AI 전략 10장 발표자료 작성"
```

---

## SDK 사용 방법

DocuMind는 API 서버 없이 Python 코드에서 직접 사용할 수 있습니다.

```python
import asyncio
from src.engine import DocuMind

async def main():
    engine = DocuMind(
        llm_provider="openai",
        default_llm_model="gpt-4o",
    )
    result = await engine.generate(
        query="2026년 AI 전략 10장 발표자료 작성",
        format="pptx",
        locale="ko",
    )
    print(result.success, result.output_path)

asyncio.run(main())
```

추가 제공:
- `generate_document(...)`: 원샷 생성 헬퍼
- `generate_stream(...)` / `engine.generate_stream(...)`: 진행 이벤트 스트리밍

---

## 인프라 배포 및 삭제

단일 EC2 기준 설치/삭제 가이드는 아래 문서를 참고하세요.
- `setup/README.md` (영문)
- `setup/README.ko.md` (국문)

포함 스크립트:
- 배포: `setup/ec2/terraform/single/tf-apply.sh`, `tf-apply.ps1`
- 삭제: `setup/ec2/terraform/single/tf-delete.sh`, `tf-delete.ps1`

Terraform 직접 삭제:

```bash
cd setup/ec2/terraform/single
terraform destroy
```

---

## 사용 오픈소스

DocuMind는 다음 오픈소스를 기반으로 구축되었습니다.

- Backend/API: `FastAPI`, `Uvicorn`, `SQLAlchemy`, `Pydantic`
- Agentic 런타임: `LangGraph`, `LangChain`
- LLM/클라우드 연동: `OpenAI SDK`, `Anthropic SDK`, `Boto3`
- 문서/렌더링: `python-pptx`, `PyMuPDF`, `Playwright`, `lxml`, `Pillow`
- Frontend: `Next.js`, `React`, `Tailwind CSS`, `Zustand`

의존성 라이선스 정책은 `pyproject.toml`의 `tool.licensecheck`에서 관리합니다.

---

## License

Apache License 2.0  
자세한 내용은 `LICENSE`를 참고하세요.

## Contact

Suyeong Yoo — `ssu0416@gmail.com`
