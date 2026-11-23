"""Clean the company taxonomy spreadsheet into data/company_directory.csv and a change report.

    python scripts/clean_company_directory.py "data/raw/India Company Taxonomy & Database.xlsx"

The spreadsheet itself is never modified. Outputs:
    data/company_directory.csv               one row per company, cleaned
    reports/company_directory/report.md      what was changed and why, with counts
    reports/company_directory/*.csv          the detail behind each section of the report
"""

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.companies.directory import DirectoryEntry, DirectoryRow, clean_row, group_duplicates  # noqa: E402

COLUMNS = [
    "name", "sector", "industry", "listing", "board", "size_tier", "peer_group",
    "nse_symbol", "bse_code", "cin", "llpin", "funding_stage",
    "entity_type", "coverage", "category_id", "sub_sector", "size_bucket_raw",
    "market_cap_text", "source_url", "also_listed_under", "source_rows",
]
MIN_PEERS = 5


def read_sheet(path: Path) -> list[DirectoryRow]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = []
    for sheet in workbook.worksheets:
        values = sheet.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(values)]
        for number, cells in enumerate(values, start=2):
            if not any(c not in (None, "") for c in cells):
                continue
            raw = {header[i]: str(cells[i]).strip() if i < len(cells) and cells[i] is not None else "" for i in range(len(header))}
            if raw.get("Company Name"):
                rows.append(clean_row(sheet.title, number, raw))
    return rows


def entry_record(entry: DirectoryEntry) -> dict:
    p, ids = entry.primary, entry.merged_ids()
    also = sorted({f"{r.sector} / {r.industry} ({r.category_id})" for r in entry.others
                   if (r.sector, r.industry) != (p.sector, p.industry)})
    return {
        "name": p.name, "sector": p.sector, "industry": p.industry, "listing": p.listing,
        "board": p.board or "", "size_tier": p.size_tier, "peer_group": p.peer_group,
        "nse_symbol": ids.nse_symbol or "", "bse_code": ids.bse_code or "", "cin": ids.cin or "",
        "llpin": ids.llpin or "", "funding_stage": ids.funding_stage or "",
        "entity_type": p.entity, "coverage": p.coverage, "category_id": p.category_id,
        "sub_sector": p.sub_sector, "size_bucket_raw": p.size_bucket_raw,
        "market_cap_text": p.market_cap_text, "source_url": p.source_url,
        "also_listed_under": "; ".join(also), "source_rows": "; ".join(r.source for r in entry.rows),
    }


