# System Explainer

Everything the pipeline does, why each piece exists, and how to explain it. Written to be
read top to bottom by someone who has never seen the code.

---

## 1. What this project is

The eventual product answers one question: **can involuntary layoffs be predicted 6 to 9
months in advance, from signals that were visible before the fact?**

That final product is a free public tool where anyone types the company on their offer letter
and gets a risk score with an explanation.

**This repository is not the model.** It is the data platform underneath it: the thing that
takes a company name, finds real signals about that company from public sources, stores them
so they can be trusted later, and produces a report.

The one-sentence version for the client:

> You type a company name. The backend works out what kind of company it is, calls only the
> sources that apply to that kind, stores everything with the dates attached, and hands back
> a PDF. It does not invent a risk score, because the model is a later phase.

---

## 2. The complete flow

```
   "TCS"  (typed by a user)
     │
     ▼
┌─────────────────┐   Is this a company we know? Try aliases.
│ COMPANY RESOLVER│   "Bundl Technologies" → Swiggy
└────────┬────────┘   "Tata" → ambiguous, return candidates, do not guess
         │
         ▼
┌─────────────────┐   Which of the 6 segments? Decided from country,
│  CLASSIFIER     │   ticker/exchange, and funding status.
└────────┬────────┘   Unknown funding → segment stays NULL, not guessed
         │
         ▼
┌─────────────────┐   An unlisted company gets no share-price call.
│ SOURCE SELECTION│   A foreign company gets no MCA call.
└────────┬────────┘   Paid sources are off unless asked for.
         │
    ┌────┼────┬────────┬─────────┬──────────┐
    ▼    ▼    ▼        ▼         ▼          ▼
  News  MCA  Yahoo   GDELT   Glassdoor  NSE deals
    │    │    │        │         │          │
    └────┴────┴────┬───┴─────────┴──────────┘
                   │  Each source runs independently.
                   │  One failing never stops the others.
                   ▼
         ┌───────────────────┐
         │   PostgreSQL      │  raw response  → source_records
         │     (Neon)        │  clean numbers → kpi_observations
         └─────────┬─────────┘  what happened → collection_runs
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
   ┌──────────┐        ┌──────────────┐
   │  REPORT  │        │ POINT-IN-TIME│
   │ HTML/PDF │        │   FEATURES   │
   └──────────┘        └──────────────┘
   for a human          for the future model
```

**The single most important design point:** the report reads from the **database**, not from
live API calls. Scheduled jobs fill the database; a web request just reads it. That is what
makes a free public tool both fast and cheap to host, which the client cares about because he
is paying the infrastructure bill.

---

## 3. The six company segments

The client's own catalog. One source cannot cover all six, so the pipeline routes by segment.

| Segment | Meaning |
| --- | --- |
| `INDIA_LISTED` | Indian company on NSE/BSE |
| `INDIA_UNLISTED_FUNDED` | Indian private company with known funding |
| `INDIA_UNLISTED_NON_FUNDED` | Indian private company, no known funding |
| `FOREIGN_LISTED` | Listed outside India |
| `FOREIGN_UNLISTED_FUNDED` | Foreign private, funded |
| `FOREIGN_UNLISTED_NON_FUNDED` | Foreign private, not funded |

How it is decided:

- **Geography** from `country`
- **Listed or not** from `ticker` + `exchange`
- **Funded or not** from `funding_status`

If funding status is unknown for an unlisted company, **the segment is left NULL rather than
guessed**. A wrong segment sends the wrong sources at a company and quietly corrupts the data,
so an honest blank is better. Those companies still get the sources that work from a name
alone: news and GDELT.

### Which sources run for which segment

| Segment | News | MCA | Yahoo | GDELT | Glassdoor | NSE deals |
| --- | --- | --- | --- | --- | --- | --- |
| `INDIA_LISTED` | yes | yes | **yes** | yes | paid | **yes** |
| `INDIA_UNLISTED_FUNDED` | yes | yes | no | yes | paid | no |
| `INDIA_UNLISTED_NON_FUNDED` | yes | yes | no | yes | paid | no |
| `FOREIGN_LISTED` | yes | no | **yes** | yes | paid | no |
| `FOREIGN_UNLISTED_FUNDED` | yes | no | no | yes | paid | no |
| `FOREIGN_UNLISTED_NON_FUNDED` | yes | no | no | yes | paid | no |
| undetermined | yes | no | no | yes | paid | no |

---

## 4. Every data source

