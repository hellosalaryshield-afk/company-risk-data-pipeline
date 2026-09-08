import argparse
from pathlib import Path

from app.companies.normalization import normalize_company_name
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.reports.company_report import build_company_report, resolve_for_report
from app.reports.renderer import render_company_report_html, render_company_report_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an HTML and PDF signal report for one company.")
    parser.add_argument("company_name", help="Company name or alias, for example TCS")
    parser.add_argument("--out-dir", default="reports", help="Directory to write the report files into")
    parser.add_argument("--html-only", action="store_true", help="Skip PDF generation")
    args = parser.parse_args()

    db = next(get_db_session())
    resolution = resolve_for_report(db, args.company_name)

    if not resolution["company"]:
        print(f"Status: {resolution['status']}. Could not resolve '{args.company_name}'.")
        for candidate in resolution["candidates"]:
            print(f"  candidate: {candidate['canonical_name']} (id={candidate['id']})")
        raise SystemExit(1)

    company = resolution["company"]
    report = build_company_report(db, company)
    footer_note = get_settings().report_footer_note

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = normalize_company_name(company.canonical_name).replace(" ", "_") or f"company_{company.id}"

    html_path = out_dir / f"{slug}_signal_report.html"
    html_path.write_text(render_company_report_html(report, footer_note=footer_note), encoding="utf-8")
    print(f"HTML: {html_path}")

    if not args.html_only:
        pdf_path = out_dir / f"{slug}_signal_report.pdf"
        pdf_path.write_bytes(render_company_report_pdf(report, footer_note=footer_note))
        print(f"PDF:  {pdf_path}")

    print(
        f"Company: {company.canonical_name} | segment: {report['segment']['segment'] or 'undetermined'} | "
        f"signals: {len(report['kpis'])} | coverage: {report['coverage']['band']}"
    )


if __name__ == "__main__":
    main()
