# Database Schema

Initial schema for the data-collection foundation.

## Core Tables

### companies

Canonical company registry for the pilot universe.

Examples:

- Tata Consultancy Services
- Infosys
- Microsoft

This table stores stable company-level metadata such as country, sector, ticker, exchange, and website.

MCA/Data.gov collection can also populate identity fields:

- CIN
- incorporation date
- company status
- company category

### company_aliases

Name variants that resolve to one canonical company.

Examples:

- TCS
- TCS Ltd
- Tata Consultancy Services Limited

The `normalized_alias` column supports deterministic entity resolution.

### data_sources

Registry of source systems used by the pipeline.

Examples later:

- News API
- job-board scraper
- funding API
- company announcements source

This table records whether a source needs authentication and known source limitations.

### collection_runs

One execution of the collection pipeline.

This lets us answer:

- which company was collected
- when collection started/completed
- whether it succeeded
- what failed

### source_records

Raw records collected from APIs or scrapers.

The `raw_data` JSONB column preserves original source payloads for audit/debugging. The `normalized_data` JSONB column stores cleaned source-specific output.

### kpi_observations

Feature-ready observations extracted from source records.

Examples later:

- job_posting_count
- layoff_news_count
- employee_count
- funding_event_count
- leadership_change_count

This table includes point-in-time fields such as `observed_at`, `published_at`, `period_start`, and `period_end`.

## Design Notes

- Raw source payloads are preserved before normalization.
- Company identity is centralized in `companies` and `company_aliases`.
- Source metadata is centralized in `data_sources`.
- Collection attempts are tracked independently from final KPI values.
- The schema is intentionally flexible because source availability will evolve during Phase 1-3.
