"""Search the company directory and turn a directory row into a scorable company.

The directory (~13,000 rows) is small enough to search in memory. The index is rebuilt
when the table's row count changes, so a reload of the directory is picked up without a
restart.

Matching, best first:
    1. ticker or CIN typed exactly            ("HDFCBANK", "500325")
    2. the whole name, or a brand in brackets ("HDFC Bank", "Groww")
    3. every word typed starts a word of the name, in order of fit
    4. a close spelling                        ("hdfc bnak")
"""

import csv
import difflib
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.companies.normalization import normalize_company_name
from app.models.company import Company, CompanyAlias
from app.models.directory import DirectoryCompany

DIRECTORY_CSV = Path("data/company_directory.csv")
# US-listed companies with large teams in India; the taxonomy sheet covers India only.
US_COMPANIES_CSV = Path("data/us_companies.csv")
US_SECTOR = "US-listed companies"
STARTUP_TIERS = {"late_stage", "growth_stage", "early_stage"}
# Bigger, better-covered companies first when a search is ambiguous.
COVERAGE_RANK = {"full": 0, "registry": 1, "basic": 2}
SIZE_RANK = {"large": 0, "late_stage": 0, "mid": 1, "medium": 1, "growth_stage": 1, "small": 2,
             "early_stage": 3, "sme": 3, "micro": 4, "unknown": 5}
BRACKET_RE = re.compile(r"\(([^)]+)\)")


def name_key(name: str) -> str:
    """The directory's search key: the company name without legal suffixes or brackets."""
    return normalize_company_name(BRACKET_RE.sub(" ", name or ""))


def brand_keys(name: str) -> list[str]:
    """Names inside brackets that read as brands, e.g. "Billionbrains Garage Ventures Ltd (Groww)"."""
    keys = []
    for inner in BRACKET_RE.findall(name or ""):
        key = normalize_company_name(inner)
        if key and not re.fullmatch(r"(india|unlisted|listed|nse|bse)( .*)?", key) and len(key) >= 3:
            keys.append(key)
    return keys


# Loading ---------------------------------------------------------------------------------------


def alias_keys(aliases: str | None) -> list[str]:
    keys = [normalize_company_name(a) for a in (aliases or "").split("|")]
    return [k for k in keys if len(k) >= 2]


