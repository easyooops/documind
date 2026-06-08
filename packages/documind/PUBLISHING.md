# Publishing DocuMind SDK

DocuMind SDK is published from `packages/documind`. The source tree is prepared
with `scripts/sync_runtime.py` immediately before packaging.

**Korean:** [`PUBLISHING.ko.md`](PUBLISHING.ko.md)

## Prerequisites

```bash
python -m pip install --upgrade build twine
```

Create `.pypirc` from the repository root `.pypirc.sample`, then put the PyPI API
token in the `password` field. Do not commit the real `.pypirc`.

## SDK Layout

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
    └── src/                 # generated package runtime
```

`overlays/src` contains package-specific runtime files:

- icon assets use a lightweight file-cache flow
- browser screenshots are not required for package generation
- diagram-style visuals use image-model asset generation
- image generation failure continues without image assets
- native document outputs are generated for PPTX, DOCX, XLSX, Markdown, and HWPX

## Build

```bash
cd packages/documind
python scripts/sync_runtime.py
python -m build
```

On Windows, if isolated build fails because of console/path encoding, build with
the current environment:

```bash
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python -m build --no-isolation
```

Expected outputs:

```text
dist/documind-0.2.2-py3-none-any.whl
dist/documind-0.2.2.tar.gz
```

## Validate

```bash
twine check dist/*
```

Confirm the wheel contents:

```bash
python -c "import zipfile; n=zipfile.ZipFile('dist/documind-0.2.2-py3-none-any.whl').namelist(); print(any(x.startswith('src/api') for x in n), any('/formats/pdf/' in x for x in n), any('playwright' in x.lower() for x in n), any('visual_renderer.py' in x for x in n))"
```

All printed values should be `False`.

Local install smoke test:

```bash
python -m venv .venv-test
.venv-test\Scripts\python -m pip install dist\documind-0.2.2-py3-none-any.whl
.venv-test\Scripts\python -c "from documind import DocuMind, GenerationRequest; print(DocuMind.__name__, GenerationRequest(query='x').document_type)"
```

## Upload To TestPyPI

When using `.pypirc` from the repository root:

```bash
twine upload --config-file ..\..\.pypirc --repository testpypi dist/*
```

Install test:

```bash
python -m venv .venv-testpypi
.venv-testpypi\Scripts\python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ documind
.venv-testpypi\Scripts\python -c "from documind import generate_document; print(generate_document)"
```

## Upload To PyPI

```bash
twine upload --config-file ..\..\.pypirc dist/*
```

PyPI does not allow overwriting an existing version. To republish, update
`version` in `packages/documind/pyproject.toml`, run a clean build, and upload
the new artifacts.

## Release Checklist

- [ ] Run `python scripts/sync_runtime.py`
- [ ] Run `python -m build`
- [ ] Run `twine check dist/*`
- [ ] Confirm wheel content check prints only `False`
- [ ] Verify TestPyPI install and import
- [ ] Upload to PyPI
- [ ] Verify `pip install documind`
