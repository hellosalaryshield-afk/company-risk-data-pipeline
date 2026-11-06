import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
RETRYABLE_STATUSES = {500, 502, 503, 504}


class GeminiError(RuntimeError):
    pass


class GeminiRateLimitError(GeminiError):
    """Quota or rate limit reached. On the free tier this is usually the daily cap."""


@dataclass(frozen=True)
class GeminiResult:
    data: Any
    model: str
    usage: dict[str, Any]


class GeminiClient:
    """Minimal client for the Gemini generateContent REST endpoint, JSON output only.

    Free-tier terms allow Google to use submitted content and to have humans review it,
    so only public data such as news articles may be sent through a free-tier key. Never
    send personal data (offer letters, CVs) through this client on a free key.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        retry_waits: tuple[float, ...] = (5.0, 20.0),
    ):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.retry_waits = retry_waits

    def generate_json(self, prompt: str, response_schema: dict[str, Any]) -> GeminiResult:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": response_schema,
                "temperature": 0,
            },
        }

        # Google answers 500/503 ("high demand") during load spikes that pass within seconds,
        # so those are retried. A 429 is a quota limit and is not.
        for wait in (*self.retry_waits, None):
            try:
                response = httpx.post(
                    f"{GEMINI_API_BASE}/models/{self.model}:generateContent",
                    json=body,
                    headers={"x-goog-api-key": self.api_key},
                    timeout=self.timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                if wait is None:
                    raise GeminiError("Gemini request timed out.") from exc
                time.sleep(wait)
                continue
            except httpx.HTTPError as exc:
                raise GeminiError(f"Gemini request failed: {exc.__class__.__name__}.") from exc
            if response.status_code in RETRYABLE_STATUSES and wait is not None:
                time.sleep(wait)
                continue
            break

        if response.status_code == 429:
            raise GeminiRateLimitError(f"Gemini quota or rate limit reached: {error_message(response)}")
        if response.status_code != 200:
            raise GeminiError(f"Gemini returned HTTP {response.status_code}: {error_message(response)}")

        payload = response.json()
        text = response_text(payload)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GeminiError("Gemini returned text that is not valid JSON.") from exc

        return GeminiResult(
            data=data,
            model=payload.get("modelVersion") or self.model,
            usage=payload.get("usageMetadata") or {},
        )


def response_text(payload: dict[str, Any]) -> str:
    block_reason = (payload.get("promptFeedback") or {}).get("blockReason")
    if block_reason:
        raise GeminiError(f"Gemini blocked the prompt: {block_reason}.")

    candidates = payload.get("candidates") or []
    if not candidates:
        raise GeminiError("Gemini returned no candidates.")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    if not text.strip():
        reason = candidates[0].get("finishReason") or "unknown"
        raise GeminiError(f"Gemini returned an empty response (finish reason: {reason}).")
    return text


def error_message(response: httpx.Response) -> str:
    """Pull Google's error message without echoing the request, which carries the key."""
    try:
        return response.json().get("error", {}).get("message", "")[:300]
    except ValueError:
        return response.text[:300]
