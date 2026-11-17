"""Render the Company Scorecard to HTML, and to PDF through headless Chromium.

The design uses CSS grid, colour variables and gradients that xhtml2pdf cannot draw, so the
PDF is printed by a real browser engine, the same kind Claude Design rendered it in.
Chromium comes from the optional `scorecard` extra:

    pip install -e ".[scorecard]"
    python -m playwright install chromium
"""

from typing import Any

from app.reports.renderer import build_environment

# Copied from the design's THEMES (design/Scorecard Page.dc.html).
THEMES: dict[str, dict[str, str]] = {
    "paper": {
        "--page": "#FBF8F2", "--panel": "#F2EDE4", "--rule": "#DDD5C6", "--rule-strong": "#14141A",
        "--ink": "#14141A", "--ink2": "#32323E", "--ink3": "#5E5B63",
        "--gold": "#C9A84C", "--gold-text": "#7E6326",
        "--low": "#4A9B7F", "--low-text": "#2F7560", "--mod": "#C9A84C", "--elev": "#F07050", "--elev-text": "#B03A20",
        "--high": "#E05533", "--high-text": "#B03A20",
        "--empty-track": "#E4DDD0", "--nodata-fill": "transparent", "--nodata-line": "#32323E",
        "--cta-bg": "#F2EDE4", "--logo-s": "#14141A", "--hatch": "rgba(20,20,26,0.07)",
    },
    "night": {
        "--page": "#0D0D0F", "--panel": "rgba(201,168,76,0.07)", "--rule": "rgba(201,168,76,0.16)",
        "--rule-strong": "rgba(201,168,76,0.45)",
        "--ink": "#F2EDE4", "--ink2": "#CFC9BE", "--ink3": "#9D978D",
        "--gold": "#C9A84C", "--gold-text": "#E8C76A",
        "--low": "#4A9B7F", "--low-text": "#6BB89A", "--mod": "#C9A84C", "--elev": "#F07050", "--elev-text": "#F07050",
        "--high": "#E05533", "--high-text": "#F07050",
        "--empty-track": "#24242E", "--nodata-fill": "#1C1C24", "--nodata-line": "#6A6A78",
        "--cta-bg": "rgba(201,168,76,0.10)", "--logo-s": "#0D0D0F", "--hatch": "rgba(242,237,228,0.05)",
    },
}
SCREEN_BACKGROUND = {"paper": "#D8D3CA", "night": "#080809"}


class ScorecardRenderError(RuntimeError):
    pass


def render_scorecard_html(view: dict[str, Any], theme: str = "paper") -> str:
    if theme not in THEMES:
        raise ValueError(f"Unknown theme {theme!r}; use one of {sorted(THEMES)}.")
    template = build_environment().get_template("scorecard.html")
    return template.render(s=view, theme=THEMES[theme], screen_bg=SCREEN_BACKGROUND[theme])


def render_scorecard_pdf(html: str, timeout_ms: int = 30000) -> bytes:
    """Print the HTML to an A4 PDF with backgrounds, waiting for the web fonts to load."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScorecardRenderError(
            'PDF rendering needs Playwright: pip install -e ".[scorecard]" && python -m playwright install chromium'
        ) from exc

    try:
        with sync_playwright() as playwright:
            # Small containers (Render's free plan: 512 MB) have a tiny /dev/shm and no GPU.
            browser = playwright.chromium.launch(args=["--disable-dev-shm-usage", "--disable-gpu", "--no-zygote"])
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="networkidle", timeout=timeout_ms)
                page.evaluate("document.fonts.ready")
                return page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ScorecardRenderError(f"Chromium could not render the scorecard: {exc}") from exc
