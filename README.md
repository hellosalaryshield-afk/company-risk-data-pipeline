# Company Risk Data Pipeline

Backend/data-pipeline foundation for a company layoff-risk research project.

The long-term system will accept a company name, resolve it to a canonical company, collect KPI data from APIs and public web sources, normalize the collected records, store them in PostgreSQL, and generate reports that can support risk scoring.

This repository currently includes the project foundation, initial database schema, and company registry/entity-resolution API. It does not yet include scraping, source integrations, ML, reports, or a user dashboard.

## Current Architecture

```text
app/
  api/             FastAPI routes
  companies/       Company registry and entity resolution
  config/          Settings and logging
  database/        SQLAlchemy/Neon connection setup
  models/          Database models
  normalization/   Data normalization later
  pipeline/        Collection orchestration later
  reports/         HTML/PDF reports later
  schemas/         Pydantic schemas later
  sources/         API and scraper adapters later
  utils/           Shared utilities

tests/
  unit/
  integration/

scripts/
docs/
```

## Requirements

- Python 3.11 or newer
- Git
- Neon PostgreSQL connection string

## Local Setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the project in editable mode with development tools:

```powershell
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Create your local environment file:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and add your real Neon connection string:

```text
APP_ENV=development
LOG_LEVEL=INFO
DATABASE_URL=postgresql+psycopg://user:password@host:5432/database?sslmode=require
```

Never commit `.env`.

## Run The API

```powershell
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "app_env": "development"
}
```

## Run Tests

```powershell
pytest
```

## Check Neon Connection

After adding your real `DATABASE_URL` to `.env`, run:

```powershell
python scripts/check_database.py
```

Expected output:

```text
Database connection ok.
```

## Database Migrations

Apply migrations to Neon:

```powershell
python -m alembic upgrade head
```

Check current migration version:

```powershell
python -m alembic current
```

The initial schema is documented in [docs/database-schema.md](docs/database-schema.md).

## Seed Pilot Companies

Load the first pilot company registry into Neon:

```powershell
python scripts/seed_companies.py
```

This seed is idempotent. Running it again skips companies that already exist.

## Check Company Resolution

Test the company resolver against Neon:

```powershell
python scripts/check_resolver.py TCS
python scripts/check_resolver.py "Bundl Technologies"
python scripts/check_resolver.py "Unknown Startup"
```

Expected behavior:

- `TCS` resolves to `Tata Consultancy Services`
- `Bundl Technologies` resolves to `Swiggy`
- unknown names return `unmatched`

## Current API Endpoints

- `GET /health`
- `GET /companies`
- `POST /companies`
- `POST /companies/resolve`
- `POST /collections/news`
- `POST /collections/mca`

## Collect NewsAPI Data

Add your NewsAPI key to `.env`:

```text
NEWS_API_KEY=your_key_here
```

Collect news for one resolved company:

```powershell
python scripts/collect_news.py TCS
```

Or call the API:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/collections/news `
  -ContentType "application/json" `
  -Body '{"company_name":"TCS","days_back":30,"page_size":25}'
```

This stores:

- raw NewsAPI articles in `source_records`
- extracted counts in `kpi_observations`
- run status in `collection_runs`

Example local smoke test:

```powershell
python scripts/collect_news.py TCS --page-size 5
```

If this succeeds, the response includes `records_stored` and KPI counts.

## Collect MCA/Data.gov Company Master Data

Add your Data.gov.in API key to `.env`:

```text
DATA_GOV_API_KEY=your_key_here
```

Collect MCA company master data for one resolved company:

```powershell
python scripts/collect_mca.py Razorpay
```

Or call the API:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/collections/mca `
  -ContentType "application/json" `
  -Body '{"company_name":"Razorpay"}'
```

This stores the raw MCA record in `source_records` and updates company identity fields such as CIN, legal name, incorporation date, status, and category when available.

If Swagger returns:

```json
{
  "detail": "DATA_GOV_API_KEY is not configured."
}
```

check that `DATA_GOV_API_KEY` exists in `.env`, then stop and restart Uvicorn. The app reads environment variables when the server process starts.

If it returns a timeout message, the key is configured but Data.gov.in did not respond within the request timeout. Try again later or test another company.

## Render Deployment

Use these settings for a Render Web Service:

```text
Language: Python 3
Branch: main
Root Directory: blank
Build Command: pip install -e .
Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set environment variables in Render without quotes:

```text
APP_ENV=production
LOG_LEVEL=INFO
DATABASE_URL=your_neon_database_url
NEWS_API_KEY=your_newsapi_key
```

The deployed app should respond at:

```text
/health
```

## Git Workflow

Check changed files:

```powershell
git status --short
```

Commit a milestone:

```powershell
git add .
git commit -m "feat: initialize Python project foundation"
git push
```

## Next Milestones

1. Implement company registry and alias matching.
2. Add scripts to seed the first pilot companies.
3. Add the source-adapter interface before implementing real APIs or scrapers.
4. Build one real source adapter with mocked tests.
