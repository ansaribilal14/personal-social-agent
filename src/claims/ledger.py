"""Claim ledger (spec sections 15, 58).

Rules enforced:
- FACT claims require a source URL from research; without one -> UNVERIFIED
  -> policy says REWRITE OR REMOVE (never confident-sounding compensation)
- opinions stay opinions; inference never presented as fact; speculation
  labeled; personal experiences never fabricated
- provenance: claim -> research_item url -> post_version snapshot
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

CLAIM_TYPES = ("FACT", "OPINION", "INFERENCE", "SPECULATION", "PERSONAL_EXPERIENCE")


@dataclass
class Claim:
    claim_id: str
    text: str
    claim_type: str
    source: dict = field(default_factory=dict)   # {url, title} | {}
    evidence: str = ""
    confidence: int = 0
    status: str = "PROPOSED"                     # PROPOSED|VERIFIED|UNVERIFIED|REWRITTEN|REMOVED

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id, "text": self.text,
            "claim_type": self.claim_type, "source": self.source,
            "evidence": self.evidence, "confidence": self.confidence,
            "status": self.status,
        }


class ClaimLedger:
    def __init__(self, unverified_policy: str | None = None,
                 fact_requires_source: bool | None = None):
        if unverified_policy is None or fact_requires_source is None:
            from src.config import get_config
            cfg = get_config().quality.get("claims", {})
            unverified_policy = unverified_policy or cfg.get("unverified_policy", "rewrite")
            fact_requires_source = fact_requires_source if fact_requires_source is not None \
                else cfg.get("fact_requires_source", True)
        self.unverified_policy = unverified_policy
        self.fact_requires_source = fact_requires_source

    @staticmethod
    def new_claim(text: str, claim_type: str, source: dict | None = None,
                  evidence: str = "", confidence: int = 50) -> Claim:
        if claim_type not in CLAIM_TYPES:
            raise ValueError(f"invalid claim type {claim_type}")
        if not text or not text.strip():
            raise ValueError("claim text empty")
        return Claim(
            claim_id=f"CLM-{uuid.uuid4().hex[:10]}",
            text=text.strip(),
            claim_type=claim_type,
            source=source or {},
            evidence=evidence,
            confidence=max(0, min(100, int(confidence))),
        )

    def verify_against_research(self, claim: Claim,
                                research_items: list[dict]) -> Claim:
        """FACTs require a source matching a research item URL (spec 15).

        Never invents metadata: only URLs that actually appear in stored
        research count as verification.
        """
        if claim.claim_type != "FACT":
            # Opinions/inferences/speculation don't need factual verification,
            # but they must not be mislabeled facts either.
            claim.status = "VERIFIED" if claim.claim_type in ("OPINION", "SPECULATION") \
                else claim.status
            return claim

        known_urls = {i.get("source_url") for i in research_items if i.get("source_url")}
        url = (claim.source or {}).get("url")
        if self.fact_requires_source and (not url or url not in known_urls):
            claim.status = "UNVERIFIED"
            claim.evidence = claim.evidence or "no matching research source URL"
            return claim
        claim.status = "VERIFIED"
        return claim

    def unverified_facts(self, claims: list[Claim]) -> list[Claim]:
        return [c for c in claims if c.claim_type == "FACT" and c.status == "UNVERIFIED"]

    def decide(self, claims: list[Claim]) -> tuple[bool, list[str]]:
        """Returns (all_ok, required_actions[]). Actions use the configured
        unverified_policy: rewrite | remove | block."""
        actions: list[str] = []
        for c in self.unverified_facts(claims):
            actions.append(f"{self.unverified_policy.upper()}: {c.claim_id} ({c.text[:60]})")
        return (not actions, actions)


def build_claims_snapshot(claims: list[Claim]) -> list[dict]:
    return [c.to_dict() for c in claims]
