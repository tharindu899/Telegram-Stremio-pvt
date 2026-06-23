# Hugging Face Spaces-friendly Docker build.
# Uses standard Python + pip instead of `uv sync` so builds do not wait on
# uv lockfile resolution or a Python standalone download.
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=en_US.UTF-8 \
    PORT=7860 \
    HF_SPACE=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        bash \
        curl \
        ca-certificates \
        locales && \
    locale-gen en_US.UTF-8 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependencies first so Hugging Face can cache this layer between code-only
# updates.  `--prefer-binary` avoids source builds when wheels are available.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip setuptools wheel && \
    pip install --prefer-binary --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh

EXPOSE 7860

# HF detects the service once it binds to the `app_port` declared in README.md.
CMD ["bash", "start.sh"]
