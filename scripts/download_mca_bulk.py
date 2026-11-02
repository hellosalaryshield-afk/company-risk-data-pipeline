import argparse
import csv
import io
import time
from pathlib import Path

import httpx

from app.config.settings import get_settings
from app.sources.data_gov_mca import DATA_GOV_MCA_ENDPOINT, USER_AGENT

DEFAULT_OUT_DIR = Path("data/raw/mca")

# Larger pages are cut off mid-response by the server (seen at 10,000), so stay at 1,000.
PAGE_SIZE = 1000


def fetch_total(api_key: str) -> int:
    response = httpx.get(
        DATA_GOV_MCA_ENDPOINT,
        params={"api-key": api_key, "format": "json", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    return int(response.json()["total"])


def fetch_page(api_key: str, offset: int) -> str:
    response = httpx.get(
        DATA_GOV_MCA_ENDPOINT,
        params={"api-key": api_key, "format": "csv", "limit": PAGE_SIZE, "offset": offset},
        headers={"User-Agent": USER_AGENT},
        timeout=120,
    )
    response.raise_for_status()
    return response.text


def row_count(page: str) -> int:
    """Data rows in a CSV page, or -1 if it is not the expected CSV at all."""
    if not page.startswith("CIN,"):
        return -1
    return sum(1 for _ in csv.reader(io.StringIO(page))) - 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Download the full MCA company master data from data.gov.in as CSV pages. "
            "Resumable: pages already on disk are skipped, so rerun after any interruption."
        )
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--max-pages", type=int, help="Stop after this many new pages, for a trial run")
    parser.add_argument("--pause", type=float, default=0.3, help="Seconds between requests")
    parser.add_argument("--retries", type=int, default=5)
    args = parser.parse_args()

    api_key = get_settings().data_gov_api_key
    if not api_key:
        raise SystemExit("DATA_GOV_API_KEY is not configured.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    total = fetch_total(api_key)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    print(f"{total:,} companies in {pages:,} pages of {PAGE_SIZE}. Saving to {args.out_dir}/")

    downloaded = skipped = 0
    started = time.monotonic()

    for page_number in range(pages):
        offset = page_number * PAGE_SIZE
        path = args.out_dir / f"page_{offset:08d}.csv"
        if path.exists():
            skipped += 1
            continue
        if args.max_pages is not None and downloaded >= args.max_pages:
            break

        expected = min(PAGE_SIZE, total - offset)
        for attempt in range(1, args.retries + 1):
            try:
                page = fetch_page(api_key, offset)
                rows = row_count(page)
                if rows == expected:
                    break
                problem = f"got {rows} rows, expected {expected}"
            except httpx.HTTPError as exc:
                problem = exc.__class__.__name__
            wait = 5 * 3 ** (attempt - 1)
            print(f"  offset {offset:,}: {problem}; retry {attempt}/{args.retries} in {wait}s")
            time.sleep(wait)
        else:
            raise SystemExit(f"Giving up at offset {offset:,}. Rerun to resume from here.")

        # Write to a temporary name first, so an interrupted write never looks complete.
        temporary = path.with_suffix(".part")
        temporary.write_text(page, encoding="utf-8")
        temporary.rename(path)
        downloaded += 1

        if downloaded % 25 == 0 or page_number == pages - 1:
            elapsed = time.monotonic() - started
            remaining = pages - page_number - 1
            eta_minutes = elapsed / downloaded * remaining / 60
            print(f"  {page_number + 1:,}/{pages:,} pages, about {eta_minutes:.0f} min left")
        time.sleep(args.pause)

    on_disk = len(list(args.out_dir.glob("page_*.csv")))
    print(f"\nDone this run: {downloaded} downloaded, {skipped} already present. {on_disk:,}/{pages:,} pages on disk.")


if __name__ == "__main__":
    main()
