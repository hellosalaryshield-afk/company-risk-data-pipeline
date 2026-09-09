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
- `POST /companies/segment`
- `POST /collections/news`
- `POST /collections/mca`
- `POST /collections/market`
- `POST /collections/company` - runs every source that applies to the company
- `POST /collections/macro` - macro and index covariates
- `POST /collections/gdelt` - news tone and coverage volume, no API key
- `POST /collections/workplace` - Glassdoor ratings and open job count (billed per call)
- `GET /sources`
- `GET /collection-runs`
- `GET /companies/{id}/summary`
- `GET /companies/{id}/report` - HTML report
- `GET /companies/{id}/report.pdf` - PDF report
- `GET /companies/{id}/features?as_of=...` - point-in-time feature vector
- `GET /leakage-check` - point-in-time audit of every stored KPI

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

If it returns `status: source_failed`, the key is configured but Data.gov.in did not respond within the request timeout. The failure is recorded in `collection_runs` so the overall pipeline can continue.

## Classify Company Segments

Every company is classified into one of the six client-defined segments before source
selection, because no single source covers all six:

```text
INDIA_LISTED
INDIA_UNLISTED_FUNDED
INDIA_UNLISTED_NON_FUNDED
FOREIGN_LISTED
FOREIGN_UNLISTED_FUNDED
FOREIGN_UNLISTED_NON_FUNDED
```

Listed companies are decided by ticker/exchange. Unlisted companies also need
`funding_status`; when funding is unknown the segment stays `NULL` instead of being guessed.

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/companies/segment `
  -ContentType "application/json" `
  -Body '{"company_name":"TCS"}'
```

Seeding assigns segments automatically. To push registry corrections such as a changed
ticker into an already-seeded database:

```powershell
python scripts/seed_companies.py --update-existing
```

## Collect Market Data (Yahoo Finance)

This source needs no API key. It covers `INDIA_LISTED` and `FOREIGN_LISTED` companies and
is not part of the original Colab notebook.

```powershell
python scripts/collect_market.py TCS --range 6mo
python scripts/collect_market.py Freshworks
```

Or call the API:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/collections/market `
  -ContentType "application/json" `
  -Body '{"company_name":"TCS","range_period":"6mo","interval":"1d"}'
```

This stores a price-history record in `source_records` and these KPIs in `kpi_observations`:

- `market_price`
- `market_price_change_pct_period`
- `market_drawdown_from_52w_high_pct`
- `market_premium_over_52w_low_pct`
- `market_max_drawdown_pct_period`
- `market_annualized_volatility_pct`
- `market_trading_volume`

Responses to expect:

- `completed` with `record_found: true` - data stored
- `completed` with `record_found: false` - the ticker is stale or delisted
- `not_applicable` - the company is unlisted, so this source does not apply
- `source_failed` - Yahoo Finance was unreachable; the failure is recorded in `collection_runs`

Source limits are documented in [docs/source-feasibility.md](docs/source-feasibility.md).

## Run The Full Pipeline For One Company

One call resolves the company, classifies its segment, and runs every source that applies
to that segment. A source that fails or is not configured never stops the others.

```powershell
python scripts/collect_company.py TCS
```

Or call the API:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/collections/company `
  -ContentType "application/json" `
  -Body '{"company_name":"TCS","days_back":30,"page_size":25,"range_period":"6mo"}'
```

Source routing by segment is defined in `app/pipeline/source_selection.py`:

| Segment | Sources run |
| --- | --- |
| `INDIA_LISTED` | news, MCA, market, workplace* |
| `INDIA_UNLISTED_FUNDED` / `INDIA_UNLISTED_NON_FUNDED` | news, MCA, workplace* |
| `FOREIGN_LISTED` | news, market, workplace* |
| `FOREIGN_UNLISTED_FUNDED` / `FOREIGN_UNLISTED_NON_FUNDED` | news, workplace* |
| undetermined | news, workplace* |

`*` The Glassdoor actor is **billed about $0.10 per company**, so it is skipped by default.
Pass `"include_metered_sources": true` to run it.

If the typed name is ambiguous the response lists candidates instead of guessing.

## Collect Macro And Industry Covariates

Phase 3 needs industry-level and macro features. These come from the same keyless Yahoo
endpoint, so they add no new credential or cost:

```powershell
python scripts/collect_macro.py --range 6mo
```

Collected indicators: USD/INR, gold, US 13-week T-bill rate, NIFTY 50, NIFTY IT, S&P 500.

These rows are stored with `company_id = NULL` because they describe the environment, not
one company.

## Collect Glassdoor Workplace Data

Glassdoor closed its public API in 2024, so this goes through an Apify actor. Add the token:

```text
APIFY_TOKEN=your_apify_token
```

```powershell
python scripts/collect_workplace.py "Tata Consultancy Services"
```

This stores 13 KPIs including overall rating, work-life balance, senior management, positive
business outlook, and **open job count** - which is also the hiring signal Phase 3 needs.

**Cost:** about $0.10 per company per run. An Apify FREE plan carries $5/month, so roughly
50 companies. A 500-company refresh costs about $50. The actor is therefore skipped by
default in `POST /collections/company`.

**Identity guard:** the actor returns its top search hit without verifying it. A live query for
`Zepto` returned `Zepto (Mexico)`. Every result is name-checked; a weak match is stored for
audit, returned as `match_rejected`, and never becomes a KPI.

## Collect News Tone With GDELT

GDELT DOC 2.0 needs no API key and, unlike the NewsAPI developer tier, carries no restriction
on production use. It supplies daily news tone (sentiment) and coverage volume.

```powershell
python scripts/collect_gdelt_batch.py --timespan 3m
```

Or for one company:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/collections/gdelt `
  -ContentType "application/json" `
  -Body '{"company_name":"TCS","timespan":"3m"}'
```

