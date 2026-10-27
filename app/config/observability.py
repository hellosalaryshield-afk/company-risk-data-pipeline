"""Optional request tracing with Logfire, for seeing what one request does step by step.

Off unless LOGFIRE_TOKEN is set, and harmless when the logfire package is not installed,
so tests and production behave exactly as before. Install it locally with:

    pip install -e ".[observability]"

Only public data may be traced on Logfire's hosted service. Never enable tracing for code
paths that handle personal data such as offer letters.
"""

from contextlib import AbstractContextManager, nullcontext
from typing import Any

from app.config.settings import Settings, get_settings

_enabled = False


def configure_observability(settings: Settings | None = None, app: Any = None) -> bool:
    global _enabled
    settings = settings or get_settings()
    if not settings.logfire_token:
        return False

    try:
        import logfire
    except ImportError:
        return False

    if not _enabled:
        logfire.configure(
            token=settings.logfire_token,
            service_name="company-risk-data-pipeline",
            console=False,
            scrubbing=logfire.ScrubbingOptions(extra_patterns=["x-goog-api-key", "x-api-key"]),
        )
        # Bodies only. Headers carry API keys, and Logfire never scrubs URLs.
        logfire.instrument_httpx(capture_request_body=True, capture_response_body=True)
        logfire.instrument_sqlalchemy()
        _enabled = True

    if app is not None:
        logfire.instrument_fastapi(app)
    return True


def step(name: str, **attributes: Any) -> AbstractContextManager:
    """A labelled block in the trace timeline, or nothing when tracing is off."""
    if not _enabled:
        return nullcontext()

    import logfire

    return logfire.span(name, **attributes)


def note(message: str, **attributes: Any) -> None:
    """A one-line event in the trace timeline, or nothing when tracing is off."""
    if not _enabled:
        return

    import logfire

    logfire.info(message, **attributes)
