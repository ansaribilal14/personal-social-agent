"""Prompt loader + version registry (spec section 61).

Every important prompt has a version; the version used is stored against each
generated content version. Files: prompts/library.yml (name -> text with
`name: vX:` keys) plus critic prompts defined inline in src/critics/.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

# Registry: prompt name -> version string. Keep in sync with library.yml.
PROMPT_VERSIONS = {
    "researcher": "v2",
    "strategist": "v4",
    "writer": "v6",
    "iterator": "v4",
    "analyst": "v1",
    "critic_originality": "v2",
    "critic_voice": "v2",
    "critic_slop": "v2",
    "critic_platform": "v1",
    "critic_hook": "v1",
}


@lru_cache(maxsize=1)
def _library() -> dict[str, str]:
    with (PROMPTS_DIR / "library.yml").open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return {str(key): str(text) for key, text in raw.items()}


def prompt(name: str) -> str:
    """Look up the prompt EXPLICITLY by its registered version (PROMPT_VERSIONS),
    never by "whichever _vN key happens to appear last in the YAML file" -
    that used to silently pick the wrong prompt if the file was reordered or a
    new draft version was inserted mid-file instead of appended at the end."""
    lib = _library()
    key = f"{name}_{version_of(name)}"
    if key not in lib:
        raise KeyError(f"unknown prompt '{key}' (from name='{name}')")
    return lib[key]


def version_of(name: str) -> str:
    base = name.rsplit("_v", 1)[0]
    return PROMPT_VERSIONS.get(base, "v0")


def versions_used(*names: str) -> dict[str, str]:
    return {n: version_of(n) for n in names}


def render(name: str, **kwargs) -> str:
    text = prompt(name)
    for key, value in kwargs.items():
        text = text.replace("{" + key + "}", str(value))
    return text
