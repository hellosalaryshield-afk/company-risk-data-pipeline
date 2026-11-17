# Backend image for Render (Docker runtime).
#
# Built on Microsoft's Playwright image because the scorecard PDF is printed by headless
# Chromium, which needs system libraries that Render's native Python runtime cannot install.
# The image already contains Chromium matching Playwright 1.65.0 (pinned in pyproject.toml).
FROM mcr.microsoft.com/playwright/python:v1.65.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install ".[scorecard]"

COPY alembic.ini ./
COPY alembic ./alembic
COPY data/pilot_companies.csv ./data/pilot_companies.csv
COPY scripts ./scripts

# Render sets PORT. Migrations run first so a deploy brings the schema up to date.
# One worker keeps memory inside the free plan's 512 MB.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
