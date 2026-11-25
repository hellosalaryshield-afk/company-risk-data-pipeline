from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.database.session import get_db_session
from app.main import app
from app.models.collection import CollectionRun, KpiObservation, SourceRecord
from app.models.source import DataSource
from app.pipeline.news_signal_collection import CLASSIFIED_RECORD_TYPE, get_or_create_news_signal_source
from app.reports import scorecard as scorecard_module
from app.reports import scorecard_render
from app.reports.scorecard import build_scorecard, markers
from app.reports.scorecard_render import ScorecardRenderError, render_scorecard_html
from app.scoring.config import load_config

NOW = datetime(2026, 11, 10, tzinfo=UTC)
SMALL_UNIVERSE = {**load_config(), "min_peer_group_size": 2, "min_values_for_percentile": 2}


@pytest.fixture
def small_universe(monkeypatch):
    monkeypatch.setattr(scorecard_module, "load_config", lambda: SMALL_UNIVERSE)


@pytest.fixture
def market_source(db_session):
    source = DataSource(name="yahoo_finance_chart", source_type="api")
    db_session.session.add(source)
    db_session.session.flush()
    return source


def listed_company(db_session, market_source, name, change):
    company = db_session.create_company(
        canonical_name=name, aliases=[], ticker=name.upper(), exchange="NSE", sector="Quick Commerce",
        company_status="Active", incorporation_date=date(2013, 12, 26),
    )
    for kpi, value, unit in (
        ("market_price", 178.15, "INR"),
        ("market_price_change_pct_period", change, "percent"),
        ("market_drawdown_from_52w_high_pct", 57.7, "percent"),
        ("market_max_drawdown_pct_period", 40.5, "percent"),
        ("market_annualized_volatility_pct", 47.5, "percent"),
    ):
        db_session.session.add(
            KpiObservation(
                company_id=company.id, source_id=market_source.id, kpi_name=kpi, value_numeric=Decimal(str(value)),
                unit=unit, observed_at=NOW - timedelta(days=7), collected_at=NOW - timedelta(days=7),
            )
        )
    db_session.session.add(
        CollectionRun(company_id=company.id, status="completed", started_at=NOW - timedelta(days=1),
                      extra_metadata={"source": "gemini_news_signals"})
    )
    db_session.session.flush()
    return company


def news(db_session, company, signal, days_ago, event_id, event, about=True):
    source = get_or_create_news_signal_source(db_session.session)
    db_session.session.add(
        SourceRecord(
            company_id=company.id, source_id=source.id, record_type=CLASSIFIED_RECORD_TYPE,
            url=f"https://n.example/{event_id}-{days_ago}", title=event, published_at=NOW - timedelta(days=days_ago),
            raw_data={}, normalized_data={"about_company": about, "signal": signal, "severity": 2, "event": event, "event_id": event_id},
        )
    )
    db_session.session.flush()


@pytest.fixture
def swiggy(db_session, market_source, small_universe):
    company = listed_company(db_session, market_source, "Swiggy", -36.1)
    listed_company(db_session, market_source, "Peer One", 4.0)
    listed_company(db_session, market_source, "Peer Two", 12.0)
    news(db_session, company, "restructuring", 16, "e1", "Swiggy moves Instamart to an inventory-led model")
    news(db_session, company, "financial_distress", 18, "e2", "Swiggy reports a Q2 loss of 922 crore")
    news(db_session, company, "financial_distress", 17, "e2", "Same loss story from another outlet")  # same story
    news(db_session, company, "contract_win", 21, "e3", "Swiggy partners with JioHotstar")
    news(db_session, company, "neutral", 5, "e4", "Swiggy launches a new app icon")
    news(db_session, company, "layoffs", 3, "e5", "Unrelated company layoffs", about=False)
    news(db_session, company, "layoffs", 45, "e6", "Old layoff story")  # outside the 30-day timeline
    return company


def test_scorecard_view_for_a_scored_listed_company(db_session, swiggy):
    view = build_scorecard(db_session.session, swiggy, now=NOW)

    assert view["scored"] is True
    assert 0 <= view["score"] <= 100
    assert view["band"] in {"Low", "Moderate", "Elevated", "High"}
    assert view["subtitle"] == "Quick Commerce · Registered 2013 · Listed on NSE (SWIGGY)"
    assert [p["name"] for p in view["pillars"]] == ["Financial", "Employee sentiment", "News & events", "Company profile"]
    sentiment = view["pillars"][1]
    assert sentiment["score"] is None and sentiment["note"] == "Employee reviews not available yet"
    assert view["market"]["price"] == "₹178.15"
    assert view["market"]["change"] == "▼ 36.1%"
    assert len(view["tips"]) == 3


def test_timeline_is_one_row_per_story_newest_first_and_about_the_company(db_session, swiggy):
    view = build_scorecard(db_session.session, swiggy, now=NOW)

    texts = [e["text"] for e in view["timeline"]]
    assert texts == [
        "Swiggy moves Instamart to an inventory-led model",
        "Same loss story from another outlet",
        "Swiggy partners with JioHotstar",
    ]
    assert view["timeline"][0]["tag"] == "Restructuring" and view["timeline"][0]["raises"]
    assert view["timeline"][1]["tag"] == "Financial pressure"
    assert view["timeline"][2]["lowers"] and not view["timeline"][2]["raises"]


def test_plain_words_describe_the_evidence(db_session, swiggy):
    words = build_scorecard(db_session.session, swiggy, now=NOW)["plain_words"]

    assert words.startswith("Swiggy's share price is down 36% over six months.")
    assert "Recent news points to restructuring and financial pressure." in words
    assert "We found no reports of layoffs." in words


