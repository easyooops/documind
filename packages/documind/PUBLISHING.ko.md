# DocuMind SDK PyPI 배포 가이드

이 디렉터리(`packages/documind`)가 PyPI 배포용 패키지 루트입니다. 서비스 전체 self-host 코드는 저장소 루트에서 관리하고, PyPI wheel에는 SDK 호출에 필요한 문서 생성 관련 코드와 런타임 자산만 포함합니다.

## 소스 관리 원칙

DocuMind 런타임의 원본 코드는 저장소 루트의 `src`입니다. `packages/documind/src/src`는 배포 직전에 `python scripts/sync_runtime.py`로 생성하는 빌드용 미러이며, 직접 수정하지 않습니다.

일반 기능 변경 시에는 루트 `src`만 수정합니다. PyPI 패키지의 공개 import 표면, 패키지 메타데이터, 배포 문서만 `packages/documind`에서 관리합니다.

## 사전 준비

```bash
python -m pip install --upgrade build twine
```

PyPI 업로드에는 PyPI 계정, 2FA, API token 또는 Trusted Publisher 설정이 필요합니다.

API token으로 업로드할 경우 저장소의 `.pypirc.sample`을 참고해 사용자 홈 디렉터리의 `%USERPROFILE%\.pypirc` 파일을 만듭니다. 실제 token이 들어간 `.pypirc`는 저장소에 commit하지 않습니다.

## 빌드

```bash
cd packages/documind
python scripts/sync_runtime.py
python -m build --wheel
```

생성물:

```text
packages/documind/dist/documind-0.2.0-py3-none-any.whl
```

## 산출물 검증

```bash
twine check dist/*
```

wheel에 API, 웹, 테스트 코드가 들어가지 않았는지 확인:

```bash
python -c "import zipfile; names=zipfile.ZipFile('dist/documind-0.2.0-py3-none-any.whl').namelist(); print(any(n.startswith('src/api') for n in names), any(n.startswith('web') for n in names), any(n.startswith('tests') for n in names))"
```

모두 `False`여야 합니다.

`src/src`는 Git에 올리지 않고, 배포 직전에 재생성합니다.

## TestPyPI 업로드

```bash
twine upload --repository testpypi dist/*
```

설치 테스트:

```bash
python -m venv .venv-test
.venv-test\Scripts\python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ documind
.venv-test\Scripts\python -c "from documind import DocuMind, GenerationRequest; print(DocuMind.__name__, GenerationRequest(query='x').document_type)"
```

## 운영 PyPI 업로드

```bash
twine upload dist/*
```

PyPI는 같은 버전을 다시 업로드할 수 없습니다. 재배포 전 `packages/documind/pyproject.toml`의 `version`을 올려야 합니다.

## Trusted Publisher 예시

```yaml
name: Publish documind SDK to PyPI

on:
  release:
    types: [published]
  push:
    tags: ["documind-v*"]

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install build tools
        run: python -m pip install build
      - name: Build
        working-directory: packages/documind
        run: |
          python scripts/sync_runtime.py
          python -m build --wheel
      - name: Publish
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: packages/documind/dist/
```
