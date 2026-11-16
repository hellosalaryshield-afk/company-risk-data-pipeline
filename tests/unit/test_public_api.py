from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email import message_from_bytes

import pytest
from fastapi.testclient import TestClient

from app.api import public
from app.config.settings import Settings
from app.database.session import get_db_session
from app.main import app
from app.models.lead import ReportRequest
from app.models.scoring import RiskScore
from app.reports import emailer


def save_score(db_session, company, score, band="Low", days_ago=0):
    db_session.session.add(
        RiskScore(
            company_id=company.id, weights_version="1.0", score=Decimal(str(score)) if score is not None else None,
            band=band if score is not None else None, confidence="Limited data", coverage=Decimal("0.4"),
            result={}, computed_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    db_session.session.flush()


@pytest.fixture
def companies(db_session):
    swiggy = db_session.create_company(canonical_name="Swiggy", aliases=["Bundl Technologies"], sector="Quick Commerce")
    infosys = db_session.create_company(canonical_name="Infosys", sector="IT Services")
    kitebird = db_session.create_company(canonical_name="Kitebird", sector="EV Logistics")
    save_score(db_session, swiggy, 19, days_ago=3)
    save_score(db_session, swiggy, 25)  # the newest score is the one shown
    save_score(db_session, infosys, 18)
    save_score(db_session, kitebird, None)  # not enough data: never featured
    return {"swiggy": swiggy, "infosys": infosys, "kitebird": kitebird}


@pytest.fixture
def sent():
    """Report requests handed to the background sender."""
    return []


@pytest.fixture
def client(db_session, sent):
    app.dependency_overrides[get_db_session] = lambda: db_session.session
    app.dependency_overrides[public.report_sender] = lambda: sent.append
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_featured_lists_scored_companies_highest_first_with_their_newest_score(client, companies):
    cards = client.get("/public/companies/featured").json()

    assert [(c["name"], c["score"]) for c in cards] == [("Swiggy", 25), ("Infosys", 18)]
    assert cards[0] == {"id": companies["swiggy"].id, "name": "Swiggy", "sector": "Quick Commerce",
                        "score": 25, "band": "Low", "confidence": "Limited data"}


def test_search_matches_by_name_or_alias(client, companies):
    assert client.get("/public/companies/search", params={"q": "swiggy"}).json()["match"]["name"] == "Swiggy"
    by_alias = client.get("/public/companies/search", params={"q": "Bundl Technologies"}).json()
    assert by_alias["status"] == "matched" and by_alias["match"]["score"] == 25


def test_a_typo_gets_close_matches_and_nonsense_gets_nothing(client, companies):
    typo = client.get("/public/companies/search", params={"q": "Infosis"}).json()
    assert typo["status"] == "suggestions" and [s["name"] for s in typo["suggestions"]] == ["Infosys"]

    nothing = client.get("/public/companies/search", params={"q": "Qwertyuiop"}).json()
    assert nothing == {"status": "not_found", "query": "Qwertyuiop", "match": None, "suggestions": []}


def test_summary_refreshes_by_default_and_returns_only_what_the_panel_shows(client, companies, monkeypatch):
    refreshed = []
    monkeypatch.setattr(public, "refresh_company", lambda db, company, settings: refreshed.append(company.id))

    body = client.get(f"/public/companies/{companies['kitebird'].id}/summary").json()

    assert refreshed == [companies["kitebird"].id]
    assert body["name"] == "Kitebird" and body["scored"] is False and body["score"] is None
    assert "We do not have enough data yet" in body["plain_words"]
    assert "inputs" not in body and "drivers" not in body
    assert client.get("/public/companies/9999/summary?refresh=false").status_code == 404


def test_summary_saves_a_changed_score_so_the_list_matches_the_panel(client, companies, db_session):
    swiggy = companies["swiggy"]  # stored 25, but there is no data behind it, so the live result differs

    body = client.get(f"/public/companies/{swiggy.id}/summary?refresh=false").json()

    scores = [r.score for r in db_session.session.query(RiskScore).filter_by(company_id=swiggy.id)]
    assert body["score"] is None
    assert len(scores) == 3 and scores.count(None) == 1  # the live result was saved next to the two old ones


def test_report_request_saves_the_lead_and_queues_the_email(client, companies, sent, db_session):
    response = client.post(
        "/public/report-requests",
        json={"company_id": companies["swiggy"].id, "name": "Asha Rao", "email": " Asha@Example.com ", "consent": True},
    )

    assert response.status_code == 202 and response.json()["email"] == "asha@example.com"
    lead = db_session.session.query(ReportRequest).one()
    assert (lead.name, lead.email, lead.company_name, lead.consent, lead.status) == (
        "Asha Rao", "asha@example.com", "Swiggy", True, "pending",
    )
    assert sent == [lead.id]


@pytest.mark.parametrize(
    ("change", "status"),
    [({"email": "not-an-email"}, 422), ({"consent": False}, 422), ({"company_id": 9999}, 404)],
)
def test_report_request_validation(client, companies, sent, change, status):
    payload = {"company_id": companies["swiggy"].id, "name": "Asha", "email": "asha@example.com", "consent": True, **change}

    assert client.post("/public/report-requests", json=payload).status_code == status
    assert sent == []


def test_bots_filling_the_hidden_field_are_dropped_quietly(client, companies, sent, db_session):
    payload = {"company_id": companies["swiggy"].id, "name": "Bot", "email": "bot@example.com", "consent": True, "website": "spam"}

    assert client.post("/public/report-requests", json=payload).status_code == 202
    assert db_session.session.query(ReportRequest).count() == 0 and sent == []


def test_one_email_cannot_request_endless_reports(client, companies):
    payload = {"company_id": companies["swiggy"].id, "name": "Asha", "email": "asha@example.com", "consent": True}

    codes = [client.post("/public/report-requests", json=payload).status_code for _ in range(6)]

    assert codes == [202] * 5 + [429]


def test_cors_lets_the_website_call_the_api(client):
    response = client.options(
        "/public/companies/featured",
        headers={"Origin": "https://salary-shield.netlify.app", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers["access-control-allow-origin"] == "https://salary-shield.netlify.app"


def test_send_report_emails_the_pdf_or_saves_it_to_the_outbox(db_session, companies, monkeypatch, tmp_path):
    monkeypatch.setattr(public, "render_scorecard_pdf", lambda html: b"%PDF-1.7 fake")
    monkeypatch.setattr(public, "get_settings", lambda: Settings(report_from_email="hello@salaryshield.in"))
    monkeypatch.setattr(emailer, "OUTBOX", tmp_path)
    request = ReportRequest(name="Asha Rao", email="asha@example.com", company_id=companies["swiggy"].id,
                            company_name="Swiggy", consent=True, status="pending")
    db_session.session.add(request)
    db_session.session.flush()

    public.send_report(db_session.session, request.id)

    assert request.status == "saved" and request.sent_at is not None and request.attempts == 1
    message = message_from_bytes(next(tmp_path.glob("*.eml")).read_bytes())
    assert message["To"] == "Asha Rao <asha@example.com>"
    assert message["Subject"] == "Swiggy: your Salary Shield risk scorecard"
    attachment = next(part for part in message.walk() if part.get_filename())
    assert attachment.get_filename() == "swiggy_scorecard.pdf" and attachment.get_payload(decode=True) == b"%PDF-1.7 fake"


def test_send_report_marks_failures(db_session, companies, monkeypatch):
    def broken(html):
        raise public.ScorecardRenderError("Chromium missing")

    monkeypatch.setattr(public, "render_scorecard_pdf", broken)
    request = ReportRequest(name="Asha", email="asha@example.com", company_id=companies["swiggy"].id,
                            company_name="Swiggy", consent=True, status="pending")
    db_session.session.add(request)
    db_session.session.flush()

    public.send_report(db_session.session, request.id)

    assert request.status == "failed" and request.error == "Chromium missing"
