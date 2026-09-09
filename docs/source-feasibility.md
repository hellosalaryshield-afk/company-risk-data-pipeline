# Source Feasibility Tracker

This document tracks candidate sources from the Colab/client notes. A source is not considered production-ready until it has:

- access method confirmed
- rate limits understood
- terms/usage constraints checked
- mocked tests
- real one-company smoke test
- normalized output schema
- database storage path

## Company Segment Catalog

Every source is judged against the six client-defined segments. Source selection happens
after the company is classified, because no single source covers all six.

| Code | Segment |
| --- | --- |
| `INDIA_LISTED` | India-listed |
| `INDIA_UNLISTED_FUNDED` | India-unlisted-funded |
| `INDIA_UNLISTED_NON_FUNDED` | India-unlisted-non-funded |
| `FOREIGN_LISTED` | Foreign-listed |
| `FOREIGN_UNLISTED_FUNDED` | Foreign-unlisted-funded |
| `FOREIGN_UNLISTED_NON_FUNDED` | Foreign-unlisted-non-funded |

Classification lives in `app/companies/segments.py` and is persisted on `companies.company_segment`.
Listed companies are decided by ticker/exchange alone. Unlisted companies also need
`companies.funding_status`; when funding is unknown the segment is deliberately left `NULL`
rather than guessed, so the gap stays visible.

## Candidate Sources

| Source | Candidate KPI | Access | Status | Notes |
| --- | --- | --- | --- | --- |
| NewsAPI | layoff news, restructuring news, funding/news events | API key | Implemented | Stores raw articles and KPI counts. Coverage depends on NewsAPI plan. |
| Data.gov.in MCA Company Master Data | CIN, legal name, incorporation date, company status | API key | Implemented, source unstable | Upstream returns timeouts and 502s. Failures are recorded in `collection_runs` so the pipeline continues. Not a blocker for other sources. |
| Yahoo Finance chart API (`/v8/finance/chart`) | price, 52-week drawdown, volatility, max drawdown, volume | **No key** | Implemented and smoke-tested | Not present in the Colab notebook. Covers `INDIA_LISTED` and `FOREIGN_LISTED`. |
| Yahoo Finance chart (indices/FX/commodities) | USD/INR, gold, US T-bill rate, NIFTY 50, NIFTY IT, S&P 500 | **No key** | Implemented and smoke-tested | Covers the Phase 3 industry-level and macro covariate families from the endpoint already in use. Stored with `company_id = NULL`. |
| GDELT DOC 2.0 (`api.gdeltproject.org`) | news tone/sentiment, coverage volume, article list | **No key** | Implemented and smoke-tested | Not in the Colab notebook. Free and, unlike the NewsAPI developer tier, carries no restriction on production use. Rate limited to ~1 request/5s, so it runs as a scheduled batch. |
| Screener.in | listed-company financial KPIs | Public website | Investigate | Listed Indian companies only. Need to confirm scraping terms and page stability. |
| Apify Glassdoor actor (`burbn/glassdoor-company-search`) | 10 workplace ratings, review/salary volume, **open job count** | API token, **billed ~$0.10/company** | Implemented and smoke-tested | Re-added to scope by the client on 2026-09-09. Job count also partly covers the Hiring row. Matches are identity-checked before use. |
| Company careers pages/job boards | job postings | Scraper/API varies | Later | High value but fragmented. Needs source-by-source testing. |
| Funding/news databases | funding events | API/scraper varies | Later | Need client-provided API keys or approved data source. |

## Tested On 2026-09-08

The brief was "try getting data from APIs which are not there in the notebook". Each
candidate below was called live with a real company before any adapter was written.

| Source / endpoint | Auth | Result | Decision |
| --- | --- | --- | --- |
| Yahoo Finance `v8/finance/chart/{symbol}` | none | HTTP 200. `TCS.NS`, `NYKAA.NS`, `FRSH`, `SWIGGY.NS`, `DELHIVERY.NS`, `OLAELEC.NS`, `PAYTM.NS`, `INFY.NS` all returned meta plus a full daily close series. | **Adopted** |
| Yahoo Finance `v10/finance/quoteSummary` | crumb/cookie | HTTP 401 `Invalid Crumb`. Would have supplied employee count and financials. | **Rejected.** Do not build on it. |
| OpenCorporates `v0.4/companies/search` | paid token | HTTP 401 `Invalid Api Token`. | Rejected until the client funds a token. |
| SEC EDGAR `company_tickers.json` / XBRL | none, needs User-Agent | HTTP 200. Real filings data. | Shortlisted for `FOREIGN_LISTED` fundamentals. Next candidate. |
| Wikidata `wbsearchentities` | none | HTTP 200. Resolved Razorpay to `Q109985545`. | Shortlisted for identity/enrichment across all six segments, not for KPIs. |
| Data.gov.in MCA | API key | Timeouts / 502, as reported by the client. | Left in place, deprioritised. |