**GDELT is rate limited to roughly one request every five seconds**, and each company needs
three calls. That is about 25 seconds per company, or ~3.5 hours for 500 companies. So:

- Run `scripts/collect_gdelt_batch.py` on a schedule to fill the database.
- For a public-facing call, pass `"include_slow_sources": false` to `/collections/company`
  so the request stays fast and the report reads the values the batch already stored.

A rate-limit refusal is recorded in `collection_runs` with `source_status: rate_limited` and
never breaks the rest of the pipeline.

## How The Pieces Fit Together

The report reads from the database, not from live API calls. That is what keeps a public tool
fast and cheap:

```text
scheduled batch                     live request
---------------                     ------------
collect_gdelt_batch.py              POST /collections/company   (fast sources only)
collect_macro.py                    GET  /companies/{id}/report
        |                                        |
        +-------------> PostgreSQL <-------------+
                     source_records
                     kpi_observations
```

## Point-In-Time Features (Phase 3)

A feature used to predict a layoff six months out must only contain what was actually known
at the prediction date. Ask for a company's vector as of any date:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/companies/1/features?as_of=2026-06-01"
```

Values published after that date are excluded outright. Values that were published in time
but still fail a check are excluded too, with the reason given, for example a count whose
30-day window runs past the prediction date.

Before any modelling, run the leakage check the brief requires:

```powershell
python scripts/check_leakage.py
```

It exits 2 if any row has impossible timestamps. Two known issues it currently reports:
Glassdoor ratings carry no publication date and so cannot enter a backtest, and legacy news
rows written before the timestamp fix need `python scripts/repair_timestamps.py --apply`.

## Collect NSE Bulk And Block Deals

Large institutional or promoter selling, free and with no API key:

```powershell
python scripts/collect_deals.py
```

**Each NSE file covers a single trading day**, so this must run daily to build any history.
It is market-wide: the file is fetched once and attached to every registry company whose NSE
ticker appears in it. Most days most companies will not appear, which is normal.

Add it to the daily job list alongside `refresh_all.py --skip-slow`.

## Refresh Everything (Documented Refresh Process)

One command refreshes macro context and every company in the registry:

```powershell
python scripts/refresh_all.py
```

Useful variants:

```powershell
python scripts/refresh_all.py --skip-slow          # fast: no GDELT, run its batch separately
python scripts/refresh_all.py --include-metered    # also runs Apify Glassdoor. Costs money.
python scripts/refresh_all.py --limit 5            # smoke test
python scripts/refresh_all.py --start-at 120       # resume an interrupted run
```

Recommended cadence while the pilot is running:

| Job | Command | Frequency |
| --- | --- | --- |
| Everything except GDELT | `refresh_all.py --skip-slow` | daily |
| NSE bulk/block deals | `collect_deals.py` | **every trading day** (one day per file) |
| GDELT tone | `collect_gdelt_batch.py` | nightly, it is slow |
| Glassdoor | `refresh_all.py --include-metered` | monthly, it is billed per call |

Exit code 2 means at least one company had no source succeed at all.

## Thin-Data Companies

Most companies have no collected signals early in a pilot, and an empty report is the worst
answer for someone who typed a real company name. When a company has no value of its own for
a signal, the report falls back to the **median across comparable companies**, choosing the
tightest cohort that actually has data:

```text
same sector  ->  same segment  ->  whole registry
```

Every borrowed figure is labelled with which cohort it came from and how many peers stood
behind it, and the report states plainly that these are not measurements of that company. A
company's own value is never replaced by a cohort estimate.

## Generate A Company Report

Company name in, HTML and PDF out.

```powershell
python scripts/generate_report.py TCS --out-dir reports
```

Or in the browser:

```text
http://127.0.0.1:8000/companies/1/report
http://127.0.0.1:8000/companies/1/report.pdf
```

The report shows company identity, resolved segment, collected signals with trend
direction, per-source status, data coverage band, plain-language notes, and macro context.

The report deliberately does **not** print a risk score. The predictive model is a later
phase, so claiming a probability now would be overclaiming. It reports what was collected
and how complete that data is.

Add a footer line to every report with:

```text
REPORT_FOOTER_NOTE=Your contact or service note here.
```

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
DATA_GOV_API_KEY=your_data_gov_key
APIFY_TOKEN=your_apify_token
REPORT_FOOTER_NOTE=your_footer_note
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