def us_directory_rows(path: Path) -> list[DirectoryCompany]:
    """Rows for data/us_companies.csv: name, ticker, exchange, industry, aliases."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        records = list(csv.DictReader(handle))
    return [
        DirectoryCompany(
            name=r["name"], name_key=name_key(r["name"]), sector=US_SECTOR, industry=r["industry"],
            listing="listed", board="mainboard", size_tier="large",
            peer_group=f"{US_SECTOR} / {r['industry']} / listed / large",
            entity_type="company", coverage="full", country="United States",
            foreign_ticker=r["ticker"].strip().upper(), foreign_exchange=r["exchange"].strip().upper(),
            aliases=r.get("aliases") or None,
        )
        for r in records
    ]


def load_directory(db: Session, path: Path = DIRECTORY_CSV, us_path: Path = US_COMPANIES_CSV) -> dict:
    """Replace the directory with the CSVs, keeping links to companies already onboarded."""
    links = {(row.name, row.sector): row.company_id
             for row in db.scalars(select(DirectoryCompany).where(DirectoryCompany.company_id.is_not(None)))}
    db.execute(delete(DirectoryCompany))
    with path.open(encoding="utf-8") as handle:
        records = list(csv.DictReader(handle))
    for record in records:
        db.add(DirectoryCompany(
            name=record["name"], name_key=name_key(record["name"]), sector=record["sector"],
            industry=record["industry"], listing=record["listing"], board=record["board"] or None,
            size_tier=record["size_tier"], peer_group=record["peer_group"],
            nse_symbol=record["nse_symbol"] or None, bse_code=record["bse_code"] or None,
            cin=record["cin"] or None, llpin=record["llpin"] or None, funding_stage=record["funding_stage"] or None,
            entity_type=record["entity_type"], coverage=record["coverage"], category_id=record["category_id"] or None,
            sub_sector=record["sub_sector"] or None, market_cap_text=record["market_cap_text"] or None,
            source_url=record["source_url"] or None, also_listed_under=record["also_listed_under"] or None,
            company_id=links.get((record["name"], record["sector"])),
        ))
    us_rows = us_directory_rows(us_path)
    for row in us_rows:
        row.company_id = links.get((row.name, row.sector))
        db.add(row)
    db.flush()
    linked = link_existing_companies(db)
    db.commit()
    DirectoryIndex.reset()
    return {"rows": len(records), "us_rows": len(us_rows), "kept_links": len(links), "linked_to_existing": linked}


def link_existing_companies(db: Session) -> int:
    """Point directory rows at companies that already exist (by CIN, ticker or name)."""
    companies = list(db.scalars(select(Company)))
    by_cin = {c.cin: c.id for c in companies if c.cin}
    by_ticker = {(c.ticker or "").upper(): c.id for c in companies if c.ticker}
    by_alias = {a.normalized_alias: a.company_id for a in db.scalars(select(CompanyAlias))}
    linked = 0
    for row in db.scalars(select(DirectoryCompany).where(DirectoryCompany.company_id.is_(None))):
        company_id = (by_cin.get(row.cin or "") or by_ticker.get((row.nse_symbol or "").upper())
                      or by_ticker.get(row.bse_code or "") or by_ticker.get((row.foreign_ticker or "").upper())
                      or by_alias.get(row.name_key)
                      or next((by_alias[k] for k in alias_keys(row.aliases) if k in by_alias), None))
        if company_id:
            row.company_id = company_id
            linked += 1
    return linked


# Searching -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    id: int
    name: str
    key: str
    brands: tuple[str, ...]
    tokens: tuple[str, ...]
    nse: str
    bse: str
    cin: str
    foreign: str
    coverage: str
    size_tier: str

    @property
    def rank(self) -> tuple:
        return (COVERAGE_RANK.get(self.coverage, 3), SIZE_RANK.get(self.size_tier, 5), len(self.name))


class DirectoryIndex:
    _lock = threading.Lock()
    _cached: "DirectoryIndex | None" = None
    _cached_count: int = -1  # table row count the cache was built from

    def __init__(self, entries: list[Entry]):
        self.entries = entries
        self.by_code: dict[str, list[Entry]] = {}
        self.by_key: dict[str, list[Entry]] = {}
        self.by_token: dict[str, set[int]] = {}
        for i, entry in enumerate(entries):
            for code in (entry.nse, entry.bse, entry.cin, entry.foreign):
                if code:
                    self.by_code.setdefault(code.upper(), []).append(entry)
            for key in (entry.key, *entry.brands):
                self.by_key.setdefault(key, []).append(entry)
            for token in entry.tokens:
                self.by_token.setdefault(token, set()).add(i)
        self.keys = list(self.by_key)

    @classmethod
    def get(cls, db: Session) -> "DirectoryIndex":
        count = db.scalar(select(func.count(DirectoryCompany.id))) or 0
        with cls._lock:
            if cls._cached is None or cls._cached_count != count:
                rows = db.execute(select(
                    DirectoryCompany.id, DirectoryCompany.name, DirectoryCompany.name_key, DirectoryCompany.nse_symbol,
                    DirectoryCompany.bse_code, DirectoryCompany.cin, DirectoryCompany.coverage, DirectoryCompany.size_tier,
                    DirectoryCompany.foreign_ticker, DirectoryCompany.aliases,
                ).where(DirectoryCompany.coverage != "not_scored")).all()
                entries = []
                for r in rows:
                    brands = tuple(dict.fromkeys([*brand_keys(r[1]), *alias_keys(r[9])]))
                    entries.append(Entry(
                        id=r[0], name=r[1], key=r[2], brands=brands,
                        tokens=tuple(dict.fromkeys(f"{r[2]} {' '.join(brands)}".split())),
                        nse=r[3] or "", bse=r[4] or "", cin=r[5] or "", foreign=r[8] or "",
                        coverage=r[6], size_tier=r[7],
                    ))
                cls._cached = DirectoryIndex(entries)
                cls._cached_count = count
            return cls._cached

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._cached = None
            cls._cached_count = -1

    def search(self, query: str, limit: int = 5) -> tuple[list[Entry], bool]:
        """Matching entries, best first, and whether the first one is an exact match."""
        raw = (query or "").strip()
        if raw.upper() in self.by_code:
            return sorted(self.by_code[raw.upper()], key=lambda e: e.rank)[:limit], True
        key = normalize_company_name(raw)
        if not key:
            return [], False
        if key in self.by_key:
            exact = sorted(self.by_key[key], key=lambda e: e.rank)
            return exact[:limit], True

        words = key.split()
        candidates: set[int] | None = None
        for word in words:
            hits = {i for token, ids in self.by_token.items() if token.startswith(word) for i in ids} \
                if len(word) >= 2 else set()
            candidates = hits if candidates is None else candidates & hits
            if not candidates:
                break
        if candidates:
            found = [self.entries[i] for i in candidates]
            # Names that start with what was typed come first.
            found.sort(key=lambda e: (not e.key.startswith(key), *e.rank))
            return found[:limit], False

        close = difflib.get_close_matches(key, self.keys, n=limit, cutoff=0.82)
        found = [e for k in close for e in self.by_key[k]]
        return sorted(found, key=lambda e: e.rank)[:limit], False


# Onboarding ------------------------------------------------------------------------------------


def funding_status_for(row: DirectoryCompany) -> str:
    stage = (row.funding_stage or "").lower()
    if stage in ("unfunded", "bootstrapped"):
        return "not_funded"
    if row.size_tier in STARTUP_TIERS or stage:
        return "funded"
    return "not_funded"  # corporate private companies: not venture funded


def onboard(db: Session, row: DirectoryCompany) -> Company:
    """The scorable company for a directory row, created the first time it is needed."""
    if row.company_id and (company := db.get(Company, row.company_id)):
        return company
    # A company may already exist under this CIN, ticker or name (added by hand, or from
    # another directory row). Checking just this row keeps a search fast on a small server.
    existing = None
    if row.cin:
        existing = db.scalar(select(Company).where(Company.cin == row.cin))
    for code in (row.nse_symbol, row.bse_code, row.foreign_ticker):
        if existing is None and code:
            existing = db.scalar(select(Company).where(Company.ticker == code))
    if existing is None and row.name_key:
        alias = db.scalar(select(CompanyAlias).where(CompanyAlias.normalized_alias == row.name_key))
        existing = db.get(Company, alias.company_id) if alias else None
    if existing is not None:
        row.company_id = existing.id
        db.commit()
        return existing

    if row.foreign_ticker:
        ticker, exchange = row.foreign_ticker, row.foreign_exchange
    elif row.nse_symbol:
        ticker, exchange = row.nse_symbol, "NSE"
    elif row.bse_code:
        ticker, exchange = row.bse_code, "BSE"
    else:
        ticker, exchange = None, None
    canonical = " ".join(BRACKET_RE.sub(" ", row.name).split()) or row.name
    if db.scalar(select(Company.id).where(Company.canonical_name == canonical)):
        canonical = f"{canonical} ({row.sector})"
    company = Company(
        canonical_name=canonical[:255], legal_name=row.name if row.cin or row.llpin else None,
        country=row.country or "India", sector=row.industry, industry=row.sub_sector,
        cin=row.cin if row.cin and not db.scalar(select(Company.id).where(Company.cin == row.cin)) else None,
        funding_status=funding_status_for(row), ticker=ticker, exchange=exchange,
    )
    db.add(company)
    db.flush()
    names = [row.name, canonical, *BRACKET_RE.findall(row.name), *(a.strip() for a in (row.aliases or "").split("|") if a.strip())]
    wanted = {normalize_company_name(a): a for a in dict.fromkeys(names)}
    taken = set(db.scalars(select(CompanyAlias.normalized_alias).where(CompanyAlias.normalized_alias.in_(list(wanted)))))
    for normalized, alias in wanted.items():
        if normalized and normalized not in taken and len(normalized) >= 3 and normalized not in ("india", "unlisted"):
            db.add(CompanyAlias(company_id=company.id, alias=alias.strip(), normalized_alias=normalized, source="directory"))
            taken.add(normalized)
    row.company_id = company.id
    db.commit()
    return company
