"""Review-command parsing and authorization (spec sections 33-34, 51).

Only exact commands are accepted:
    /approve
    /reject <optional reason>
    /iterate <instruction>

Security:
- only authorized GitHub users may execute anything
- comment text is NEVER interpreted as shell or code
- iterate instruction is bounded and stripped of control characters
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

APPROVE_RE = re.compile(r"^\s*/approve\s*$", re.IGNORECASE)
REJECT_RE = re.compile(r"^\s*/reject\b(.*)$", re.IGNORECASE | re.DOTALL)
ITERATE_RE = re.compile(r"^\s*/iterate\b(.*)$", re.IGNORECASE | re.DOTALL)

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class ParsedCommand:
    name: str
    payload: str = ""
    valid: bool = True
    reject_reason: str | None = None
    metadata: dict = field(default_factory=dict)


class CommandSecurityError(Exception):
    pass


def is_authorized(actor: str, authorized_users: list[str]) -> bool:
    if not actor:
        return False
    return actor.lstrip("@").lower() in {u.lstrip("@").lower() for u in authorized_users}


def parse_command(comment_text: str, actor: str, authorized_users: list[str],
                  max_iterate_chars: int = 500) -> ParsedCommand:
    """Parse a comment into a command. Comment content is treated as untrusted DATA.

    A comment body may contain at most one command; the FIRST line starting with
    a supported slash-command is used; everything else is ignored deliberately.
    """
    if not comment_text or not comment_text.strip():
        return ParsedCommand("unknown", valid=False, reject_reason="empty comment")

    if not is_authorized(actor, authorized_users):
        return ParsedCommand(
            "unknown", valid=False,
            reject_reason=f"actor '{actor}' is not an authorized reviewer")

    first_line = comment_text.strip().splitlines()[0]

    if APPROVE_RE.match(first_line):
        return ParsedCommand("approve")

    m = REJECT_RE.match(first_line)
    if m:
        reason = CONTROL_CHARS.sub("", m.group(1) or "").strip()[:300]
        return ParsedCommand("reject", payload=reason)

    m = ITERATE_RE.match(first_line)
    if m:
        instruction = CONTROL_CHARS.sub("", m.group(1) or "").strip()
        instruction = re.sub(r"```", "", instruction)  # no fenced payloads
        if not instruction:
            return ParsedCommand("iterate", valid=False,
                                 reject_reason="iterate requires an instruction")
        if len(instruction) > max_iterate_chars:
            return ParsedCommand(
                "iterate", valid=False,
                reject_reason=f"instruction exceeds {max_iterate_chars} chars")
        return ParsedCommand("iterate", payload=instruction)

    return ParsedCommand("unknown", valid=False,
                         reject_reason="not a supported command")
