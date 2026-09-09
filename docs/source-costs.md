# Source Costs And Licensing

Prices verified against vendor pages on 2026-09-09. Anything not verified is marked as
such rather than estimated, because a wrong number here turns into a wrong budget ask.

Pilot scale assumed below: **500 companies**, refreshed **monthly**.

## The licensing problem you must decide first

**NewsAPI's free Developer tier forbids production use.** Its own pricing page states the
plan is "for development and testing" and cannot be used in staging or production. The
project's goal is a free public tool, which is production. So the current NewsAPI key is
fine for building and testing, and is not a legal basis for launch.

| Option | Cost | Consequence |
| --- | --- | --- |
| NewsAPI Business | **$449/month** | Licensed, 250,000 requests/month, 5-year article history |
| Move news signals to GDELT | **$0** | Free, no licence restriction, adds sentiment. Rate limited, so batch only |
| Launch on the free NewsAPI key | $0 | Breaches their terms. Not recommended |

GDELT was implemented for this reason. It is not only cheaper, it is the only free option
that is actually licensed for a public tool.

## Verified prices

| Source | Model | Verified price | At 500 companies/month |
| --- | --- | --- | --- |
| **GDELT DOC 2.0** | Free, no key | $0 | **$0** |
| **Yahoo Finance chart** | Undocumented public endpoint, no key | $0 | **$0** |
| **Data.gov.in MCA** | Free API key | $0 | **$0** (currently returning 502/timeouts) |
| **NewsAPI Developer** | Free | $0 | **Not licensed for production** |
| **NewsAPI Business** | Subscription | $449/mo | **$449/mo** |
| **NewsAPI Advanced** | Subscription | $1,749/mo | $1,749/mo |
| **Apify platform** | Subscription + usage credits | Free $5/mo · Starter $19/mo · Scale $199/mo · Business $999/mo | See below |
| **Apify Glassdoor actor** | Pay-per-event | "from $5.00 / 1,000 results" plus a per-run start event | See below |
| **Crunchbase** | Enterprise/Applications licence | **Basic API discontinued** — no self-serve tier, sales contact only | Unknown, needs a quote |
| **Tracxn** | Enterprise | Not published | Unknown, needs a quote |
| **MCA21 documents** | Per document | ~₹100/document | ~₹50,000 one-off |
| **FileSure** | Per company/year | ~₹330/company/year | ~₹165,000/year |

Apify credits do **not** roll over month to month.

## The Apify number needs checking in your console

The repo currently records `$0.10 per company per run`. The actor's public page says
"from $5.00 / 1,000 results", which is $0.005 per result. Those disagree by 20x.

The Apify API confirms the actor is `PAY_PER_EVENT` with **two** billable events:

- `apify-actor-start`
- `apify-default-dataset-item` (the result)

The per-event prices are not public. So a one-company run pays a full actor-start fee plus
one result, which is why the real figure is far above $0.005. **Read the actual charge in
Apify → Billing → Usage** for the runs already made and correct `COST_PER_RUN_USD` in
`app/sources/glassdoor.py` to the measured value.

If `$0.10` holds:

| Plan | Monthly credit | Companies covered per month |
| --- | --- | --- |
| Free | $5 | 50 |
| Starter $19 | $19 | 190 |
| Scale $199 | $199 | 1,990 |

At 500 companies/month that is **$50/month of credit**, so Starter ($19) is not enough and
Scale ($199) covers it with room to spare.

**Optimisation worth doing before buying anything:** because `apify-actor-start` is charged
per run, one company per run is the most expensive possible pattern. If the actor accepts
several queries in one run, batching would amortise the start fee across many companies and
could cut the bill substantially. Test this before recommending a plan.

## Recommended budget ask

| Priority | Item | Cost | Why |
| --- | --- | --- | --- |
| 1 | Nothing | $0 | GDELT, Yahoo and MCA cover market, macro, news and sentiment for free |
| 2 | Apify Starter or Scale | $19–$199/mo | Only if workplace ratings and job counts are wanted at pilot scale |
| 3 | Funding data quote | Unknown | Segments 2 and 5 have no real funding source. Get a Tracxn quote before committing |
| 4 | NewsAPI Business | $449/mo | Only if NewsAPI-specific coverage is needed beyond GDELT |

The honest recommendation is to launch on tier 1, which costs nothing, and treat tiers 2-4
as decisions the client makes with real numbers in front of them.

## What is still free and unbuilt

| Row | Source | Cost | Note |
| --- | --- | --- | --- |
| 16 | IBBI / NCLT insolvency lists | $0 | Downloadable files. A company entering CIRP is an unambiguous distress signal |
| 18 | NSE/BSE bulk and block deals | $0 | Daily files. NSE blocks unfamiliar user agents, so needs care |
| 6 | EPFO monthly payroll | $0 | Aggregate only, so it is a macro covariate rather than company-level |
| 9 | Promoter pledge | $0 | Free but only as unstructured exchange announcements; needs a scraper |
