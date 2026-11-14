FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements-base.txt /tmp/requirements-base.txt
ARG LLAMA_CPP_VERSION=0.3.36
ENV CMAKE_ARGS="-DGGML_NATIVE=OFF"
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements-base.txt \
    && pip install --no-cache-dir --prefer-binary --prefix=/install \
       "llama-cpp-python==${LLAMA_CPP_VERSION}" \
       --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app LOCAL_AI_GPP_DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
COPY backend/app /app/backend/app
COPY VERSION /app/VERSION
COPY models_storage /data/models_storage
RUN mkdir -p /data/logs

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
