# syntax=docker/dockerfile:1.4
FROM python:3.12-slim-bookworm AS base

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies required for mysqlclient, cryptography, and image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    default-libmysqlclient-dev \
    pkg-config \
    libgl1 \
    libglib2.0-0 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast, deterministic dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

# Copy dependency definition files
COPY pyproject.toml uv.lock ./

# Install locked dependencies into system environment
RUN uv export --no-dev --format requirements-txt -o requirements.txt && \
    pip install --no-cache-dir -r requirements.txt

# Create non-root system user and directory structure
RUN groupadd -r salow && useradd -r -g salow -u 1000 salow && \
    mkdir -p /app/static /app/media /backups /audit_checkpoints && \
    chown -R salow:salow /app /backups /audit_checkpoints && chmod 700 /backups /audit_checkpoints

# Copy application source code
COPY --chown=salow:salow . .

USER salow

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--threads", "2", "--timeout", "60", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-"]