def test_a_company_without_enough_data_gets_no_score_and_says_so(db_session, small_universe):
    startup = db_session.create_company(canonical_name="Kitebird", aliases=[], sector="EV Logistics", company_status="Active")

    view = build_scorecard(db_session.session, startup, now=NOW)

    assert view["scored"] is False and view["score"] is None
    assert view["confidence"] == "Not enough data"
    assert "private company" in view["plain_words"]
    assert "We do not have enough data yet to give a fair score." in view["plain_words"]
    assert view["market"] is None
    assert view["tips"][0].startswith("We could not score this company yet")


def test_the_score_label_sits_on_the_side_with_room():
    # A low score labels to the right of its marker, a high one to the left, so the label
    # never runs off the edge of the scale (a long company name once did).
    assert markers({"scored": True, "score": 10, "sector_average": 19}) == {
        "score": 10, "average": 19, "score_label_left": False,
    }
    assert markers({"scored": True, "score": 72, "sector_average": 40})["score_label_left"] is True
    assert markers({"scored": True, "score": 92, "sector_average": None})["score_label_left"] is True
    assert markers({"scored": False}) is None


def test_the_scale_shows_a_short_label_and_spells_out_the_sector_average(db_session):
    from app.reports.scorecard_render import render_scorecard_html

    company = db_session.create_company(canonical_name="Sun Pharma Advanced Research Company Ltd", ticker="SPARC",
                                        exchange="NSE")
    view = build_scorecard(db_session.session, company)
    view.update(scored=True, score=10, sector_average=19, markers=markers({"scored": True, "score": 10, "sector_average": 19}))

    html = render_scorecard_html(view, "night")

    assert "Score 10</div>" in html
    assert "Sun Pharma Advanced Research Company Ltd 10" not in html
    assert "Dashed line: sector average 19" in html


def test_html_renders_both_themes_with_the_company_data(db_session, swiggy):
    view = build_scorecard(db_session.session, swiggy, now=NOW)

    paper = render_scorecard_html(view, "paper")
    night = render_scorecard_html(view, "night")

    assert "Swiggy" in paper and f">{view['score']}<" in paper
    assert "--page: #FBF8F2" in paper and "--page: #0D0D0F" in night
    assert paper.count('class="page"') == 2
    assert "What moved the" in paper and "Not enough data yet to give a score" not in paper


def test_html_uses_the_no_data_page_when_there_is_no_score(db_session, small_universe):
    startup = db_session.create_company(canonical_name="Kitebird", aliases=[], sector="EV Logistics")

    html = render_scorecard_html(build_scorecard(db_session.session, startup, now=NOW), "paper")

    assert "Not enough data yet to give a score" in html
    assert "What moved the" not in html
    assert "Not listed on a stock exchange" in html


def test_unknown_theme_is_rejected(db_session, swiggy):
    with pytest.raises(ValueError, match="Unknown theme"):
        render_scorecard_html(build_scorecard(db_session.session, swiggy, now=NOW), "neon")


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db_session] = lambda: db_session.session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_scorecard_endpoints(client, swiggy, monkeypatch):
    refreshed = []
    monkeypatch.setattr("app.api.routes.refresh_company", lambda db, company, settings: refreshed.append(company.id))

    page = client.get(f"/companies/{swiggy.id}/scorecard?theme=night")
    assert page.status_code == 200 and "Swiggy" in page.text and "--page: #0D0D0F" in page.text
    assert refreshed == [swiggy.id]  # stale data is checked before the report by default
    assert client.get(f"/companies/{swiggy.id}/scorecard?refresh=false").status_code == 200
    assert refreshed == [swiggy.id]
    assert client.get(f"/companies/{swiggy.id}/scorecard?theme=neon").status_code == 422
    assert client.get("/companies/999/scorecard").status_code == 404

    def fail(html):
        raise ScorecardRenderError("Chromium missing")

    monkeypatch.setattr("app.api.routes.render_scorecard_pdf", fail)
    assert client.get(f"/companies/{swiggy.id}/scorecard.pdf").status_code == 503


def test_real_pdf_render_produces_two_a4_pages(db_session, swiggy):
    pytest.importorskip("playwright")
    from pypdf import PdfReader
    import io

    html = render_scorecard_html(build_scorecard(db_session.session, swiggy, now=NOW), "paper")
    try:
        pdf = scorecard_render.render_scorecard_pdf(html, timeout_ms=60000)
    except ScorecardRenderError as exc:
        pytest.skip(f"Chromium not available: {exc}")

    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 2
    width, height = float(reader.pages[0].mediabox.width), float(reader.pages[0].mediabox.height)
    assert (round(width), round(height)) == (595, 842)  # A4 in points
    assert "Swiggy" in reader.pages[0].extract_text()


def test_plain_words_never_claim_no_news_when_news_was_not_checked():
    from types import SimpleNamespace

    from app.reports.scorecard import plain_words

    company = SimpleNamespace(canonical_name="Sun Pharmaceutical Industries Ltd")
    view = {"scored": True, "confidence": "Medium"}
    unchecked = {"inputs": {}, "pillars": [{"name": "News", "score": None, "coverage": 0.0}]}
    checked = {"inputs": {}, "pillars": [{"name": "News", "score": 0, "coverage": 1.0}]}

    text = plain_words(company, view, unchecked, [], {}, listed=False)
    assert "could not check recent news" in text
    assert "no significant news" not in text and "no reports of layoffs" not in text

    text = plain_words(company, view, checked, [], {}, listed=False)
    assert "no significant news" in text and "no reports of layoffs" in text
