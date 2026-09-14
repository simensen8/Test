# --- build stage: compile wheels that need a C toolchain (lxml, cryptography, bcrypt) ---
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --upgrade pip \
    && /venv/bin/pip install --no-cache-dir -r requirements.txt

# --- runtime stage: slim image, no compiler ---
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    APP_DATA_DIR=/app/data

WORKDIR /app
COPY app ./app

# PHI-bearing SQLite DB, encryption keys, and encrypted uploads live here --
# mount this as a persistent volume in production (see docker-compose.yml).
RUN mkdir -p /app/data && chown -R appuser:appuser /app
VOLUME ["/app/data"]
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
