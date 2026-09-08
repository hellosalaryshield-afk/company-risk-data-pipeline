from io import BytesIO
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"

COVERAGE_CLASSES = {"Good": "ok", "Partial": "warn", "Thin": "bad"}


class PdfRenderError(RuntimeError):
    pass


def build_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_company_report_html(report: dict, footer_note: str | None = None) -> str:
    template = build_environment().get_template("company_report.html")
    return template.render(
        report=report,
        coverage_class=COVERAGE_CLASSES.get(report["coverage"]["band"], "muted"),
        footer_note=footer_note,
    )


def render_company_report_pdf(report: dict, footer_note: str | None = None) -> bytes:
    """Render the same HTML to PDF.

    xhtml2pdf is pure Python, so this works on a small container without system
    libraries such as those WeasyPrint needs. That keeps the hosting cost low.
    """
    from xhtml2pdf import pisa

    html = render_company_report_html(report, footer_note=footer_note)
    buffer = BytesIO()
    status = pisa.CreatePDF(src=html, dest=buffer, encoding="utf-8")
    if status.err:
        raise PdfRenderError("PDF generation failed while converting the report HTML.")

    return buffer.getvalue()
