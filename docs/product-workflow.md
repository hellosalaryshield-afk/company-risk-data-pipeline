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

Phase 3 requires that every feature be usable at prediction time. This is now implemented in
`app/features/point_in_time.py`.

Three timestamps decide whether a value may be used at a prediction date D:

| Field | Meaning | Rule |
| --- | --- | --- |
| `published_at` | when the fact became public | must be `<= D`, or it is a leak |
| `period_end` | last day the value was computed over | if it runs past `D`, the number itself contains the future |
| `collected_at` | when this pipeline fetched it | usually after `D`; acceptable only for sources that do not revise |

`GET /companies/{id}/features?as_of=...` returns the vector as it would have been known at
that date. Anything published later is excluded outright; anything that fails another check
is excluded too, with the reason listed under `excluded`.

### Source classifications

- **Revising sources** (`data_gov_mca_company_master`): MCA filings are amended, so a
  backfilled value cannot be trusted at a past date.
- **Undated sources** (`apify_glassdoor_company_search`): the actor returns a snapshot with
  no date, so nothing from it can be proven point-in-time safe. It is usable for a current
  report but must be excluded from a backtest.

### Running the leakage check

The brief requires this before any modelling, with mentor sign-off:

```powershell
python scripts/check_leakage.py
python scripts/check_leakage.py --as-of 2026-06-01
```

Exit code 2 means impossible timestamps exist and nothing downstream can be trusted.

### Known issues found by the first run

1. **Glassdoor KPIs cannot be point-in-time verified.** The actor returns an undated
   snapshot. They are fine in a current report and must not enter a backtest. Repeated
   collection over time will build a real dated history.
2. **Legacy news rows have impossible timestamps.** Rows written before the fix stamped
   `published_at` from the application clock while `collected_at` came from the database
   clock, so published landed a few seconds after collected. The collector now dates a
   count by its newest article and records the window. Repair the old rows with
   `python scripts/repair_timestamps.py --apply`, which clears the unreliable date rather
   than inventing one.

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
