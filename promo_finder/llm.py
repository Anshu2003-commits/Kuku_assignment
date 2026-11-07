"""Provider-agnostic LLM layer.

The brief says a solution where the model is easy to swap will shine, so every
provider hides behind one `complete(system, user) -> dict` call returning parsed
JSON. Swapping models is a flag or an env var, never a code change.

    PROMO_PROVIDER = gemini | openai | groq | anthropic | mock
    PROMO_MODEL    = provider-specific model id
    GEMINI_API_KEY / OPENAI_API_KEY / GROQ_API_KEY / ANTHROPIC_API_KEY

With no key present we fall back to MockLLM so the prototype always runs.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from typing import Protocol

DEFAULT_MODELS = {
    # flash-lite judges a clip in ~2.5s. The big reasoning models took ~245s
    # per call and returned the same scores on this task - see README.
    "gemini": "gemini-3.1-flash-lite",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "anthropic": "claude-sonnet-5",
    "mock": "mock",
}

# Per-request HTTP timeout. Generous enough for a reasoning model, short
# enough that a dead socket fails fast instead of stalling the whole run.
TIMEOUT_MS = int(os.environ.get("PROMO_TIMEOUT_MS", "120000"))

KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response.

    Models wrap JSON in prose or fences even when told not to, so this is
    deliberately forgiving. A hard failure here is caught upstream and retried.
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        raise LLMError(f"no JSON object in response: {text[:200]!r}")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise LLMError(f"unbalanced JSON in response: {text[:200]!r}")


class GeminiLLM:
    """Gemini. Thinking is disabled by default.

    Judging one 30-second clip is a shallow, high-volume call: with extended
    thinking on, a single judgement took ~100s, which is ~5 hours for a 50
    episode show. Off, it is a couple of seconds with no observed quality loss
    on this task. Raise PROMO_THINKING_BUDGET if you want it back.
    """

    def __init__(self, model: str, api_key: str, thinking_budget: int | None = None):
        from google import genai  # google-genai
        from google.genai import types

        self.name = f"gemini:{model}"
        self.model = model
        if thinking_budget is None:
            thinking_budget = int(os.environ.get("PROMO_THINKING_BUDGET", "0"))
        self.thinking_budget = thinking_budget
        # Without an explicit timeout the SDK can block on a dead socket
        # forever - a 200-call run once hung for two hours on a single
        # request. Always bound it; the retry layer handles the failure.
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=TIMEOUT_MS),
        )

    def complete(self, system: str, user: str) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            response_mime_type="application/json",
        )
        # Not every model exposes a thinking budget; ignore it where unsupported.
        try:
            config.thinking_config = types.ThinkingConfig(
                thinking_budget=self.thinking_budget
            )
        except Exception:  # noqa: BLE001
            pass

        resp = self._client.models.generate_content(
            model=self.model, contents=user, config=config
        )
        return resp.text or ""


class OpenAICompatLLM:
    """Works for OpenAI and for anything OpenAI-compatible (Groq, vLLM, ...)."""

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        from openai import OpenAI

        self.name = f"openai-compat:{model}"
        self.model = model
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=TIMEOUT_MS / 1000
        )

    def complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""


class AnthropicLLM:
    def __init__(self, model: str, api_key: str):
        import anthropic

        self.name = f"anthropic:{model}"
        self.model = model
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=TIMEOUT_MS / 1000
        )

    def complete(self, system: str, user: str) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=0.2,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")


class MockLLM:
    """Offline stand-in so `--no-llm` still produces a complete, honest run.

    It does NOT pretend to judge. It echoes the heuristic view back in the same
    schema, with neutral scores, so downstream code and the UI are exercised
    end to end without a key. Outputs are marked `"judged_by": "mock"`.
    """

    name = "mock"

    def complete(self, system: str, user: str) -> str:
        # Only the PROPOSED CLIP section is selectable; the lead-in is context.
        body = user.split("PROPOSED CLIP", 1)[-1]
        clip = re.findall(r"\[([A-Z]\d+\.B\d+)\]", body)
        first, last = (clip[0], clip[-1]) if clip else ("", "")
        m = re.search(r"\[[^\]]+\]\s*\([^)]*\)\s*(.+)", body)
        first_line = m.group(1)[:120] if m else ""
        return json.dumps(
            {
                "keep": True,
                "reject_reason": None,
                "start_beat": first,
                "end_beat": last,
                "hook": f"(mock) opens on: {first_line}",
                "cliffhanger": "(mock) ends on the final beat of the window",
                "why_it_works": "(mock) heuristic-only run - no model judgement",
                "hook_strength": 3,
                "cliffhanger_strength": 3,
                "standalone": 3,
                "spoiler_risk": 3,
            }
        )


def build_llm(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> tuple[LLM, list[str]]:
    """Construct an LLM from flags/env. Returns (llm, notes-for-the-run-report)."""
    notes: list[str] = []
    provider = (provider or os.environ.get("PROMO_PROVIDER") or "gemini").lower()
    model = model or os.environ.get("PROMO_MODEL") or DEFAULT_MODELS.get(provider, "")

    if provider == "mock":
        return MockLLM(), ["provider=mock (explicit)"]

    key = api_key or os.environ.get(KEY_ENV.get(provider, ""), "")
    if not key:
        notes.append(
            f"no {KEY_ENV.get(provider, 'API')} set - falling back to MockLLM; "
            f"scores are placeholders, not judgements"
        )
        return MockLLM(), notes

    try:
        if provider == "gemini":
            return GeminiLLM(model, key), notes
        if provider == "openai":
            return OpenAICompatLLM(model, key), notes
        if provider == "groq":
            return OpenAICompatLLM(
                model, key, base_url="https://api.groq.com/openai/v1"
            ), notes
        if provider == "anthropic":
            return AnthropicLLM(model, key), notes
    except ImportError as exc:
        notes.append(f"{provider} SDK not installed ({exc}) - falling back to MockLLM")
        return MockLLM(), notes

    notes.append(f"unknown provider {provider!r} - falling back to MockLLM")
    return MockLLM(), notes


class RateLimiter:
    """Sliding-window limiter so we stay under a provider's requests-per-minute.

    Gemini's free tier is 15 RPM, and blowing through it costs far more time in
    429 backoff than pacing does. Set PROMO_RPM to match your tier (a paid tier
    at 1000 RPM effectively removes the limit).
    """

    def __init__(self, rpm: int):
        self.rpm = max(1, rpm)
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._times and now - self._times[0] >= 60.0:
                    self._times.popleft()
                if len(self._times) < self.rpm:
                    self._times.append(now)
                    return
                wait = 60.0 - (now - self._times[0]) + 0.05
            time.sleep(max(0.01, wait))


def default_rate_limiter(provider: str) -> RateLimiter:
    env = os.environ.get("PROMO_RPM")
    if env:
        return RateLimiter(int(env))
    # Free-tier defaults, deliberately one under the published limit.
    return RateLimiter({"gemini": 14, "groq": 28}.get(provider, 60))


_RETRY_HINT = re.compile(r"retry in ([\d.]+)\s*s", re.I)


def _suggested_delay(exc: Exception) -> float | None:
    m = _RETRY_HINT.search(str(exc))
    return float(m.group(1)) + 0.5 if m else None


def complete_with_retry(
    llm: LLM,
    system: str,
    user: str,
    attempts: int = 4,
    backoff: float = 2.0,
    limiter: RateLimiter | None = None,
) -> str:
    """Retry transient API failures, honouring the server's own retry hint."""
    last: Exception | None = None
    for n in range(attempts):
        if limiter is not None:
            limiter.acquire()
        try:
            return llm.complete(system, user)
        except Exception as exc:  # noqa: BLE001 - provider SDKs raise freely
            last = exc
            if n == attempts - 1:
                break
            hint = _suggested_delay(exc)
            time.sleep(hint if hint is not None else backoff * (2**n))
    raise LLMError(f"{llm.name} failed after {attempts} attempts: {last}")
