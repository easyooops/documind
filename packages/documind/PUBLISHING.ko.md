# DocuMind SDK PyPI 배포

DocuMind SDK는 `packages/documind`를 패키지 루트로 사용합니다. 배포 직전에
`scripts/sync_runtime.py`로 패키지 runtime을 준비합니다.

**English:** `[PUBLISHING.md](PUBLISHING.md)`

## 사전 준비

```bash
python -m pip install --upgrade build twine
```

프로젝트 루트의 `.pypirc.sample`을 기준으로 `.pypirc`를 만들고 PyPI API token을
`password`에 입력합니다. 실제 token이 들어간 `.pypirc`는 commit하지 않습니다.

## SDK 빌드 구조

```text
packages/documind/
├── pyproject.toml
├── README.md
├── README.ko.md
├── PUBLISHING.md
├── PUBLISHING.ko.md
├── scripts/
│   └── sync_runtime.py
├── overlays/
│   └── src/
└── src/
    ├── documind/
    └── src/                 # sync_runtime.py가 생성하는 package runtime
```

`overlays/src`에는 패키지 전용 runtime 파일을 둡니다.

- 아이콘 asset은 가벼운 파일 캐시 흐름을 사용
- 패키지 생성 과정에서 browser screenshot 불필요
- diagram 스타일 시각 요소는 이미지 모델 asset 생성 사용
- 이미지 모델 미설정/실패 시 이미지 asset 없이 계속 진행
- PPTX, DOCX, XLSX, Markdown, HWPX 네이티브 문서 출력

## 빌드

```bash
cd packages/documind
python scripts/sync_runtime.py
python -m build
```

Windows 환경에서 console/path encoding 문제로 isolated build가 실패하면 현재 환경의
빌드 도구를 사용합니다.

```bash
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python -m build --no-isolation
```

생성물:

```text
dist/documind-0.2.2-py3-none-any.whl
dist/documind-0.2.2.tar.gz
```

## 산출물 검증

```bash
twine check dist/*
```

wheel 구성을 확인합니다.

```bash
python -c "import zipfile; n=zipfile.ZipFile('dist/documind-0.2.2-py3-none-any.whl').namelist(); print(any(x.startswith('src/api') for x in n), any('/formats/pdf/' in x for x in n), any('playwright' in x.lower() for x in n), any('visual_renderer.py' in x for x in n))"
```

모든 값이 `False`여야 합니다.

로컬 설치 검증:

```bash
python -m venv .venv-test
.venv-test\Scripts\python -m pip install dist\documind-0.2.2-py3-none-any.whl
.venv-test\Scripts\python -c "from documind import DocuMind, GenerationRequest; print(DocuMind.__name__, GenerationRequest(query='x').document_type)"
```

## TestPyPI 업로드

프로젝트 루트 `.pypirc`를 사용하는 경우:

```bash
twine upload --config-file ..\..\.pypirc --repository testpypi dist/*
```

설치 테스트:

```bash
python -m venv .venv-testpypi
.venv-testpypi\Scripts\python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ documind
.venv-testpypi\Scripts\python -c "from documind import generate_document; print(generate_document)"
```

## 운영 PyPI 업로드

```bash
twine upload --config-file ..\..\.pypirc dist/*
```

PyPI는 같은 버전을 다시 업로드할 수 없습니다. 다시 배포하려면
`packages/documind/pyproject.toml`의 `version`을 올린 뒤 새로 빌드합니다.

## 배포 체크리스트

- `python scripts/sync_runtime.py` 실행
- `python -m build` 성공
- `twine check dist/*` 성공
- wheel 구성 검증 결과가 모두 `False`
- TestPyPI 설치 및 import 검증
- 운영 PyPI 업로드
- `pip install documind` 설치 검증

