"""Monthly budget for paid sources.

Glassdoor (via Apify) is billed about $0.10 per call, so Apify's free $5 a month covers
about 50 calls. Every real call counts, successful or not, since Apify bills the run either
way. Re-checking an answer already stored makes no call and is not counted.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.models.collection import CollectionRun
from app.pipeline.source_selection import WORKPLACE_SOURCE


def glassdoor_calls_this_month(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    runs = db.scalars(
        select(CollectionRun.extra_metadata).where(
            CollectionRun.extra_metadata["source"].as_string() == WORKPLACE_SOURCE,
            CollectionRun.started_at >= month_start,
        )
    ).all()
    return sum(1 for metadata in runs if not (metadata or {}).get("reused_from_run_id"))


def glassdoor_allowed_on_search(db: Session, settings: Settings, now: datetime | None = None) -> bool:
    """Whether a website search may fetch Glassdoor now: switched on, a key, budget left."""
    if not (settings.glassdoor_on_search and settings.apify_token):
        return False
    return glassdoor_calls_this_month(db, now) < settings.glassdoor_monthly_limit
