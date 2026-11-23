"""Clean the company taxonomy spreadsheet into one consistent directory.

The spreadsheet (one tab per sector, ~14,500 rows) was compiled by hand from several
sources, so the same idea is spelled many ways: 30 listing statuses, 156 size buckets,
hundreds of one-off sub-sector names, six ticker formats, and companies repeated across
tabs. Its Category IDs are reused for different groups, so peer groups are rebuilt here
from the cleaned fields instead: sector / industry / listing / size.

Everything here is a pure function of the input rows, so the cleaning can be re-run
whenever the sheet changes, and every decision is recorded for the change report.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

# Identifier formats ------------------------------------------------------------------------

CIN_RE = re.compile(r"\b([LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6})\b")
LLPIN_RE = re.compile(r"\b([A-Z]{3}-\d{4})\b")
NSE_RE = re.compile(r"NSE(?:[- ]?SME|[- ]?EMERGE)?\s*[:/]\s*([A-Z0-9&\-]{1,20})", re.I)
BSE_RE = re.compile(r"BSE(?:[- ]?SME)?\s*[:/]?\s*(\d{6})", re.I)
BARE_CODE_RE = re.compile(r"^(\d{6})(?:\.0)?$")
BARE_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9&\-]{1,19}$")
STAGE_RE = re.compile(r"\b(pre-seed|seed|series [a-h]\d?|unfunded|bootstrapped)\b", re.I)


@dataclass
class Identifiers:
    nse_symbol: str | None = None
    bse_code: str | None = None
    cin: str | None = None
    llpin: str | None = None
    funding_stage: str | None = None


def parse_identifiers(text: str, listed: bool) -> Identifiers:
    """Split the mixed "Ticker / CIN" column into the identifiers it contains."""
    raw = (text or "").strip()
    ids = Identifiers()
    if not raw:
        return ids
    upper = raw.upper()
    if m := CIN_RE.search(upper):
        ids.cin = m.group(1)
    if m := BSE_RE.search(raw):
        ids.bse_code = m.group(1)
    elif m := BARE_CODE_RE.match(raw):
        ids.bse_code = m.group(1)
    if m := NSE_RE.search(raw):
        symbol = m.group(1).upper()
        if not symbol.isdigit():  # "NSE / 544937" carries a BSE-style code, not a symbol
            ids.nse_symbol = symbol
        elif not ids.bse_code:
            ids.bse_code = symbol
    if not ids.cin and (m := LLPIN_RE.search(upper)):
        ids.llpin = m.group(1)
    if listed and not (ids.nse_symbol or ids.bse_code or ids.cin or ids.llpin) and BARE_SYMBOL_RE.match(raw):
        ids.nse_symbol = raw  # bare symbols on listed rows are NSE symbols (HDFCBANK, TCS)
    if m := STAGE_RE.search(raw):
        ids.funding_stage = m.group(1).title()
    return ids


# Listing and size ---------------------------------------------------------------------------

LISTED, UNLISTED, GOVERNMENT, UNKNOWN = "listed", "unlisted", "government", "unknown"


def normalize_listing(status: str, ids: Identifiers) -> tuple[str, str | None]:
    """Return (listing, board). Board is "mainboard" or "sme" for listed companies."""
    s = (status or "").lower()
    if re.search(r"unlisted|private|\bllp\b|\bopc\b|\bnpl\b", s):
        listing = UNLISTED
    elif re.search(r"psu|govt|government", s):
        listing = LISTED if (ids.nse_symbol or ids.bse_code) else GOVERNMENT
    elif re.search(r"listed|mainboard|main-board|\bsme\b|\bnse\b|\bbse\b|emerge", s):
        listing = LISTED
    else:
        listing = LISTED if (ids.nse_symbol or ids.bse_code) else UNKNOWN
    board = None
    if listing == LISTED:
        board = "sme" if re.search(r"\bsme\b|emerge", s) else "mainboard"
    return listing, board


# Size tiers. Listed companies use market-cap tiers; unlisted ones use revenue size or,
# for startups, funding stage. "unknown" is kept rather than guessed.
LISTED_TIERS = ("large", "mid", "small", "micro", "sme")
UNLISTED_TIERS = ("late_stage", "growth_stage", "early_stage", "large", "medium", "small", "micro")


def normalize_size(bucket: str, listing: str, board: str | None, market_cap_cr: float | None) -> str:
    b = (bucket or "").lower()
    if listing == LISTED:
        if board == "sme" or re.search(r"\bsme\b|emerge", b):
            return "sme"
        for tier, pattern in (("large", r"large"), ("mid", r"\bmid"), ("small", r"small"), ("micro", r"micro")):
            if re.search(pattern, b):
                return tier
        if market_cap_cr is not None:  # AMFI-style cut-offs, used only when the bucket is missing
            return "large" if market_cap_cr >= 100_000 else "mid" if market_cap_cr >= 30_000 else "small" if market_cap_cr >= 1_000 else "micro"
        return "unknown"
    rules = (
        ("late_stage", r"unicorn|late-stage|late stage|series d"),
        ("growth_stage", r"series b|series c|mid-size \[series|mid-size \(series"),
        ("early_stage", r"seed|early|nano|bootstrap|startup|series a"),
        ("large", r"large|>\s*rs\s*500|>\s*₹\s*500|>\s*₹\s*5,000"),
        ("medium", r"medium|100\s*[-–]\s*500"),
        ("small", r"small|10\s*[-–]\s*100|sme|emerging|growth"),
        ("medium", r"\bmid"),
        ("micro", r"micro|<\s*rs\s*10|<\s*₹\s*1"),
    )
    for tier, pattern in rules:
        if re.search(pattern, b):
            return tier
    return "unknown"


MARKET_CAP_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(?:cr|crore)", re.I)


def market_cap_crore(text: str) -> float | None:
    """₹ crore figure from "₹13,06,611 Cr (Mkt Cap)"-style text, only when it is a market cap."""
    if not text or not re.search(r"mkt|market|mcap", text, re.I):
        return None
    m = MARKET_CAP_RE.search(text)
    return float(m.group(1).replace(",", "")) if m else None


# Industries ---------------------------------------------------------------------------------
# Most sector tabs use 3-6 clean sub-sectors, kept as they are. Financial Services (135
# names) and Information Technology (439 names, mostly used once) are folded into a short
# list so peer groups are big enough to compare. Rules are checked in order.

FINANCIAL_RULES = (
    # "NBFCs & Housing Finance" is a mixed bucket (Jio Financial, Bajaj Finance), so only the
    # explicit HFC label counts here; housing lenders inside it are caught by name below.
    ("Housing Finance", r"\bhfc\b"),
    ("Microfinance", r"\bmfi\b|microfinance"),
    ("Insurance", r"insurance"),
    ("Holding & Investment Companies", r"\bcic\b"),
    ("Asset & Wealth Management", r"asset management|portfolio management|wealth"),
    ("Fintech & Payments", r"fintech|payment|saas|regtech|tech\b|crypto|cpaas|embedded|neo-bank|credit-tech|digital|financial tech|fraud|analytics|bpm|ites|infrastructure"),
    ("Banks", r"bank"),
    ("NBFC & Lending", r"nbfc|lending|credit|loan"),
    ("Broking & Capital Markets", r"capital markets|broker|broking|exchange|depository|merchant bank"),
)
# A name tells more than the sheet's sub-sector for these (e.g. AMCs filed under Capital Markets).
FINANCIAL_NAME_RULES = (
    ("Asset & Wealth Management", r"asset management|\bamc\b|mutual fund|investment managers?\b"),
    ("Insurance", r"insurance"),
    ("Housing Finance", r"housing finance|home finance|housing development finance|homefin|home loans"),
    ("Microfinance", r"micro ?fin|microcredit"),
)

IT_RULES = (
    ("Cybersecurity", r"cyber|security|infosec|threat"),
    ("Robotics, Drones & Aerospace", r"robot|drone|uav|rpas|aerospace|defen[cs]e|space|avionics|evtol|air mobility|satellite"),
    ("Semiconductors & Electronics Manufacturing", r"semiconductor|fabless|osat|atmp|\bpcb|\bems\b|esdm|electronics manufactur|passive components|photonic|mems|wafer|vlsi|\bic\b"),
    ("BPO, KPO & GCCs", r"\bbpo\b|\bkpo\b|\bbpm\b|\bites\b|\bgcc"),
    ("Data, AI & Analytics", r"\bai\b|data analytics|data engineering|analytics|computer vision|mlops|generative|machine learning|ai/ml"),
    ("Cloud, Data Centers & Networking", r"cloud|data cent|hosting|\bisp|broadband|cdn|colocation|carrier|edge computing|telecom|fiber|network"),
    ("Hardware, IoT & Embedded", r"hardware|\biot\b|embedded|meter|sensor|wearable|power electronics|transformer|\bups\b|battery|\bev\b|consumer electronics|smart home|instrument|test|measurement|rf\b|microwave"),
    ("Digital Marketing & Media", r"marketing|adtech|advertis|media"),
    ("EdTech & IT Training", r"edtech|training|e-learning|skill"),
    # Services before products: "Custom Software Development" and "ERP & CRM Implementation" are services.
    ("IT Services & Consulting", r"it services|consult|digital transformation|custom software|web & mobile|app development|qa |testing services|managed it|it & software|tech services|it enabled|implementation"),
    ("Software & SaaS", r"saas|software|erp|crm|devtools|product"),
)


def normalize_industry(sector: str, sub_sector: str, name: str) -> str:
    sub = (sub_sector or "").strip()
    text = sub.lower()
    if sector == "Financial Services":
        for industry, pattern in FINANCIAL_NAME_RULES:
            if re.search(pattern, (name or "").lower()):
                return industry
        rules = FINANCIAL_RULES
    elif sector == "Information Technology":
        rules = IT_RULES
    else:
        return sub or "Other"
    for industry, pattern in rules:
        if re.search(pattern, text):
            return industry
    return "Other " + sector


# Entity types and coverage --------------------------------------------------------------------

def entity_type(name: str, listing_status: str, ticker_text: str) -> str:
    """Company, or one of the bodies that have little or no company-level public data."""
    n = (name or "").lower()
    status = (listing_status or "").lower()
    ticker = (ticker_text or "").lower()
    if re.search(r"\bassociation\b|\bfederation\b|\bcouncil\b|\bsociety of\b|chamber of", n):
        return "association"
    is_bank = re.search(r"\bbank\b|sahakari", n) is not None
    if not is_bank and (re.search(r"\bgovt\b|government|ministry|software technology parks", n) or "govt society" in ticker):
        return "government_body"
    # Foreign banks operate in India as branches: no Indian CIN, no Indian listing.
    if ("rbi scheduled)" in ticker or "(frb)" in ticker) or re.search(r"\bbank\b.*\(india\)$|india branch$", n):
        return "foreign_bank_branch"
    if re.search(r"gramin|grameen|grameena|rural bank", n) or "rrb)" in ticker:
        return "rural_bank"
    if re.search(r"co-?operative|co-?op\b|sahakari", n) and re.search(r"bank|sahakari", n):
        return "cooperative_bank"
    if re.search(r"co-?operative|co-?op\b", n):
        return "cooperative"
    return "company"


def coverage_level(listing: str, ids: Identifiers, kind: str) -> str:
    """How much public data to expect: full (listed), registry (has CIN/LLPIN), basic (name only)."""
    if kind in ("association", "government_body"):
        return "not_scored"
    if listing == LISTED and (ids.nse_symbol or ids.bse_code):
        return "full"
    if ids.cin or ids.llpin:
        return "registry"
    return "basic"


# Rows and the whole sheet -------------------------------------------------------------------


@dataclass
class DirectoryRow:
    source: str  # "Sheet!row"
    category_id: str
    sector: str
    sub_sector: str
    industry: str
    listing: str
    board: str | None
    size_tier: str
    size_bucket_raw: str
    name: str
    ids: Identifiers
    market_cap_text: str
    source_url: str
    entity: str
    coverage: str

    @property
    def peer_group(self) -> str:
        return " / ".join((self.sector, self.industry, self.listing, self.size_tier))


def clean_row(sheet: str, row_number: int, raw: dict[str, str]) -> DirectoryRow:
    status = raw.get("Listing Status", "")
    ticker = raw.get("Ticker / CIN", "")
    looks_listed = bool(re.search(r"(?<!un)listed|mainboard|main-board|\bnse\b|\bbse\b|emerge", status.lower()))
    ids = parse_identifiers(ticker, listed=looks_listed)
    listing, board = normalize_listing(status, ids)
    name = " ".join(raw.get("Company Name", "").split())
    cap_text = raw.get("Market Cap / Turnover / Valuation", "")
    kind = entity_type(name, status, ticker)
    return DirectoryRow(
        source=f"{sheet}!{row_number}",
        category_id=raw.get("Category ID", "").strip(),
        sector=sheet.strip(),
        sub_sector=raw.get("Sub-sector", "").strip(),
        industry=normalize_industry(sheet.strip(), raw.get("Sub-sector", ""), name),
        listing=listing,
        board=board,
        size_tier=normalize_size(raw.get("Size Bucket", ""), listing, board, market_cap_crore(cap_text)),
        size_bucket_raw=raw.get("Size Bucket", "").strip(),
        name=name,
        ids=ids,
        market_cap_text=cap_text.strip(),
        source_url=raw.get("Source URL", "").strip(),
        entity=kind,
        coverage=coverage_level(listing, ids, kind),
    )


NAME_NOISE = re.compile(
    r"\b(ltd|limited|pvt|private|the|co|company|inc|llp|opc|corporation|corp)\b|\(.*?\)|[^a-z0-9& ]"
)


def name_key(name: str) -> str:
    return " ".join(NAME_NOISE.sub(" ", (name or "").lower()).split())


def identity_keys(row: DirectoryRow) -> list[str]:
    keys = []
    for prefix, value in (("cin", row.ids.cin), ("llpin", row.ids.llpin), ("nse", row.ids.nse_symbol), ("bse", row.ids.bse_code)):
        if value:
            keys.append(f"{prefix}:{value}")
    if row.name:
        keys.append(f"name:{name_key(row.name)}")
    return keys


SECTOR_PREFERENCE_LAST = ("Diversified & Conglomerates",)  # holding-company tab repeats firms filed elsewhere


@dataclass
class DirectoryEntry:
    primary: DirectoryRow
    others: list[DirectoryRow] = field(default_factory=list)

    @property
    def rows(self) -> list[DirectoryRow]:
        return [self.primary, *self.others]

    def merged_ids(self) -> Identifiers:
        merged = Identifiers()
        for row in self.rows:
            for attr in ("nse_symbol", "bse_code", "cin", "llpin", "funding_stage"):
                if getattr(merged, attr) is None and getattr(row.ids, attr):
                    setattr(merged, attr, getattr(row.ids, attr))
        return merged


def group_duplicates(rows: list[DirectoryRow]) -> list[DirectoryEntry]:
    """Merge rows that share any identifier or the same normalised name (union-find)."""
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_with_key: dict[str, int] = {}
    for i, row in enumerate(rows):
        for key in identity_keys(row):
            if key.startswith("name:") and len(key) < 8:  # too short to be a safe match
                continue
            if key in first_with_key:
                parent[find(i)] = find(first_with_key[key])
            else:
                first_with_key[key] = i

    groups: dict[int, list[DirectoryRow]] = defaultdict(list)
    for i, row in enumerate(rows):
        groups[find(i)].append(row)

    entries = []
    for members in groups.values():
        def rank(row: DirectoryRow) -> tuple:
            id_count = sum(1 for v in (row.ids.nse_symbol, row.ids.bse_code, row.ids.cin, row.ids.llpin) if v)
            generic = row.industry.startswith("Other ") or "/" in row.sub_sector
            return (row.sector in SECTOR_PREFERENCE_LAST, -id_count, generic)
        ordered = sorted(members, key=rank)
        entries.append(DirectoryEntry(primary=ordered[0], others=ordered[1:]))
    return entries
