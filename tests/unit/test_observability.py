from app.config.observability import configure_observability, note, step
from app.config.settings import Settings


def test_tracing_is_off_without_a_token():
    assert configure_observability(Settings(logfire_token=None)) is False


def test_step_and_note_do_nothing_when_tracing_is_off():
    with step("1. Find articles for {company}", company="Zepto") as span:
        note("Found {count} articles", count=3)

    assert span is None
