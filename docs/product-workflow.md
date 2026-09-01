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

1. Company registry and entity resolver.
2. Seed first pilot companies and aliases.
3. Source adapter interface.
4. NewsAPI adapter from the Colab prototype.
5. MCA/Data.gov adapter for Indian company metadata.
6. Pipeline runner that stores raw records and KPIs.
7. Simple internal web UI.
8. HTML/PDF report generation.

## Colab Review Notes

The Colab is useful as exploration, but should not be copied directly into production.

Issues to fix while productionizing:

- API keys must move to `.env`.
- Notebook fallback fake values should not be treated as verified data.
- Work-culture ratings such as Glassdoor/AmbitionBox are out of current internship scope unless the client explicitly re-adds them.
- Sentiment scoring direction must be checked carefully; distress should be negative or positive consistently.
- Unit tests should use mocked API responses instead of consuming real API quota.
- Raw API responses should be stored before feature extraction.