Registry corrections found while testing tickers against Yahoo:

- `ZOMATO.NS` is dead. The company is now `Eternal Limited` (`ETERNAL.NS`).
- Groww is listed as `Billionbrains Garage Ventures Limited` (`GROWW.NS`), no longer unlisted.
- Pine Labs is listed as `Pine Labs Limited` (`PINELABS.NS`), no longer unlisted.

Run `python scripts/seed_companies.py --update-existing` to push registry corrections into
an already-seeded database.

## Yahoo Finance Adapter

Chosen as the next production source because it needs no API key, which unblocks progress
while NewsAPI and Data.gov keys are being sorted out.

KPIs written to `kpi_observations`:

| KPI | Unit | Meaning |
| --- | --- | --- |
| `market_price` | quote currency | Latest regular market price |
| `market_price_change_pct_period` | percent | Change across the requested window |
| `market_drawdown_from_52w_high_pct` | percent | Distance below the 52-week high; higher means more distress |
| `market_premium_over_52w_low_pct` | percent | Distance above the 52-week low |
| `market_max_drawdown_pct_period` | percent | Worst peak-to-trough decline in the window |
| `market_annualized_volatility_pct` | percent | Annualized stdev of daily log returns |
| `market_trading_volume` | shares | Latest regular market volume |

Sign convention: every `*_drawdown_pct` value is positive when the price sits below its
reference level, so higher always means higher risk.

Limitations:

- Listed companies only. Unlisted companies return `status: not_applicable`.
- Requires a correct ticker/exchange pair. A stale ticker returns `record_found: false`.
- Undocumented public endpoint with no published rate limit or SLA. Treat availability as
  best-effort and keep the failure path (`collection_runs.status = failed`) in place.
- Only `NSE`, `BSE`, `NASDAQ`, `NYSE`, `LSE`, `TSX`, `ASX`, `SGX` are mapped to Yahoo
  symbol suffixes. Any other exchange raises `UnsupportedExchangeError` and is reported as
  `not_applicable` rather than silently guessed.


## GDELT Adapter

Chosen because the client asked for sources outside the notebook, and because it resolves a
licensing problem rather than only adding data.

**Why it matters beyond being free:** NewsAPI's Developer tier is restricted by its own terms
to development and testing, not production. The planned product is a free public tool, which
is production. GDELT has no such restriction, so it is the only zero-cost path to a launchable
news signal. See [source-costs.md](source-costs.md).

KPIs written to `kpi_observations`:

| KPI | Unit | Direction | Meaning |
| --- | --- | --- | --- |
| `gdelt_avg_tone` | tone | lower is worse | Mean daily tone across the window, roughly -10 to +10 |
| `gdelt_worst_day_tone` | tone | lower is worse | The single most negative day |
| `gdelt_negative_day_pct` | percent | higher is worse | Share of days where coverage was net negative |
| `gdelt_tone_decline` | tone | higher is worse | Earlier-half mean minus recent-half mean; positive means coverage is worsening |
| `gdelt_volume_mean_pct` | percent | informational | Average share of worldwide coverage |
| `gdelt_volume_spike_ratio` | ratio | higher is worse | Peak coverage over its own average; a burst usually means an event |
| `gdelt_article_count` | count | informational | Articles sampled |
| `gdelt_tone_days` | count | informational | Days of tone data returned |

Tone is the one KPI family in this project where **lower is worse**, because that is GDELT's
own scale. The derived signals above are named so their direction is unambiguous, and the
report marks the risk-shaped ones explicitly.

Limitations, all measured on 2026-09-09:

- **Rate limited.** GDELT asks for one request every five seconds. In testing, even six-second
  spacing drew HTTP 429 after a burst. It also answers with a plain-text notice under HTTP 200
  instead of a proper error, so both cases are detected and raised as `GdeltRateLimitError`.