### NewsAPI — layoff and distress keywords
- **Cost:** free tier, but **its terms forbid production use**. Licensed tier is $449/month.
- **Gives:** article counts matching layoff, restructuring, funding and distress keywords.
- **Limit:** free tier is 100 requests/day and one month of history.
- **The problem to raise with the client:** the planned product is a free public tool, which
  is production. The current key is fine for building and is not a legal basis for launch.

### GDELT — news tone and coverage volume
- **Cost:** free, no API key, **no production restriction**. This is why it was added.
- **Gives:** daily average news tone (sentiment), coverage volume, article listings.
- **Limit:** roughly one request per five seconds. Three calls per company means about 25
  seconds each, or ~3.5 hours for 500 companies. **Batch only**, never inside a web request.
- It answers with plain text instead of an error when rate limited, so both cases are caught.

### Yahoo Finance — market risk
- **Cost:** free, no key.
- **Gives:** price, 52-week drawdown, volatility, max drawdown, volume.
- **Also gives macro:** the same endpoint serves USD/INR, gold, US T-bill rate, NIFTY 50,
  NIFTY IT and S&P 500, which covers the Phase 3 macro and industry covariates at no cost.
- **Limit:** listed companies only, and needs a correct ticker. The `quoteSummary` endpoint
  that would have given employee counts is crumb-gated and returns 401 — do not build on it.

### MCA / Data.gov.in — Indian company identity
- **Cost:** free API key.
- **Gives:** CIN, legal name, incorporation date, company status, category.
- **Limit:** **currently returning timeouts and 502s.** The adapter is written and tested;
  the outage is upstream. Failures are recorded and the pipeline continues.

### Glassdoor via Apify — workplace ratings
- **Cost:** **metered.** Charges an actor-start event plus per result, so one company per run
  is the most expensive possible pattern. **Off by default.**
- **Gives:** 13 KPIs including overall rating, CEO approval, business outlook, and **open job
  count**, which partly covers a Hiring row the sheet had listed as paid-only.
- **Limit:** returns an **undated snapshot**, so nothing from it can enter a backtest. It also
  returns its top search hit without verifying identity — a query for Zepto once returned
  "Zepto (Mexico)" — so every match is identity-scored before use.

### NSE bulk and block deals — large-lot selling
- **Cost:** free CSV, no key.
- **Gives:** large-lot buy/sell volume, net quantity, sell share, counterparty count.
- **Limit:** **one trading day per file**, so history exists only if collected every trading
  day. NSE-listed only. Needs a browser user agent or NSE refuses the request.
- **Honest caveat found in testing:** on 2026-09-10 the file held 123 deals across 35 symbols
  and **none of the 17 pilot companies appeared**. Bulk deals are dominated by small caps.
  This source becomes useful as the registry grows toward 200-500 companies including mid and
  small caps. Zero matches on a given day is a normal result, not a failure.

---

## 5. The database, table by table

| Table | Holds | Why it exists |
| --- | --- | --- |
| `companies` | One clean record per company: name, country, sector, segment, ticker, CIN | The canonical identity everything else hangs off |
| `company_aliases` | Every name that means the same company | So "TCS", "Tata Consultancy Services Ltd" and "Bundl Technologies" all resolve correctly |
| `data_sources` | One row per source, with its known limitations | So a report can show source health honestly |
| `collection_runs` | Every attempt, succeeded or failed, with the error | **This is why a failing source is visible instead of silent** |
| `source_records` | The raw API response, unmodified | The brief requires raw responses be stored before feature extraction, so a parsing bug can be fixed without re-fetching |
| `kpi_observations` | One clean number per signal, with all its dates | The actual feature store |

### The three dates on every KPI, and why there are three

This is the part most people get wrong, and it is what makes the model trustworthy.

| Column | Meaning |
| --- | --- |
| `published_at` | When the fact became public |
| `period_end` | The last day the value was computed over |
| `collected_at` | When this pipeline fetched it |

A 30-day layoff-article count published on 7 September, computed over 10 August to 7
September, fetched on 9 September, has three different dates. Storing only one of them makes
it impossible to prove later that the number did not contain the future.

---

## 6. Every endpoint

**Company identity**
| Endpoint | Does |
| --- | --- |
| `POST /companies/resolve` | Name → canonical company, or a candidate list if ambiguous |
| `GET /companies` | List the registry |
| `POST /companies` | Add a company |
| `POST /companies/segment` | Classify one company into the six segments |

