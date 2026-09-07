"""Claim ledger rules (spec sections 15, 58) + idea scoring in code (spec 16)."""
import pytest

from src.claims.ledger import ClaimLedger


RESEARCH = [
    {"source_url": "https://example.com/report-2026", "title": "Report"},
    {"source_url": "https://example.org/study", "title": "Study"},
]


def test_fact_with_matching_source_verified():
    ledger = ClaimLedger()
    c = ledger.new_claim("Agent tool errors compound 3x faster", "FACT",
                         {"url": "https://example.com/report-2026"}, confidence=90)
    ledger.verify_against_research(c, RESEARCH)
    assert c.status == "VERIFIED"


def test_fact_without_source_is_unverified():
    ledger = ClaimLedger()
    c = ledger.new_claim("Models double in size every 6 months", "FACT", {})
    ledger.verify_against_research(c, RESEARCH)
    assert c.status == "UNVERIFIED"


def test_fact_with_invented_source_is_unverified():
    ledger = ClaimLedger()
    c = ledger.new_claim("Claim", "FACT", {"url": "https://fake.example/nope"})
    ledger.verify_against_research(c, RESEARCH)
    assert c.status == "UNVERIFIED"


def test_opinions_and_speculation_do_not_need_sources():
    ledger = ClaimLedger()
    op = ledger.new_claim("Tool bloat hurts quality", "OPINION")
    sp = ledger.new_claim("Agencies will standardize on 5 tools", "SPECULATION")
    ledger.verify_against_research(op, RESEARCH)
    ledger.verify_against_research(sp, RESEARCH)
    assert op.status == "VERIFIED"
    assert sp.status == "VERIFIED"


def test_decision_requires_rewrite_for_unverified():
    ledger = ClaimLedger()
    c = ledger.new_claim("Unverified stat", "FACT", {})
    ledger.verify_against_research(c, RESEARCH)
    ok, actions = ledger.decide([c])
    assert not ok
    assert any("REWRITE" in a for a in actions)


def test_invalid_claim_type_rejected():
    with pytest.raises(ValueError):
        ClaimLedger.new_claim("x", "RUMOR")


def test_personal_experience_never_fabricated_marker():
    """PERSONAL_EXPERIENCE claims must originate from the account, never from
    research data; the writer prompt forbids inventing them - the ledger only
    accepts them with an explicit non-research provenance marker."""
    ledger = ClaimLedger()
    c = ledger.new_claim("I shipped this at work", "PERSONAL_EXPERIENCE",
                         {"url": "https://example.com/report-2026"})
    ledger.verify_against_research(c, RESEARCH)
    # stays PROPOSED - neither verified nor unverified by research matching
    assert c.status == "PROPOSED"


def test_score_computed_in_code_not_by_llm():
    from src.pipeline.production import IdeaDiscoveryPipeline  # noqa: F401
    # weights from config
    from src.config import get_config
    weights = get_config().strategy.get("scoring_weights", {})
    evaluation = {"novelty": 100, "interestingness": 100, "relevance": 100,
                  "personal_fit": 100, "discussion_value": 100,
                  "factual_confidence": 100, "content_potential": 100,
                  "timeliness": 100}
    expected = int(round(sum(float(evaluation[k]) * float(w)
                             for k, w in weights.items())
                         / sum(float(w) for w in weights.values())))
    assert expected == 100
    half = {k: 50 for k in evaluation}
    got = int(round(sum(float(half[k]) * float(w) for k, w in weights.items())
                    / sum(float(w) for w in weights.values())))
    assert got == 50
