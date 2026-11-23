"""Company financial results from Yahoo Finance's keyless fundamentals endpoint.

The same provider as the share prices (`market_data.py`), and it covers both Indian (NSE)
and US listings. Financial Modeling Prep was tried first; its free plan only serves
statements for a short list of large US companies (Amazon yes; Qualcomm, Oracle and
Cognizant no), and SEC EDGAR covers US filers only.

Four of the scorecard's financial features come from here. Each prefers the freshest basis
Yahoo has and falls back to the last annual report, because Indian companies often report
cash and balance-sheet items only every six months:

    revenue_growth_yoy   latest quarter vs the same quarter a year earlier, else last year vs the year before
    operating_margin     operating income / revenue over the last twelve months, else the last year
    cash_runway_months   cash / monthly operating cash burn; a company not burning cash gets the cap
    debt_to_equity       total debt / shareholders' equity at the latest balance sheet

Ratios and percentages do not depend on currency, so INR and USD companies compare directly.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.sources.market_data import REQUEST_HEADERS

FUNDAMENTALS_ENDPOINT = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"

ITEMS = (
    "TotalRevenue",
    "OperatingIncome",
    "OperatingCashFlow",
    "CashCashEquivalentsAndShortTermInvestments",
    "CashAndCashEquivalents",
    "TotalDebt",
    "StockholdersEquity",
)
PERIODS = ("annual", "quarterly", "trailing")

# A company that generates cash has no runway limit. Capping it keeps the value finite so it
# can be ranked against peers: every cash-generating company ties as the safest.
RUNWAY_CAP_MONTHS = 60.0
# How far apart two quarters may be and still count as "a year apart".
YEAR_DAYS, YEAR_TOLERANCE_DAYS = 365, 25

KPI_UNITS = {
    "fin_revenue_growth_yoy_pct": "percent",
    "fin_operating_margin_pct": "percent",
    "fin_cash_runway_months": "months",
    "fin_debt_to_equity": "ratio",
}


@dataclass(frozen=True)
class Point:
    as_of: date
    value: float
    currency: str | None = None


@dataclass
class Financials:
    symbol: str
    series: dict[str, list[Point]]  # "quarterlyTotalRevenue" -> points, oldest first
    raw_data: dict[str, Any] = field(default_factory=dict)

    def get(self, period: str, item: str) -> list[Point]:
        return self.series.get(period + item, [])

    @property
    def currency(self) -> str | None:
        for points in self.series.values():
            for point in points:
                if point.currency:
                    return point.currency
        return None

    @property
    def latest_period_end(self) -> date | None:
        dates = [p.as_of for points in self.series.values() for p in points]
        return max(dates) if dates else None

    @property
    def is_empty(self) -> bool:
        return not any(self.series.values())


class YahooFundamentalsClient:
    def __init__(self, timeout_seconds: float = 20.0):
        self.timeout_seconds = timeout_seconds

    def fetch(self, symbol: str, now: datetime | None = None) -> Financials:
        now = now or datetime.now(UTC)
        types = ",".join(period + item for period in PERIODS for item in ITEMS)
        response = httpx.get(
            FUNDAMENTALS_ENDPOINT.format(symbol=symbol),
            params={"type": types, "period1": int(datetime(now.year - 4, 1, 1, tzinfo=UTC).timestamp()),
                    "period2": int(now.timestamp())},
            headers=REQUEST_HEADERS,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return parse_fundamentals(symbol, response.json())


def parse_fundamentals(symbol: str, payload: dict[str, Any]) -> Financials:
    series: dict[str, list[Point]] = {}
    for result in (payload.get("timeseries") or {}).get("result") or []:
        types = (result.get("meta") or {}).get("type") or []
        if not types:
            continue
        name = types[0]
        points = []
        for entry in result.get(name) or []:
            if not entry:
                continue
            raw = (entry.get("reportedValue") or {}).get("raw")
            as_of = entry.get("asOfDate")
            if raw is None or not as_of:
                continue
            points.append(Point(date.fromisoformat(as_of), float(raw), entry.get("currencyCode")))
        if points:
            series[name] = sorted(points, key=lambda p: p.as_of)
    return Financials(symbol=symbol, series=series, raw_data=payload)


def latest(points: list[Point]) -> Point | None:
    return points[-1] if points else None


def revenue_growth(fin: Financials) -> tuple[float | None, dict]:
    quarters = fin.get("quarterly", "TotalRevenue")
    if quarters:
        now = quarters[-1]
        year_ago = [p for p in quarters[:-1] if abs((now.as_of - p.as_of).days - YEAR_DAYS) <= YEAR_TOLERANCE_DAYS]
        if year_ago and year_ago[-1].value > 0:
            before = year_ago[-1]
            return pct_change(now.value, before.value), {"basis": "quarter vs same quarter last year",
                                                          "period_end": now.as_of, "compared_with": before.as_of}
    years = fin.get("annual", "TotalRevenue")
    if len(years) >= 2 and years[-2].value > 0:
        return pct_change(years[-1].value, years[-2].value), {"basis": "last year vs year before",
                                                              "period_end": years[-1].as_of, "compared_with": years[-2].as_of}
    return None, {}


def operating_margin(fin: Financials) -> tuple[float | None, dict]:
    for period, basis in (("trailing", "last twelve months"), ("annual", "last year")):
        revenue = {p.as_of: p.value for p in fin.get(period, "TotalRevenue")}
        income = {p.as_of: p.value for p in fin.get(period, "OperatingIncome")}
        common = sorted(set(revenue) & set(income))
        if common and revenue[common[-1]] > 0:
            when = common[-1]
            return round(100 * income[when] / revenue[when], 2), {"basis": basis, "period_end": when}
    return None, {}


def cash_runway(fin: Financials) -> tuple[float | None, dict]:
    cash = None
    for period in ("quarterly", "annual"):
        cash = latest(fin.get(period, "CashCashEquivalentsAndShortTermInvestments")) or latest(
            fin.get(period, "CashAndCashEquivalents")
        )
        if cash:
            break
    flow, basis = latest(fin.get("trailing", "OperatingCashFlow")), "last twelve months"
    if flow is None:
        flow, basis = latest(fin.get("annual", "OperatingCashFlow")), "last year"
    if flow is None:
        return None, {}
    details = {"basis": f"operating cash flow, {basis}", "period_end": flow.as_of, "operating_cash_flow": flow.value}
    if flow.value >= 0:
        return RUNWAY_CAP_MONTHS, {**details, "burning_cash": False}
    if cash is None:
        return None, {}
    monthly_burn = -flow.value / 12
    months = min(RUNWAY_CAP_MONTHS, cash.value / monthly_burn)
    return round(months, 1), {**details, "burning_cash": True, "cash": cash.value, "cash_as_of": cash.as_of}


def debt_to_equity(fin: Financials) -> tuple[float | None, dict]:
    for period in ("quarterly", "annual"):
        debt = {p.as_of: p.value for p in fin.get(period, "TotalDebt")}
        equity = {p.as_of: p.value for p in fin.get(period, "StockholdersEquity")}
        common = sorted(set(debt) & set(equity))
        if not common:
            continue
        when = common[-1]
        if equity[when] <= 0:
            # Negative equity makes the ratio meaningless rather than "very safe".
            return None, {"period_end": when, "note": "shareholders' equity is zero or negative"}
        return round(debt[when] / equity[when], 3), {"period_end": when}
    return None, {}


def pct_change(new: float, old: float) -> float:
    return round(100 * (new - old) / old, 2)


def financial_kpis(fin: Financials) -> tuple[dict[str, float], dict[str, dict]]:
    """KPI values and, for each, how it was worked out (basis and report date)."""
    values: dict[str, float] = {}
    details: dict[str, dict] = {}
    for kpi, compute in (
        ("fin_revenue_growth_yoy_pct", revenue_growth),
        ("fin_operating_margin_pct", operating_margin),
        ("fin_cash_runway_months", cash_runway),
        ("fin_debt_to_equity", debt_to_equity),
    ):
        value, detail = compute(fin)
        if value is not None:
            values[kpi] = value
        if detail:
            details[kpi] = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in detail.items()}
    return values, details