**Collection**
| Endpoint | Does |
| --- | --- |
| `POST /collections/company` | **The main one.** Runs every applicable source in one call |
| `POST /collections/news` | NewsAPI only |
| `POST /collections/mca` | MCA only |
| `POST /collections/market` | Yahoo market data only |
| `POST /collections/gdelt` | GDELT tone only |
| `POST /collections/workplace` | Glassdoor only (costs money) |
| `POST /collections/macro` | Macro and index covariates, not company-specific |

**Reading and reporting**
| Endpoint | Does |
| --- | --- |
| `GET /companies/{id}/report` | HTML report |
| `GET /companies/{id}/report.pdf` | PDF report |
| `GET /companies/{id}/summary` | The same content as JSON |
| `GET /sources` | Source registry and known limitations |
| `GET /collection-runs` | Recent runs, including failures |

**For the model**
| Endpoint | Does |
| --- | --- |
| `GET /companies/{id}/features?as_of=...` | Feature vector as it would have been known on that date |
| `GET /leakage-check` | Audit of every stored KPI for point-in-time problems |

---

## 7. Every script, and when to run it

| Script | When |
| --- | --- |
| `seed_companies.py` | Once, to load the registry |
| `refresh_all.py --skip-slow` | **Daily.** Macro plus every company, fast sources |
| `collect_deals.py` | **Every trading day.** One day per file; a missed day is lost forever |
| `collect_gdelt_batch.py` | **Nightly.** Slow because of rate limits |
| `refresh_all.py --include-metered` | **Monthly.** Costs money |
| `generate_report.py <company>` | On demand, writes HTML and PDF |
| `check_leakage.py` | **Before any modelling.** The brief requires mentor sign-off on it |
| `repair_timestamps.py` | Once, to clear bad dates on legacy rows. Dry run unless `--apply` |
| `check_database.py`, `check_resolver.py` | Diagnostics |

---

## 8. The KPIs

39 signals. Convention: unless noted, **higher means more risk**.

**Market (7)** — price, price change, below 52-week high†, above 52-week low, worst drawdown†,
annualised volatility†, trading volume.

**News keywords (5)** — article count, layoffs†, restructuring†, funding, distress†.

**GDELT tone (8)** — average tone, worst day tone, days with negative coverage†, tone
decline†, coverage volume, coverage spike ratio†, article count, days of data.
*Tone is the one family where **lower** is worse, because that is GDELT's own scale. The
derived signals are named so the direction is unambiguous.*

**Glassdoor (13)** — overall rating, CEO approval, business outlook, culture, compensation,
career opportunities, work-life balance, senior management, diversity, recommend-to-friend,
review count, salary count, **open job count**.

**NSE deals (6)** — deal count, buy quantity, sell quantity, net quantity, **sell share†**,
distinct counterparties.

† = higher means more risk.

---

## 9. Point-in-time, and why it decides whether the model is real

A model that predicts six months ahead must only use what was known on the prediction date.
If a feature secretly contains later information, the backtest looks excellent and the live
model fails. This is called leakage and it is the most common way projects like this die.

Ask for a company's vector on any date:

```
GET /companies/1/features?as_of=2026-06-01
```

A value is **excluded** if:
- it has no publication date, so it cannot be verified
- it was published after the prediction date
- its computation window runs past the prediction date
- it was backfilled from a source that **revises** its figures, like MCA

A value is **kept with a warning** if it was backfilled from a source that never restates.
Everything in the database was collected after any past date, so treating backfill itself as
disqualifying would empty every historical vector and make backtesting impossible.

### What the audit currently finds

```
python scripts/check_leakage.py
```

- **Glassdoor: 0 of 26 rows usable in a backtest.** The actor returns an undated snapshot.
  Fine for a current report, must never enter the model.
- **GDELT 8/8, NewsAPI 50/50, Yahoo 63/63 backtest-safe.**
- **45 legacy news rows have impossible timestamps** — a bug this audit found, where
  `published_at` came from the application clock and `collected_at` from the database clock,
  so published landed seconds after collected. The collector is fixed;
  `repair_timestamps.py --apply` clears the bad dates on the old rows.

**Being able to show the flaws is the point.** A pipeline that reports everything is fine is
the one nobody should trust.

---

## 9b. Historical data

The client's question: is there any history, or does the model have to wait?

**For listed companies, history now exists.** The backfill reaches 1996. For everything else,
history accrues forward from the day collection starts.

