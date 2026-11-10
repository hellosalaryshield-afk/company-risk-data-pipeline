import argparse
from pathlib import Path

from app.companies.repository import CompanyRepository
from app.companies.resolver import CompanyResolver
from app.database.session import get_db_session
from app.reports.scorecard import build_scorecard
from app.reports.scorecard_render import THEMES, render_scorecard_html, render_scorecard_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Company Scorecard (HTML and PDF) for one company.")
    parser.add_argument("company", help="Company name or alias")
    parser.add_argument("--theme", choices=sorted(THEMES), default="paper")
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--html-only", action="store_true", help="Skip the PDF (no Chromium needed)")
    args = parser.parse_args()

    db = next(get_db_session())
    resolved = CompanyResolver(CompanyRepository(db)).resolve(args.company)
    if not resolved.match:
        names = ", ".join(c.canonical_name for c in resolved.candidates) or "none"
        raise SystemExit(f"Company not resolved ({resolved.status}). Candidates: {names}")

    company = resolved.match
    view = build_scorecard(db, company)
    html = render_scorecard_html(view, args.theme)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{company.canonical_name.lower().replace(' ', '_')}_scorecard_{args.theme}"
    html_path = args.out_dir / f"{stem}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"HTML: {html_path}")
    if not args.html_only:
        pdf_path = args.out_dir / f"{stem}.pdf"
        pdf_path.write_bytes(render_scorecard_pdf(html))
        print(f"PDF:  {pdf_path}")

    score = f"{view['score']} ({view['band']})" if view["scored"] else "not enough data"
    print(f"{company.canonical_name}: score {score}, confidence {view['confidence']}, {len(view['timeline'])} news events")


if __name__ == "__main__":
    main()
