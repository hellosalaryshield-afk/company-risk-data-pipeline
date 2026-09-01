# Source Feasibility Tracker

This document tracks candidate sources from the Colab/client notes. A source is not considered production-ready until it has:

- access method confirmed
- rate limits understood
- terms/usage constraints checked
- mocked tests
- real one-company smoke test
- normalized output schema
- database storage path

## Candidate Sources

| Source | Candidate KPI | Access | Status | Notes |
| --- | --- | --- | --- | --- |
| NewsAPI | layoff news, restructuring news, funding/news events | API key | Candidate | Good first adapter because it returns JSON and supports query/date filters. |
| Data.gov.in MCA Company Master Data | CIN, legal name, incorporation date, company status | API key | Candidate | Useful for Indian company identity verification. Need validate exact filters and data freshness. |
| Screener.in | listed-company financial KPIs | Public website | Investigate | Likely only useful for listed Indian companies. Need confirm scraping terms and page stability. |
| Apify Glassdoor actor | ratings, review count, job count | Third-party actor/API | Skip for now | Work-culture data is out of current internship scope. Could revisit only if client explicitly asks. |
| Company careers pages/job boards | job postings | Scraper/API varies | Later | High value but fragmented. Needs source-by-source testing. |
| Funding/news databases | funding events | API/scraper varies | Later | Need client-provided API keys or approved data source. |

## First Production Source Recommendation

Start with NewsAPI because:

- it already appears in the Colab
- it has a normal API response
- it directly supports layoff/restructuring/funding signals
- it can be mocked easily in tests
- it can store both raw articles and extracted KPI counts

## Do Not Do Yet

- Do not scrape logged-in, CAPTCHA, or paywalled data.
- Do not rotate API keys unless they are authorized keys from the client and provider terms allow it.
- Do not store API keys in notebooks or source code.
- Do not build ML scores before the source pipeline and target definition are stable.