| Source | History | Detail |
| --- | --- | --- |
| Yahoo market | **1996 to now** | Backfilled. 21,488 rows, 1,231 distinct dates |
| GDELT tone | ~24 months | Free and obtainable, **not yet done** |
| EPFO, IBBI | Published archives | Obtainable once those adapters are built |
| NewsAPI | 1 month free | 5 years costs $449/month |
| NSE deals | Forward only | One trading day per file; past days cannot be recovered |
| Glassdoor | Forward only | Undated snapshots, no past history exists at all |
| MCA | None | Current filing state only |

Two details that make the backfill trustworthy rather than merely large:

1. **Trailing windows.** Each historical value uses only the points up to its own date. Using
   the whole series to compute a 2022 figure would put 2026 information inside it.
2. **Measured intervals.** Yahoo returns monthly bars for a 2002 listing and daily bars for a
   2024 one. Annualising daily bars as if monthly understated volatility by 4.6x - Swiggy read
   8.4% where the correct figure is 49.7%. The interval is now inferred from the actual
   spacing between points.

**The honest limit:** four of the six segments are unlisted and have no share price, so they
have no market history and never will. Their history starts today.


## 10. Thin-data companies

Most companies have no collected signals early in a pilot, and a blank page is the worst
answer for someone who typed a real company name. So when a company has no value of its own,
the report falls back to the **median across comparable companies**, choosing the tightest
cohort that actually has data:

```
same sector  →  same segment  →  whole registry
```

Every borrowed figure is labelled with its cohort and peer count, and the report states
plainly that these are not measurements of that company. A company's own value is never
replaced by an estimate.

Before this, 12 of 17 pilot companies rendered an empty report. Now none do.

---

## 11. Where the project stands against the brief

| Phase | Scope | State |
| --- | --- | --- |
| 1 — define the target | What counts as an involuntary layoff event | **Not started. Needs the client.** |
| 2 — pipeline and entity resolution | Registry, aliases, sources, ingestion | **Done** |
| 3 — lagged covariates | Company, external, industry, macro + leakage check | **Mostly done.** Macro, industry, news, market, tone all in. Point-in-time and the leakage check are built. Missing: company-internal hiring trend |
| 4 — model and metrics | Calibrated probability, walk-forward backtest | Not started. Depends on 1 and 3 |
| 5 — EPFO feasibility | Payroll releases, access routes | Not started |
| 6 — serve and refresh | Name in, score and explanation out | **Mostly done.** Resolution, ambiguity handling, thin-data fallback, HTML/PDF, documented refresh. Only the score waits on Phase 4 |

**Sheet tally:** 3 Done, 7 Partial, 15 Pending. Backtest-safe: 5 rows.

Of the 15 pending: 1 the client skipped, 3 blocked by the Data.gov outage, 2 have no source at
all, 1 needs a paid key, 3 depend on a blocked row, and **3 are free and still buildable**
(EPFO, promoter pledge, IBBI insolvency).

---

## 12. What still needs the client

1. **Phase 1 target definition.** Nothing can be modelled until "involuntary layoff event" is
   defined. This is his input, not something to invent.
2. **Funding data budget.** Segments 2 and 5 have only a keyword proxy. Tracxn or Crunchbase
   needs a quote — Crunchbase's free Basic API has been discontinued.
3. **NewsAPI licensing.** Free tier cannot be used in production. Either $449/month or lean on
   GDELT, which is free and unrestricted.
4. **Apify plan**, only if workplace data is wanted at pilot scale.

---

## 13. Talking points

**"Where is the risk score?"**
Phase 4. The target definition is not settled, and that is his call. Putting a number on the
page now would be inventing one. The pipeline that feeds the model is built, and the report is
ready to carry a score the day there is one.

**"What does it cost?"**
Today, nothing. GDELT, Yahoo, NSE and MCA need no paid key. Apify is the only metered source
and it is off by default.

**"How do I know the data is right?"**
Every source's status is in the report, failures included. Every number carries its dates. The
leakage audit is a command anyone can run, and it currently reports real problems rather than
a clean bill of health.

**"When can it go live?"**
The collection pipeline and report are done. What is missing before a public launch is the
Phase 1 target definition and a decision on funding data.

---

## 14. One operational warning

Neon's free tier auto-suspends when idle and takes about 30 seconds to wake. Measured: DNS
resolves instantly, the TCP handshake took 30.3 seconds cold and 3 seconds warm. Render's free
tier does the same.

**Before any demo, open the app a minute early**, or the first company lookup will look broken
when it is only cold.
