import json

import httpx
import pytest

from app.sources import gemini
from app.sources.gemini import GeminiClient, GeminiError, GeminiRateLimitError


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(gemini.time, "sleep", lambda seconds: None)


def fake_post(status_code=200, payload=None, calls=None):
    def post(url, json=None, headers=None, timeout=None):
        if calls is not None:
            calls.append({"url": url, "json": json, "headers": headers})
        request = httpx.Request("POST", url)
        return httpx.Response(status_code, json=payload if payload is not None else {}, request=request)

    return post


def ok_payload(data):
    return {
        "candidates": [{"content": {"parts": [{"text": json.dumps(data)}]}, "finishReason": "STOP"}],
        "usageMetadata": {"totalTokenCount": 42},
        "modelVersion": "gemini-test-001",
    }


def test_generate_json_parses_the_response_and_sends_the_key_in_a_header(monkeypatch):
    calls = []
    monkeypatch.setattr(gemini.httpx, "post", fake_post(payload=ok_payload({"articles": []}), calls=calls))

    result = GeminiClient("secret-key", "gemini-test").generate_json("prompt", {"type": "OBJECT"})

    assert result.data == {"articles": []}
    assert result.model == "gemini-test-001"
    assert result.usage["totalTokenCount"] == 42
    assert calls[0]["url"].endswith("/models/gemini-test:generateContent")
    assert "secret-key" not in calls[0]["url"]
    assert calls[0]["headers"]["x-goog-api-key"] == "secret-key"
    config = calls[0]["json"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["temperature"] == 0


def test_rate_limit_is_its_own_error(monkeypatch):
    payload = {"error": {"message": "Quota exceeded"}}
    monkeypatch.setattr(gemini.httpx, "post", fake_post(status_code=429, payload=payload))

    with pytest.raises(GeminiRateLimitError, match="Quota exceeded"):
        GeminiClient("k", "m").generate_json("p", {})


def test_other_http_errors_carry_googles_message(monkeypatch):
    payload = {"error": {"message": "API key not valid"}}
    monkeypatch.setattr(gemini.httpx, "post", fake_post(status_code=400, payload=payload))

    with pytest.raises(GeminiError, match="HTTP 400: API key not valid"):
        GeminiClient("k", "m").generate_json("p", {})


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "blocked"),
        ({"candidates": []}, "no candidates"),
        ({"candidates": [{"content": {"parts": [{"text": " "}]}, "finishReason": "MAX_TOKENS"}]}, "MAX_TOKENS"),
        ({"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}, "not valid JSON"),
    ],
)
def test_unusable_responses_raise(monkeypatch, payload, message):
    monkeypatch.setattr(gemini.httpx, "post", fake_post(payload=payload))

    with pytest.raises(GeminiError, match=message):
        GeminiClient("k", "m").generate_json("p", {})


def test_thought_parts_are_ignored(monkeypatch):
    payload = {
        "candidates": [
            {"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"a": 1}'}]}}
        ]
    }
    monkeypatch.setattr(gemini.httpx, "post", fake_post(payload=payload))

    assert GeminiClient("k", "m").generate_json("p", {}).data == {"a": 1}


def test_timeouts_become_gemini_errors(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(gemini.httpx, "post", timeout)

    with pytest.raises(GeminiError, match="timed out"):
        GeminiClient("k", "m").generate_json("p", {})


def test_a_temporary_503_is_retried(monkeypatch):
    responses = [
        httpx.Response(503, json={"error": {"message": "high demand"}}, request=httpx.Request("POST", "u")),
        httpx.Response(200, json=ok_payload({"ok": True}), request=httpx.Request("POST", "u")),
    ]
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(gemini.httpx, "post", post)

    assert GeminiClient("k", "m").generate_json("p", {}).data == {"ok": True}
    assert len(calls) == 2


def test_a_persistent_503_gives_up_after_the_retries(monkeypatch):
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        return httpx.Response(503, json={"error": {"message": "high demand"}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(gemini.httpx, "post", post)

    with pytest.raises(GeminiError, match="HTTP 503"):
        GeminiClient("k", "m", retry_waits=(1.0, 1.0)).generate_json("p", {})
    assert len(calls) == 3


def test_a_quota_429_is_not_retried(monkeypatch):
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        return httpx.Response(429, json={"error": {"message": "Quota"}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(gemini.httpx, "post", post)

    with pytest.raises(GeminiRateLimitError):
        GeminiClient("k", "m").generate_json("p", {})
    assert len(calls) == 1
