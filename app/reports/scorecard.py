"""Everything the Company Scorecard PDF shows, gathered and worded for one company.

The layout follows the Claude Design "Company Scorecard" (design/Scorecard Page.dc.html);
this module only decides what goes in it.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.features.point_in_time import ensure_utc
from app.models.collection import KpiObservation, SourceRecord
from app.models.company import Company
from app.models.registry import McaCompany
from app.models.source import DataSource
from app.pipeline.news_signal_collection import CLASSIFIED_RECORD_TYPE, NEWS_SIGNAL_SOURCE_NAME
from app.scoring.config import load_config
from app.scoring.engine import band_for
from app.scoring.inputs import MCA_SOURCE_NAME, is_listed, latest_news_scan, latest_record_time
from app.scoring.service import score_company
from app.sources.news_classifier import POSITIVE_SIGNALS, RISK_SIGNALS

TIMELINE_DAYS = 30
TIMELINE_ROWS = 5

BAND_STEPS = {"Low": 1, "Moderate": 2, "Elevated": 3, "High": 4}
BAND_VAR = {"Low": "low", "Moderate": "mod", "Elevated": "elev", "High": "high"}
BAND_TEXT_VAR = {"Low": "low-text", "Moderate": "gold-text", "Elevated": "elev-text", "High": "high-text"}
CONFIDENCE_CELLS = {"High": 3, "Medium": 2, "Limited data": 1}

TAG_LABELS = {
    "layoffs": "Layoffs",
    "salary_delay": "Salary delays",
    "hiring_freeze": "Hiring freeze",
    "restructuring": "Restructuring",
    "funding_trouble": "Funding trouble",
    "financial_distress": "Financial pressure",
    "regulatory_legal": "Regulatory / legal",
    "leadership_exit": "Leadership exit",
    "funding_raised": "Funding raised",
    "contract_win": "Contract win",
    "hiring_surge": "Hiring surge",
}

# How each risk type reads inside a sentence ("Recent news points to ...").
RISK_PHRASES = {
    "layoffs": "layoffs",
    "salary_delay": "delayed salaries",
    "hiring_freeze": "a hiring freeze",
    "restructuring": "restructuring",
    "funding_trouble": "funding difficulties",
    "financial_distress": "financial pressure",
    "regulatory_legal": "regulatory or legal trouble",
    "leadership_exit": "senior leaders leaving",
}

PILLAR_LABELS = {"Financial": "Financial", "Sentiment": "Employee sentiment", "News": "News & events", "Profile": "Company profile"}
DRIVER_TAGS = {"News": "News", "Financial": "Financial", "Profile": "Profile", "Sentiment": "Sentiment", "Adjustment": "Method"}

FILINGS_UP_TO_DATE = "✓ Up to date"


def fmt_date(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d %b %Y").lstrip("0")


def build_scorecard(db: Session, company: Company, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    config = load_config()
    score = score_company(db, company, as_of=now, config=config)
    listed = is_listed(company)
    registry = db.get(McaCompany, company.cin) if company.cin else None
    market = latest_market(db, company.id)
    events = timeline_events(db, company.id, now)

    scored = score["score"] is not None
    band = score["band"]
    pillars = {p["name"]: p for p in score["pillars"]}

    view: dict[str, Any] = {
        "name": company.canonical_name,
        "legal_name": registry.company_name if registry else company.legal_name,
        "cin": company.cin,
        "subtitle": " · ".join(part for part in identity_parts(company, registry, listed) if part),
        "mca_status": company.company_status,
        "report_date": fmt_date(now),
        "data_as_of": fmt_date(date.fromisoformat(score["data_as_of"])) if score["data_as_of"] else None,
        "scored": scored,
        "score": score["score"],
        "band": band,
        "band_var": BAND_VAR.get(band, "low"),
        "band_text_var": BAND_TEXT_VAR.get(band, "ink2"),
        "band_steps": BAND_STEPS.get(band, 0),
        "band_index": BAND_STEPS.get(band, 0),
        "band_range": band_range(band, config) if band else None,
        "horizon": score["horizon"],
        "confidence": score["confidence"] if scored else "Not enough data",
        "confidence_cells": CONFIDENCE_CELLS.get(score["confidence"], 0) if scored else 0,
        "coverage_pct": round(score["coverage"] * 100),
        "coverage_segments": round(score["coverage"] * 20),
        "sector_average": score["sector_average"],
        "bands": [
            {"name": name, "var": BAND_VAR[name], "text_var": BAND_TEXT_VAR[name], "range": band_range(name, config)}
            for name in ("Low", "Moderate", "Elevated", "High")
        ],
        "pillars": [pillar_row(name, pillars[name], score, config, listed) for name in ("Financial", "Sentiment", "News", "Profile")],
        "drivers": driver_rows(score["drivers"]),
        "timeline": events,
        "market": market_view(market) if listed else None,
        "listed": listed,
        "exchange": company.exchange,
        "company_rows": company_rows(company, registry, score),
        "sources": source_rows(db, company, market, score),
        "weights_version": score["weights_version"],
    }
    view["confidence_note"] = confidence_note(view)
    view["plain_words"] = plain_words(company, view, score, events, market, listed)
    view["tips"] = tips(view)
    view["markers"] = markers(view)
    view["driver_scale"] = max((abs(d["points"]) for d in view["drivers"]), default=1)
    return view


def identity_parts(company: Company, registry: McaCompany | None, listed: bool) -> list[str | None]:
    state = registry.registered_state.title() if registry and registry.registered_state else None
    if not state and company.country and company.country.lower() != "india":
        state = company.country
    registered = f"Registered {company.incorporation_date.year}" if company.incorporation_date else None
    if listed:
        listing = f"Listed on {company.exchange} ({company.ticker})"
    else:
        listing = "Unlisted (private)"
    return [company.sector, state, registered, listing]


def band_range(band: str, config: dict) -> str:
    bands = config["bands"]
    return {
        "Low": f"0–{bands['low']}",
        "Moderate": f"{bands['low'] + 1}–{bands['moderate']}",
        "Elevated": f"{bands['moderate'] + 1}–{bands['elevated']}",
        "High": f"{bands['elevated'] + 1}–100",
    }[band]


def pillar_row(name: str, pillar: dict, score: dict, config: dict, listed: bool) -> dict:
    value = pillar["score"]
    coverage = round(pillar["coverage"] * 100)
    if name == "Financial":
        if value is None:
            note = "No financial data yet" if listed else "No share price (unlisted) and no funding data yet"
        elif listed:
            note = f"Share price only · {coverage}% of its inputs" if coverage < 100 else "Financial results and share price"
        else:
            note = f"Funding news only · {coverage}% of its inputs"
    elif name == "Sentiment":
        note = "Employee reviews (Glassdoor)" if value is not None else "Employee reviews not available yet"
    elif name == "News":
        note = "Scanned in the last 30 days" if value is not None else "Not scanned in the last 30 days"
    else:
        note = "Company age and filing status" if value is not None else "No company registry data"
    band = band_for(value, config) if value is not None else None
    return {
        "name": PILLAR_LABELS[name],
        "score": round(value) if value is not None else None,
        "note": note,
        "weight_pct": round(pillar["weight"] * 100),
        "band": band,
        "band_var": BAND_VAR.get(band, "low"),
        "band_steps": BAND_STEPS.get(band, 0),
    }


def driver_rows(drivers: list[dict]) -> list[dict]:
    return [
        {"label": d["label"], "tag": DRIVER_TAGS.get(d["pillar"], d["pillar"]), "points": d["points"]}
        for d in drivers
    ]


def timeline_events(db: Session, company_id: int, now: datetime) -> list[dict]:
    """News stories about the company in the last 30 days, one row per story, newest first."""
    rows = db.execute(
        select(SourceRecord.published_at, SourceRecord.title, SourceRecord.normalized_data, SourceRecord.url)
        .join(DataSource, DataSource.id == SourceRecord.source_id)
        .where(
            SourceRecord.company_id == company_id,
            DataSource.name == NEWS_SIGNAL_SOURCE_NAME,
            SourceRecord.record_type == CLASSIFIED_RECORD_TYPE,
            SourceRecord.published_at >= now - timedelta(days=TIMELINE_DAYS),
        )
        .order_by(desc(SourceRecord.published_at))
    ).all()

    stories: list[dict] = []
    seen: set[str] = set()
    for published, title, data, url in rows:
        data = data or {}
        signal = data.get("signal")
        if not data.get("about_company") or signal not in TAG_LABELS:
            continue
        key = data.get("event_id") or url
        if key in seen:
            continue
        seen.add(key)
        stories.append(
            {
                "date": fmt_date(ensure_utc(published)),
                "tag": TAG_LABELS[signal],
                "signal": signal,
                "text": data.get("event") or title,
                "raises": signal in RISK_SIGNALS,
                "lowers": signal in POSITIVE_SIGNALS,
            }
        )
    return stories[:TIMELINE_ROWS]


def latest_market(db: Session, company_id: int) -> dict[str, Any]:
    names = (
        "market_price",
        "market_price_change_pct_period",
        "market_drawdown_from_52w_high_pct",
        "market_max_drawdown_pct_period",
        "market_annualized_volatility_pct",
    )
    out: dict[str, Any] = {}
    for name in names:
        row = db.execute(
            select(KpiObservation.value_numeric, KpiObservation.unit, KpiObservation.observed_at, KpiObservation.collected_at)
            .where(KpiObservation.company_id == company_id, KpiObservation.kpi_name == name)
            .order_by(desc(KpiObservation.observed_at), desc(KpiObservation.id))
            .limit(1)
        ).first()
        if row and row[0] is not None:
            out[name] = {"value": float(row[0]), "unit": row[1], "observed_at": row[2], "collected_at": row[3]}
    return out


CURRENCY = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def market_view(market: dict) -> dict | None:
    if "market_price" not in market:
        return None
    price = market["market_price"]
    symbol = CURRENCY.get((price["unit"] or "").upper(), "")
    change = market.get("market_price_change_pct_period", {}).get("value")

    def pct(name):
        item = market.get(name)
        return f"{item['value']:.1f}%" if item else "—"

    return {
        "price": f"{symbol}{price['value']:,.2f}",
        "price_date": fmt_date(price["observed_at"]),
        "change": f"{'▼' if change < 0 else '▲'} {abs(change):.1f}%" if change is not None else "—",
        "change_label": "Down over 6 months" if change is not None and change < 0 else "Up over 6 months",
        "below_high": pct("market_drawdown_from_52w_high_pct"),
        "worst_fall": pct("market_max_drawdown_pct_period"),
        "volatility": pct("market_annualized_volatility_pct"),
    }


def company_rows(company: Company, registry: McaCompany | None, score: dict) -> list[dict]:
    if not company.cin and not company.company_status:
        return [{"label": "Registry", "value": "Not in the Indian company registry (registered outside India)"}]
    rows = []
    if registry and (registry.company_class or registry.company_category):
        category = (registry.company_category or "").replace("Company ", "").replace("company ", "")
        rows.append({"label": "Entity", "value": ", ".join(p for p in (registry.company_class, category) if p)})
    if company.company_status:
        rows.append({"label": "Status", "value": f"● {company.company_status}", "strong": True})
    if company.incorporation_date:
        rows.append({"label": "Registered", "value": fmt_date(company.incorporation_date)})
    if registry and registry.registered_state:
        rows.append({"label": "State", "value": registry.registered_state.title()})
    overdue = score["inputs"].get("filings_overdue")
    if overdue is not None:
        rows.append({"label": "Filing status", "value": "Overdue" if overdue else FILINGS_UP_TO_DATE, "strong": True})
    return rows


def source_rows(db: Session, company: Company, market: dict, score: dict) -> list[dict]:
    mca = latest_record_time(db, company.id, MCA_SOURCE_NAME, "mca_company_master")
    price = market.get("market_price", {}).get("collected_at")
    news = latest_news_scan(db, company.id, datetime.now(UTC))
    glassdoor = None
    if "glassdoor_rating" in score["inputs"]:
        glassdoor = db.scalar(
            select(KpiObservation.collected_at)
            .where(KpiObservation.company_id == company.id, KpiObservation.kpi_name == "glassdoor_rating")
            .order_by(desc(KpiObservation.collected_at))
            .limit(1)
        )
    return [
        {"label": "MCA company registry", "date": fmt_date(mca), "missing": "Not available" if company.cin else "Not applicable"},
        {
            "label": f"Share price ({company.exchange} via Yahoo Finance)" if is_listed(company) else "Share price",
            "date": fmt_date(ensure_utc(price)) if price else None,
            "missing": "Not listed",
        },
        {"label": "News scan (publisher RSS, AI-checked for relevance)", "date": fmt_date(news), "missing": "Not scanned yet"},
        {"label": "Employee reviews", "date": fmt_date(ensure_utc(glassdoor)) if glassdoor else None, "missing": "Not available yet"},
    ]


def confidence_note(view: dict) -> str:
    pct = view["coverage_pct"]
    if not view["scored"]:
        if not view["listed"]:
            return "Young or private companies often have no share price and few public reviews. That is normal, not a warning sign."
        return "Too few of the inputs we look for were available to give a fair score."
    if view["confidence"] == "High":
        return f"{pct}% of the inputs we look for were available."
    if view["confidence"] == "Medium":
        return f"{pct}% of the inputs we look for were available. Read this score with some caution."
    return f"Only {pct}% of the inputs we look for were available. Read this score as a rough guide, not a firm answer."


def plain_words(company: Company, view: dict, score: dict, events: list[dict], market: dict, listed: bool) -> str:
    name = company.canonical_name
    sentences = []

    change = market.get("market_price_change_pct_period", {}).get("value") if listed else None
    if not listed:
        sentences.append(f"{name} is a private company, so there is no share price to read.")
    elif change is not None and change <= -15:
        sentences.append(f"{name}'s share price is down {abs(change):.0f}% over six months.")
    elif change is not None and change >= 15:
        sentences.append(f"{name}'s share price is up {change:.0f}% over six months.")
    elif change is not None:
        sentences.append(f"{name}'s share price has been fairly steady over six months.")

    risk_types = []
    for event in events:
        if event["raises"] and RISK_PHRASES[event["signal"]] not in risk_types:
            risk_types.append(RISK_PHRASES[event["signal"]])
    has_layoffs = any(event["signal"] == "layoffs" for event in events)
    if risk_types:
        listed_types = risk_types[0] if len(risk_types) == 1 else ", ".join(risk_types[:-1]) + " and " + risk_types[-1]
        sentences.append(f"Recent news points to {listed_types}.")
    elif events:
        sentences.append("Recent news about the company is mostly positive or routine.")
    else:
        sentences.append("We found no significant news about the company in the last 30 days.")
    if not has_layoffs:
        sentences.append("We found no reports of layoffs.")

    if not view["scored"]:
        sentences.append("We do not have enough data yet to give a fair score.")
    elif view["confidence"] == "Limited data":
        sentences.append(
            "Much of the data we would normally use, such as employee reviews, is not available yet, "
            "so treat this score as a rough guide."
        )
    elif view["confidence"] == "Medium":
        sentences.append("Some data is missing, so read the score with some caution.")
    return " ".join(sentences)


def tips(view: dict) -> list[str]:
    band = view["band"]
    if not view["scored"]:
        first = "We could not score this company yet. Check back before you sign."
    elif band == "Low" and view["confidence"] == "Limited data":
        first = "The risk looks low, but it is based on limited data. Re-check before you sign."
    elif band == "Low":
        first = "The risk looks low on the data we have. Keep an eye on the news before you sign."
    elif band == "Moderate":
        first = "Some warning signs are showing. Ask about team stability and recent changes before you sign."
    else:
        first = "Several warning signs are showing. Ask directly about layoffs, funding and runway before you sign."
    return [
        first,
        "Ask your hiring manager how the team you are joining fits into current plans.",
        "Keep 3 to 6 months of expenses saved, whichever company you join.",
    ]


def markers(view: dict) -> dict | None:
    """Where the company and sector-average labels sit on the band scale, so they never collide."""
    if not view["scored"]:
        return None
    score = view["score"]
    average = view["sector_average"]
    # The two labels face away from each other, as in the design: whichever marker is
    # further left labels to its left. A score near the right edge always labels left.
    average_left = average is not None and average < score
    return {
        "score": score,
        "average": average,
        "score_label_left": (average is not None and score < average) or score > 85,
        "average_label_left": average_left,
    }