def write_csv(path: Path, columns: list[str], records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spreadsheet", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data/company_directory.csv"))
    parser.add_argument("--report-dir", type=Path, default=Path("reports/company_directory"))
    args = parser.parse_args()

    rows = read_sheet(args.spreadsheet)
    entries = group_duplicates(rows)
    records = sorted((entry_record(e) for e in entries), key=lambda r: (r["sector"], r["industry"], r["name"].lower()))
    write_csv(args.out, COLUMNS, records)

    report = args.report_dir
    # 1. Duplicates merged
    merged = [e for e in entries if e.others]
    write_csv(report / "duplicates_merged.csv", ["kept", "kept_from", "merged_rows", "merged_from"], [
        {"kept": e.primary.name, "kept_from": f"{e.primary.source} {e.primary.sector} / {e.primary.sub_sector} ({e.primary.category_id})",
         "merged_rows": " | ".join(r.name for r in e.others),
         "merged_from": " | ".join(f"{r.source} {r.sector} / {r.sub_sector} ({r.category_id})" for r in e.others)}
        for e in merged])
    conflicts = []
    for e in merged:
        for attr in ("cin", "nse_symbol", "bse_code"):
            values = {getattr(r.ids, attr) for r in e.rows if getattr(r.ids, attr)}
            if len(values) > 1:
                conflicts.append({"company": e.primary.name, "field": attr, "values": " | ".join(sorted(values)),
                                  "rows": " | ".join(r.source for r in e.rows)})
    write_csv(report / "identifier_conflicts.csv", ["company", "field", "values", "rows"], conflicts)

    # 2. Category IDs reused for different groups
    by_category: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_category[r.category_id][(r.sector, r.sub_sector, r.listing)] += 1
    reused = [{"category_id": c, "groups": " | ".join(f"{s} / {sub} / {lst}: {n}" for (s, sub, lst), n in groups.most_common())}
              for c, groups in sorted(by_category.items()) if len(groups) > 1]
    write_csv(report / "category_ids_reused.csv", ["category_id", "groups"], reused)

    # 3. Peer groups
    peer_sizes = Counter(r["peer_group"] for r in records if r["coverage"] != "not_scored")
    write_csv(report / "peer_groups.csv", ["peer_group", "companies"],
              [{"peer_group": g, "companies": n} for g, n in sorted(peer_sizes.items())])

    # 4. Rows needing a look
    flagged = [r for r in records if r["entity_type"] != "company" or r["industry"].startswith("Other ")
               or r["size_tier"] == "unknown" or r["coverage"] == "basic"]
    write_csv(report / "flagged.csv", COLUMNS, flagged)

    count = lambda field: Counter(r[field] for r in records)  # noqa: E731
    lines = [
        "# Company directory: cleaning report",
        "",
        f"Source: `{args.spreadsheet.name}` — {len(rows):,} rows across {len({r.sector for r in rows})} sector tabs.",
        f"Result: **{len(records):,} companies** in `{args.out}`.",
        "",
        "## What was changed",
        "",
        f"- **Duplicates merged:** {len(merged):,} companies appeared more than once ({sum(len(e.others) for e in merged):,} extra rows). "
        "One row is kept (the one with most identifiers, preferring the company's own sector over the holding-company tab); "
        "the others are kept in `also_listed_under` and `source_rows`. Detail: `duplicates_merged.csv`.",
        f"- **Identifier conflicts:** {len(conflicts)} merged companies carry two different CINs or tickers. Detail: `identifier_conflicts.csv`.",
        f"- **Category IDs reused:** {len(reused)} of {len(by_category)} IDs cover more than one sub-sector or listing. "
        "Peer groups are rebuilt as sector / industry / listing / size instead; the original ID is kept in `category_id`. Detail: `category_ids_reused.csv`.",
        f"- **Listing status:** {len({r.listing for r in rows})} values after cleaning (from 30 spellings): "
        + ", ".join(f"{k} {v:,}" for k, v in count("listing").most_common()) + ".",
        f"- **Size:** 156 spellings folded into tiers: " + ", ".join(f"{k} {v:,}" for k, v in count("size_tier").most_common()) + ".",
        f"- **Industries:** Financial Services (135 sub-sector names) and IT (439) folded into short lists; other tabs kept as they were. "
        f"{sum(1 for r in records if r['industry'].startswith('Other ')):,} companies did not match a rule and sit in an 'Other' industry.",
        f"- **Identifiers:** NSE symbols {sum(1 for r in records if r['nse_symbol']):,}, BSE codes {sum(1 for r in records if r['bse_code']):,}, "
        f"CINs {sum(1 for r in records if r['cin']):,}, LLPINs {sum(1 for r in records if r['llpin']):,}. "
        "BSE codes stored as numbers (\"539947.0\") were restored; LLP numbers mistaken for tickers were recognised.",
        "",
        "## Coverage to expect",
        "",
        "- full: listed with a ticker (share price, financial results, news)",
        "- registry: unlisted with a CIN or LLPIN (government registry and news)",
        "- basic: name only (news only; scores will be 'Limited data')",
        "- not_scored: associations and government bodies (kept for search, never scored)",
        "",
        "| coverage | companies |", "|---|---|",
        *(f"| {k} | {v:,} |" for k, v in count("coverage").most_common()),
        "",
        "| entity type | companies |", "|---|---|",
        *(f"| {k} | {v:,} |" for k, v in count("entity_type").most_common()),
        "",
        "## Peer groups",
        "",
        f"{len(peer_sizes):,} peer groups. {sum(1 for n in peer_sizes.values() if n >= MIN_PEERS):,} have {MIN_PEERS}+ companies; "
        f"the other {sum(1 for n in peer_sizes.values() if n < MIN_PEERS):,} fall back to sector / industry when scoring. Detail: `peer_groups.csv`.",
        "",
        "| sector | companies |", "|---|---|",
        *(f"| {k} | {v:,} |" for k, v in sorted(count("sector").items())),
    ]
    (report / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
