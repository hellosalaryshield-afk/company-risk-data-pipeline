# Demo Guide

A fifteen-minute walkthrough for showing the client where the project stands. Every command
here has been run against the live database and works.

## Before you start

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs`. If you are demoing the deployed copy, use
`https://company-risk-data-pipeline.onrender.com/docs` instead. Render's free tier sleeps, so
open it a minute early and let it wake up.

Have a PDF pre-generated so you are not waiting on a live call:

```powershell
python scripts/generate_report.py TCS --out-dir reports
```

## The one sentence to open with

> You type a company name, the backend works out what kind of company it is, calls only the
> sources that apply to that kind, stores everything, and hands you back a PDF. What it does
> not do is guess a risk score, because the model is a later phase.

## Step 1 — It asks instead of guessing

`POST /companies/resolve` with `{"query": "Tata"}`

```
status: ambiguous
candidates: ["Tata Consultancy Services"]
```

**Say:** the brief asks for a clarification step on ambiguous names rather than silently
picking one. Typing a half-name gives you candidates to choose from, not a wrong answer.

Then `{"query": "Bundl Technologies"}`:

```
Bundl Technologies -> Swiggy   (INDIA_LISTED)
```

**Say:** the alias registry means the legal entity name resolves to the company people
actually know.

## Step 2 — One call, every applicable source

`POST /collections/company` with `{"company_name": "TCS"}`

Point at the `sources` array in the response. Each source reports its own status:

```
newsapi                       completed
yahoo_finance_chart           completed
gdelt_doc                     completed
data_gov_mca_company_master   source_failed   Data.gov.in timed out
apify_glassdoor_company_search  skipped       billed per call, not requested
```

**Say three things:**

1. Sources are chosen by company segment. An unlisted company does not get a share-price
   call; a foreign company does not get an MCA call.
2. A failing source does not break the run. Data.gov has been down for a week and TCS still
   returned 12 KPIs.
3. Anything that costs money is off unless you ask for it, so a routine refresh cannot run
   up a bill.

## Step 3 — The PDF

Open `GET /companies/1/report.pdf` in the browser, or show the pre-generated file.

Walk through it top to bottom:

- **The banner**: "This is a data collection report, not a risk score." Say this out loud.
  It is the most important line in the demo.
- **Company identity and segment**, with the reason it was classified that way.
- **Collected signals** with trend arrows and, where relevant, "higher = more risk".
- **Source status**, including failures. The client can see Data.gov is down rather than
  taking your word for it.
- **Macro context**: USD/INR, gold, interest rate, NIFTY 50, NIFTY IT, S&P 500.
- **The footer**, which is where your service note goes.

## Step 4 — A company with no data still gets a useful page

`GET /companies/6/summary` (PhonePe)

```
own signals: 0
cohort: segment: INDIA_UNLISTED_FUNDED
estimates: 5
```

**Say:** the brief asks for a fallback that blends toward the sector or cohort estimate
rather than returning nothing. A company nobody has collected yet still gets context from
comparable companies, and every borrowed number is labelled as the cohort's, not the
company's. Before this, twelve of seventeen companies rendered a blank page.

## Step 5 — The part that protects the model

This is the step that separates the project from a scraping script. Show both:

```
GET /companies/1/features?as_of=2026-06-01   ->   0 safe features, 13 excluded
GET /companies/1/features?as_of=2026-09-09   ->  12 safe features, 21 excluded
```

**Say:** the model has to predict six to nine months ahead, so a feature can only contain
what was actually known on the prediction date. Ask for June and you get what was knowable in
June, which right now is nothing because collection started later. That is the honest answer.
Anything excluded says why.

Then `GET /leakage-check`:

```
checked=147  safe=121  issues=26  impossible timestamps=45
```

**Say:** the brief requires a leakage check with mentor sign-off before modelling. This is
it, and it is already finding real problems: Glassdoor ratings carry no publication date so
they cannot enter a backtest, and a batch of older news rows has bad timestamps from a bug
this check found. Both are documented and one has a repair script.

Being able to show the flaws is the point. A pipeline that reports "everything is fine" is
the one you should not trust.

## Step 6 — Refreshing it

```powershell
python scripts/refresh_all.py --skip-slow
```

**Say:** one command refreshes macro plus every company. GDELT runs on its own nightly job
because it is rate limited. Glassdoor runs monthly because it is billed per call.

## If something goes wrong mid-demo

| Problem | What to say and do |
| --- | --- |
| Data.gov returns `source_failed` | Expected, and it is the point. Show it is recorded in `collection_runs` and the other sources still worked. |
| GDELT returns `source_failed` | Rate limit. Show that the stored values are already in the report; the live call is not what feeds the PDF. |
| Render is slow to respond | Free tier cold start. Keep a local server as backup. |
| A company has no data | Use it. Show the cohort fallback instead. |

## The three questions he will probably ask

**"Where is the risk score?"**
Phase 4. The target definition in Phase 1 is not settled yet, and that is his input. Putting
a number on the page now would be inventing one. The pipeline that feeds the model is built
and the report is ready to carry a score the day there is one.

**"What does it cost to run?"**
Today, nothing. GDELT, Yahoo and MCA need no paid key. Apify is the only metered source and
it is off by default. See [source-costs.md](source-costs.md) for verified numbers, including
that NewsAPI's free tier is not licensed for production.

**"When can it go live?"**
The collection pipeline and report are done. What is missing before a public launch is a
decision on funding data for unlisted companies, and the Phase 1 target definition.

## What to leave him with

- The deployed `/docs` link
- One generated PDF
- [docs/kpi-source-matrix.csv](kpi-source-matrix.csv), his own sheet with a verified status
  per row
- [docs/source-costs.md](source-costs.md) for the budget decision
