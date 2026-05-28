FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        fonts-noto-cjk \
        libcairo2-dev \
        libreoffice \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml constraints.txt README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install ".[all-providers,postgres,s3,icons]" \
    && python -m playwright install --with-deps chromium

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
