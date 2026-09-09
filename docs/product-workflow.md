# Product Workflow

This project converts exploratory Colab notebooks into a maintainable API and database pipeline.

## What We Are Building

The current scope is not the final machine-learning model. The current scope is the data platform that collects, normalizes, stores, and serves company-level signals.

```text
company name
  -> company resolver
  -> source adapters
  -> raw records
  -> normalized KPI observations
  -> database
  -> report/API response
```

## Main Users

### Internal Researcher

Uses the system to test sources, validate KPI coverage, and prepare feature-ready data for Phase 1-3.

### Backend/API Consumer

Calls the FastAPI service when a company report is requested.

### Future Public User

Types a company name and receives a risk-band/report. This public flow is later; the current web UI should be a simple internal testing interface.

## Input

The system should accept:

- typed company name, such as `TCS` or `Razorpay`
- optional source selection, such as `newsapi` or `mca`
- optional refresh flag

## Processing

1. Resolve typed name to canonical company.
2. Create a collection run.
3. Trigger enabled source adapters.
4. Store raw source response in `source_records`.
5. Extract normalized KPIs into `kpi_observations`.
6. Return collection status and available observations.

## Output

Initial API output:

- resolved company
- match confidence
- source status
- collected records count
- KPI observations

Later report output:

- HTML report
- PDF report
- email delivery

## Why A Web UI Helps

A full admin dashboard is not needed now. A simple internal UI is useful later for:

- testing company matching
- running one company through selected sources
- viewing source failures
- previewing report JSON
- demonstrating progress to the client

The Stitch screens can map to this internal tool:

- Risk Dashboard - TCS: report preview
- Company Registry & Entity Resolution: resolver tester and registry table
- Data Sources & Pipeline Health: source status and collection runs
- Design System: shared UI styling

## Current Build Order

1. Company registry and entity resolver. Done.
2. Seed first pilot companies and aliases. Done.
3. Source adapter interface. Done.
4. NewsAPI adapter from the Colab prototype. Done.
5. MCA/Data.gov adapter for Indian company metadata. Done, source unstable upstream.
6. Company segment classification for the six client segments. Done.
7. Yahoo Finance market adapter for listed companies. Done.
8. Macro and industry covariates. Done.
9. Pipeline runner that stores raw records and KPIs. Done (`POST /collections/company`).
10. HTML/PDF report generation. Done (`GET /companies/{id}/report`).
11. Thin-data sector/cohort fallback. Done.
12. Single documented refresh process (`scripts/refresh_all.py`). Done.
13. Simple internal web UI. Not started.
12. Glassdoor workplace ratings and open job count. Done.
13. Full KPI/source matrix against the client sheet. Done (`docs/kpi-source-matrix.csv`).
14. GDELT news and sentiment. Not started, recommended next.
15. Point-in-time / as-of feature querying. Not started, largest correctness risk.

## Mapping To The Internship Phases

| Phase | Scope | State |
| --- | --- | --- |
| 1 - define and validate the target | Layoff proxy definition, confidence flag, validation set | Not started. Needs client input on the target definition. |
| 2 - data pipeline and entity resolution | Registry, alias mapping, source list, ingestion | Largely done. Registry, resolver, three sources, source list documented. |
| 3 - lagged covariates | Company-internal, company-external, industry, macro | Partial. Industry and macro done; company-external partly via news. Company-internal (job postings, hiring/attrition) not started. Point-in-time handling not started. |
| 4 - model, metrics, benchmark | Calibrated probability, walk-forward backtest | Not started. Depends on Phase 1 and 3. |
| 5 - EPFO feasibility | Payroll releases, establishment portal, access routes | Not started. |
| 6 - serve and refresh prototype | Name in, score and explanation out | Mostly done. Name resolution, ambiguity clarification, thin-data cohort fallback, HTML/PDF output and a single documented refresh process all exist. Only the score itself waits on Phase 4. |

## Point-In-Time Handling

Phase 3 requires that every feature be usable at prediction time. This is not yet
implemented and is the largest correctness risk in the current pipeline:

- `source_records.published_at` and `collected_at` are stored, so an as-of filter is
  possible, but nothing enforces it yet.
- `kpi_observations` currently records values as of collection time. Recomputing a
  historical feature vector would need an explicit as-of query.
- No leakage check has been run, and the brief requires mentor sign-off before modeling.

## Colab Review Notes

The Colab is useful as exploration, but should not be copied directly into production.

Issues to fix while productionizing:

- API keys must move to `.env`.
- Notebook fallback fake values should not be treated as verified data.
- Work-culture ratings were out of scope until 2026-09-09, when the client explicitly re-added
  Glassdoor. Now implemented via the Apify actor; AmbitionBox is still not covered.
- Sentiment scoring direction must be checked carefully; distress should be negative or positive consistently.
- Unit tests should use mocked API responses instead of consuming real API quota.
- Raw API responses should be stored before feature extraction.