- **Three requests per company** (tone, volume, articles), so roughly 25 seconds per company
  with the 8-second floor used here. At 500 companies a full refresh is about 3.5 hours.
- **Therefore batch-only.** `scripts/collect_gdelt_batch.py` is the supported path. The live
  endpoint can skip it with `include_slow_sources: false` and read stored values instead.
- **Name ambiguity.** Tone is computed over all global coverage matching the company name, so
  a common name pulls in unrelated articles. Distinctive names are far more reliable.
- No authentication, no account, no published quota beyond the pacing request.

## Segment Coverage Today

| Segment | NewsAPI | MCA/Data.gov | Yahoo Finance | GDELT |
| --- | --- | --- | --- | --- |
| `INDIA_LISTED` | yes | yes (unstable) | **yes** | **yes** |
| `INDIA_UNLISTED_FUNDED` | yes | yes (unstable) | no | **yes** |
| `INDIA_UNLISTED_NON_FUNDED` | yes | yes (unstable) | no | **yes** |
| `FOREIGN_LISTED` | yes | no | **yes** | **yes** |
| `FOREIGN_UNLISTED_FUNDED` | yes | no | no | **yes** |
| `FOREIGN_UNLISTED_NON_FUNDED` | yes | no | no | **yes** |

GDELT covers all six segments, so every segment now has at least two working sources. The unlisted segments still have no *financial* source beyond news and tone. That is the largest remaining gap
and the reason funding/startup databases need either a client-provided key or an approved
public alternative.

## First Production Source Recommendation

NewsAPI remains the baseline because it covers all six segments. Yahoo Finance is the
second production source because it is keyless, adds genuine market-risk KPIs, and covers
both listed segments.

## Do Not Do Yet

- Do not scrape logged-in, CAPTCHA, or paywalled data.
- Do not rotate API keys unless they are authorized keys from the client and provider terms allow it.
- Do not store API keys in notebooks or source code.
- Do not build ML scores before the source pipeline and target definition are stable.
- Do not build on Yahoo `quoteSummary`; it is crumb-gated and returns 401.
- Do not run the Glassdoor actor across the whole pilot universe without a budget decision.

## Glassdoor / Apify Adapter

Added because the client explicitly re-added work-culture data to scope on 2026-09-09.
Glassdoor closed its own public API in 2024, so a third-party actor is the only route.

### Cost, measured

| Item | Value |
| --- | --- |
| Billing model | Pay per event (actor start + result) |
| Measured cost | **$0.10 per company per run** |
| Account plan | Apify FREE, $5/month credit |
| Companies per month on that credit | **~50** |
| Cost for a 500-company refresh | **~$50** |

The pilot universe is 200-500 companies, so a single full refresh exceeds the free tier by
roughly 10x. For that reason the actor is marked **metered** in `source_selection.py` and is
skipped by default; `POST /collections/company` runs it only when `include_metered_sources`
is true. The estimated cost of every run is written to `collection_runs.metadata`.

### Identity guard

The actor returns its top search hit with no verification. A live query for `Zepto` returned
**`Zepto (Mexico)`** with one review, a completely different company. Every result is therefore
scored by `match_confidence()`:

| Case | Result | Confidence |
| --- | --- | --- |
| `ZS Associates` -> `ZS Associates` | exact | high |
| `Infosys` -> `Infosys Limited` | legal suffix only | high |
| `Tata Consultancy Services` -> `Tata Consultancy` | shortened name | medium |
| `Zepto` -> `Zepto (Mexico)` | extra qualifier | **low, rejected** |
| `Swiggy` -> `Swiggy Instamart` | different brand | **low, rejected** |
| `Razorpay` -> `Stripe` | unrelated | **low, rejected** |

A low-confidence result is still stored in `source_records` for audit, but never becomes a
`kpi_observation` and never updates the company row. Status comes back as `match_rejected`.

### Limitations

- Point-in-time only. No review dates, so **velocity and recency cannot be computed** from a
  single call; they need repeated collection over months.
- Headcount is a coarse band (`"501 to 1000 Employees"`), not a number and not a trend.
- AmbitionBox's explicit **job-security sub-score has no Glassdoor equivalent** and is not covered.
- Ratings are lower-is-riskier, the inverse of the market drawdown KPIs. The direction is
  recorded in `workplace_signals.LOWER_IS_RISKIER` rather than left implicit.

