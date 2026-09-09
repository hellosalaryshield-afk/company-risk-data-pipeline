import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

# GDELT DOC 2.0. No API key, no account, and usable in production - unlike the NewsAPI
# developer tier, which its terms restrict to development and testing.
GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

# GDELT asks for one request every five seconds and answers with a plain-text notice
# instead of JSON when that is exceeded. Measured on 2026-09-09: even six-second spacing
# drew 429s after a burst, so the floor here is deliberately higher than the documented
# minimum and every call still has to tolerate a refusal.
MIN_REQUEST_INTERVAL_SECONDS = 8.0

RATE_LIMIT_MARKER = "please limit requests"


class GdeltError(RuntimeError):
    pass


class GdeltRateLimitError(GdeltError):
    """GDELT refused the call because requests came too quickly."""


@dataclass(frozen=True)
class GdeltTimeline:
    series: str
    dates: list[datetime]
    values: list[float]


@dataclass(frozen=True)
class GdeltFetchResult:
    company_name: str
    timespan: str
    tone: GdeltTimeline | None
    volume: GdeltTimeline | None
    articles: list[dict[str, Any]]
    raw_data: dict[str, Any] = field(default_factory=dict)


class _Throttle:
    """Process-wide spacing between GDELT calls.

    GDELT rate-limits per client, so the limit is shared across every company being
    collected rather than per adapter instance.
    """

    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = min_interval_seconds
        self._lock = threading.Lock()
        self._last_call_at = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call_at
            remaining = self.min_interval_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
            self._last_call_at = time.monotonic()


_THROTTLE = _Throttle(MIN_REQUEST_INTERVAL_SECONDS)


class GdeltClient:
    def __init__(
        self,
        timeout_seconds: float = 30.0,
        throttle: _Throttle | None = None,
        user_agent: str = "CompanyRiskDataPipeline/1.0",
    ):
        self.timeout_seconds = timeout_seconds
        self.throttle = throttle if throttle is not None else _THROTTLE
        self.user_agent = user_agent

    def fetch_mode(self, company_name: str, mode: str, timespan: str = "3m", **extra: Any) -> dict[str, Any]:
        """Call one GDELT mode and return parsed JSON.

        GDELT signals rate limiting with an HTTP 429 or, confusingly, with a plain-text
        body under a 200. Both are surfaced as GdeltRateLimitError.
        """
        params = {
            "query": f'"{company_name}"',
            "mode": mode,
            "format": "json",
            "timespan": timespan,
            **extra,
        }

        self.throttle.wait()
        response = httpx.get(
            GDELT_DOC_ENDPOINT,
            params=params,
            timeout=self.timeout_seconds,
            headers={"User-Agent": self.user_agent},
        )

        if response.status_code == 429:
            raise GdeltRateLimitError("GDELT rate limit reached (HTTP 429).")

        response.raise_for_status()
        body = response.text.strip()

        if RATE_LIMIT_MARKER in body[:200].lower():
            raise GdeltRateLimitError("GDELT rate limit reached (plain-text notice).")

        if not body.startswith(("{", "[")):
            raise GdeltError(f"GDELT returned an unexpected non-JSON body for mode {mode}.")

        return response.json()

    def fetch_company_coverage(self, company_name: str, timespan: str = "3m", max_articles: int = 25) -> GdeltFetchResult:
        """Collect tone, volume and a sample of articles for one company.

        Each mode is a separate throttled request. A rate limit on a later mode still
        returns whatever earlier modes produced, so a partial result is preserved rather
        than thrown away.
        """
        raw: dict[str, Any] = {}
        tone = volume = None
        articles: list[dict[str, Any]] = []

        try:
            payload = self.fetch_mode(company_name, "timelinetone", timespan=timespan)
            raw["timelinetone"] = payload
            tone = parse_timeline(payload)
        except GdeltRateLimitError:
            raise
        except GdeltError:
            pass

        try:
            payload = self.fetch_mode(company_name, "timelinevol", timespan=timespan)
            raw["timelinevol"] = payload
            volume = parse_timeline(payload)
        except GdeltError:
            pass

        try:
            payload = self.fetch_mode(
                company_name, "artlist", timespan=timespan, maxrecords=str(max_articles)
            )
            raw["artlist"] = payload
            articles = payload.get("articles") or []
        except GdeltError:
            pass

        return GdeltFetchResult(
            company_name=company_name,
            timespan=timespan,
            tone=tone,
            volume=volume,
            articles=articles,
            raw_data=raw,
        )


def parse_timeline(payload: dict[str, Any]) -> GdeltTimeline | None:
    series_list = payload.get("timeline") or []
    if not series_list:
        return None

    series = series_list[0]
    dates: list[datetime] = []
    values: list[float] = []

    for point in series.get("data") or []:
        parsed = parse_gdelt_date(point.get("date"))
        value = point.get("value")
        if parsed is None or value is None:
            continue
        dates.append(parsed)
        values.append(float(value))

    if not values:
        return None

    return GdeltTimeline(series=series.get("series") or "unknown", dates=dates, values=values)


def parse_gdelt_date(value: str | None) -> datetime | None:
    """GDELT stamps points as 20260612T000000Z."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
