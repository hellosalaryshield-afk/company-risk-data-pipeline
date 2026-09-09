import csv
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

# NSE publishes bulk and block deals as plain CSV. No API key, no account.
#
# Two things shape the design. First, each file is a single-day snapshot: the copy fetched on
# 2026-09-09 held only that day's deals, 193 rows across 47 symbols. History therefore only
# exists if the file is collected every trading day. Second, NSE rejects unfamiliar clients,
# so a browser user agent is required or the request is refused.
BULK_DEALS_URL = "https://nsearchives.nseindia.com/content/equities/bulk.csv"
BLOCK_DEALS_URL = "https://nsearchives.nseindia.com/content/equities/block.csv"

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

NO_RECORDS_MARKER = "NO RECORDS"


class NseDealsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Deal:
    deal_date: date
    symbol: str
    security_name: str
    client_name: str
    side: str
    quantity: float
    price: float | None
    deal_type: str
    raw_data: dict[str, Any]


class NseDealsClient:
    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds

    def fetch_csv(self, url: str) -> str:
        response = httpx.get(
            url,
            timeout=self.timeout_seconds,
            headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "text/csv,*/*"},
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text

    def fetch_bulk_deals(self) -> list[Deal]:
        return parse_deals(self.fetch_csv(BULK_DEALS_URL), deal_type="bulk")

    def fetch_block_deals(self) -> list[Deal]:
        return parse_deals(self.fetch_csv(BLOCK_DEALS_URL), deal_type="block")

    def fetch_all_deals(self) -> list[Deal]:
        """Both files. A failure on one does not discard the other."""
        deals: list[Deal] = []
        for fetch in (self.fetch_bulk_deals, self.fetch_block_deals):
            try:
                deals.extend(fetch())
            except httpx.HTTPError:
                continue
        return deals


def parse_deals(payload: str, deal_type: str) -> list[Deal]:
    """Parse one NSE deals CSV.

    An empty day is published as a single row reading "NO RECORDS" rather than as an empty
    file, so that is treated as no deals rather than as a parse failure.
    """
    reader = csv.DictReader(io.StringIO(payload.lstrip("﻿")))
    deals: list[Deal] = []

    for row in reader:
        symbol = (row.get("Symbol") or "").strip()
        deal_date = parse_deal_date(row.get("Date"))
        if not symbol or deal_date is None:
            continue
        if NO_RECORDS_MARKER in (row.get("Date") or "").upper():
            continue

        quantity = parse_number(row.get("Quantity Traded"))
        if quantity is None:
            continue

        deals.append(
            Deal(
                deal_date=deal_date,
                symbol=symbol.upper(),
                security_name=(row.get("Security Name") or "").strip(),
                client_name=(row.get("Client Name") or "").strip(),
                side=(row.get("Buy/Sell") or "").strip().upper(),
                quantity=quantity,
                price=parse_number(row.get("Trade Price / Wght. Avg. Price")),
                deal_type=deal_type,
                raw_data=dict(row),
            )
        )

    return deals


def parse_deal_date(value: str | None) -> date | None:
    """NSE writes dates as 09-SEP-2026."""
    if not value:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def group_by_symbol(deals: list[Deal]) -> dict[str, list[Deal]]:
    grouped: dict[str, list[Deal]] = {}
    for deal in deals:
        grouped.setdefault(deal.symbol, []).append(deal)
    return grouped
