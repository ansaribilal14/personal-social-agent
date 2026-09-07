"""Secret redaction for logs (spec sections 50, 59)."""
from __future__ import annotations

import re

_BUILTIN_KEYS = ("authorization", "token", "secret", "password", "api_key", "apikey",
                 "webhook", "jwt", "bearer")
_TOKENISH = re.compile(r"\b(ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|"
                       r"nvapi-[A-Za-z0-9_\-]{20,}|"
                       r"[A-Za-z0-9_\-]{30,})\b")


def _load_redact_keys() -> tuple[str, ...]:
    try:
        from src.config import get_config
        keys = get_config().security.get("logging", {}).get("redact_keys", [])
        return tuple(k.lower() for k in keys) or _BUILTIN_KEYS
    except Exception:
        return _BUILTIN_KEYS


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return value
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * max(len(value) - keep - 3, 3) + value[-3:]


def redact_text(text: str) -> str:
    """Mask token-shaped strings embedded in free text."""
    if not text:
        return text
    return _TOKENISH.sub(lambda m: mask(m.group(0)), str(text))


def redact_payload(payload) -> dict:
    """Recursively redact sensitive keys from dict/list payloads."""
    keys = _load_redact_keys()

    def _walk(obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if any(kk in str(k).lower() for kk in keys):
                    out[k] = mask(str(v)) if v else v
                else:
                    out[k] = _walk(v)
            return out
        if isinstance(obj, list):
            return [_walk(x) for x in obj]
        if isinstance(obj, str):
            return redact_text(obj)
        return obj

    if isinstance(payload, dict):
        return _walk(payload)
    if isinstance(payload, str):
        return redact_text(payload)
    return payload
