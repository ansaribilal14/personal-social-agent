"""NVIDIA NIM integration - isolated provider layer (spec section 47).

NIM exposes OpenAI-compatible chat completions. Everything AI-related in the
system goes through this module; no business logic calls HTTP directly.

Failure taxonomy (spec section 49): every failure maps to a typed exception so
callers can classify without parsing messages. No failure may publish content
or mutate approval state - callers treat NIMError as "skip/block", never
"force through".
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

import requests

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
# Live-verified 2026-09-07: meta/llama-3.3-70b-instruct reached end-of-life on
# NIM (HTTP 410); nvidia/nemotron-3.5-lightning-30b-a3b responds correctly and
# is the default. Nemotron-3 models are reasoning models - the client disables
# chain-of-thought via chat_template_kwargs (see _extra_payload) so `content`
# carries only the final answer. Override with NIM_MODEL; set NIM_THINKING=true
# to keep reasoning mode (content is then stripped of <think> blocks).
DEFAULT_MODEL = os.environ.get("NIM_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
DEFAULT_TIMEOUT = float(os.environ.get("NIM_TIMEOUT_SECONDS", "60"))
MAX_RETRIES = int(os.environ.get("NIM_MAX_RETRIES", "2"))

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class NIMError(Exception):
    """Base class - callers must treat any NIMError as blocking for that step."""


class NimUnavailable(NIMError):
    """No API key configured or provider unreachable -> step BLOCKED."""


class NimTimeout(NIMError):
    pass


class NimRateLimited(NIMError):
    pass


class NimInvalidJSON(NIMError):
    pass


class NimEmptyResponse(NIMError):
    pass


class NimProviderError(NIMError):
    pass


class NIMClient:
    """OpenAI-compatible chat client with retries, timeouts and structured output."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, timeout: float | None = None,
                 session: requests.Session | None = None):
        self.api_key = api_key or os.environ.get("NVIDIA_API_KEY")
        self.base_url = (base_url or os.environ.get("NVIDIA_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        self.session = session or requests.Session()
        self.prompt_versions: dict[str, str] = {}

    # ------------------------------------------------------------- plumbing
    def _headers(self) -> dict:
        if not self.api_key:
            raise NimUnavailable("NVIDIA_API_KEY not configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _extra_payload(model: str) -> dict:
        """Nemotron-3 reasoning models: default to thinking OFF so the final
        answer lands in `content` without reasoning preamble. Live-verified:
        with chat_template_kwargs {thinking: false} the model returns clean
        tweet-sized answers; without it, reasoning text leaks into content."""
        if "nemotron" in model.lower() and \
                os.environ.get("NIM_THINKING", "false").lower() != "true":
            return {"chat_template_kwargs": {"thinking": False}}
        return {}

    @staticmethod
    def _strip_think(text: str) -> str:
        """Defensive removal of <think>...</think> blocks from content."""
        return THINK_RE.sub("", text).strip()

    def chat(self, system: str, user: str, model: str | None = None,
             temperature: float = 0.4, max_tokens: int = 2048,
             on_retry: Callable[[int, Exception], None] | None = None) -> str:
        """Return assistant text. Raises typed NIMError subclasses on failure."""
        if not self.api_key:
            raise NimUnavailable("NVIDIA_API_KEY not configured")

        url = f"{self.base_url}/chat/completions"
        chosen_model = model or self.model
        payload = {
            "model": chosen_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self._extra_payload(chosen_model),
        }

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self.session.post(url, json=payload, headers=self._headers(),
                                         timeout=self.timeout)
                if resp.status_code == 429:
                    raise NimRateLimited("rate limited by provider")
                if resp.status_code >= 400:
                    # Never expose full provider response (may contain request ids/keys)
                    raise NimProviderError(f"provider error HTTP {resp.status_code}")
                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    raise NimEmptyResponse("empty choices from provider")
                content = (choices[0].get("message") or {}).get("content")
                if not content or not str(content).strip():
                    raise NimEmptyResponse("empty content from provider")
                return self._strip_think(str(content))
            except (NimRateLimited, NimTimeout, requests.Timeout,
                    requests.ConnectionError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    if on_retry:
                        on_retry(attempt, exc)
                    time.sleep(min(2 ** attempt, 8))
                    continue
                if isinstance(exc, requests.Timeout):
                    raise NimTimeout("provider timeout after retries") from exc
                if isinstance(exc, requests.ConnectionError):
                    raise NimUnavailable("provider unreachable") from exc
                raise
        raise last_exc or NimProviderError("unreachable")  # pragma: no cover

    # ----------------------------------------------------- structured output
    def chat_structured(self, system: str, user: str, model: str | None = None,
                        temperature: float = 0.2, max_tokens: int = 2048) -> dict:
        """Request a JSON object; validate it is a dict; typed failure otherwise.

        Malformed output is retried, then raises NimInvalidJSON. Callers must
        never let malformed output control publication (spec section 48).
        """
        prompt = (
            "Respond with a single valid JSON object and nothing else. "
            "No markdown fences, no commentary.\n\n" + user
        )
        last_error: Exception | None = None
        for _ in range(2):
            text = self.chat(system, prompt, model=model, temperature=temperature,
                             max_tokens=max_tokens)
            parsed = _extract_json(text)
            if isinstance(parsed, dict):
                return parsed
            last_error = parsed if isinstance(parsed, Exception) else \
                NimInvalidJSON("model output was not a JSON object")
        raise last_error or NimInvalidJSON("unparseable model output")


def _extract_json(text: str):
    """Extract the first JSON object from model text (fallback parser)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass
    # fallback: first {...} block
    depth = 0
    start = -1
    for i, ch in enumerate(cleaned):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(cleaned[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    start = -1
    return NimInvalidJSON("no JSON object found in model output")
