"""Quality engine (spec section 25) - the decision is COMPUTED IN CODE.

    LLM evaluations -> structured evidence -> code-based policy -> PASS/FAIL/REVISE

No single LLM score can pass a post. Hard gates (platform_fit, antislop, fact)
FAIL regardless of scores. Unresolved unverified facts never reach review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.config import get_config
from src.critics.critics import ALL_CRITICS, CriticResult


@dataclass
class QualityDecision:
    decision: str                 # PASS | REVISE | FAIL
    overall_score: int
    critic_results: list[CriticResult] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    recommended_changes: list[str] = field(default_factory=list)


class QualityEngine:
    def __init__(self, thresholds: dict | None = None):
        cfg = get_config().quality
        self.thresholds = thresholds or cfg.get("thresholds", {})
        hard_gates = self.thresholds.get(
            "hard_gates", ["platform_fit", "antislop", "fact", "post_shape"])
        self.hard_gates = set(hard_gates)

    def run_critic(self, critic, post: dict, version: dict, context: dict) -> CriticResult:
        try:
            return critic.evaluate(post, version, context)
        except Exception as exc:  # a broken critic must not pass content
            return CriticResult(critic.name, False, 0,
                                [f"critic error: {type(exc).__name__}"], [],
                                ["fix critic infrastructure"], engine="deterministic")

    def evaluate(self, post: dict, version: dict, context: dict) -> QualityDecision:
        results = [self.run_critic(c, post, version, context) for c in ALL_CRITICS]
        issues: list[str] = []
        changes: list[str] = []

        for r in results:
            issues.extend(f"{r.critic}: {i}" for i in r.issues if not i.startswith("WARN"))
            changes.extend(r.recommended_changes)

        # Hard gates: any failed gate -> FAIL (never scored around).
        for r in results:
            if r.critic in self.hard_gates and not r.passed:
                return QualityDecision("FAIL", self._composite(results), results,
                                       issues, changes)

        min_critic = int(self.thresholds.get("min_critic_score", 70))
        weak = [r for r in results if r.score < min_critic]
        overall = self._composite(results)
        min_overall = int(self.thresholds.get("min_overall_score", 75))

        if weak:
            # weak non-gate critics -> REVISE (bounded by revision cycles config)
            return QualityDecision("REVISE", overall, results,
                                   issues + [f"{r.critic} below {min_critic}" for r in weak],
                                   changes)
        if overall < min_overall:
            return QualityDecision("REVISE", overall, results,
                                   issues + [f"overall {overall} < {min_overall}"], changes)
        return QualityDecision("PASS", overall, results, issues, changes)

    @staticmethod
    def _composite(results: list[CriticResult]) -> int:
        """Weighted composite computed in code - never an LLM-asserted score."""
        weights = {"platform_fit": 1.2, "fact": 1.2, "antislop": 1.2,
                   "originality": 1.1, "voice": 1.0, "post_shape": 1.0,
                   "hook": 0.8, "coherence": 0.8}
        num = sum(r.score * weights.get(r.critic, 1.0) for r in results)
        den = sum(weights.get(r.critic, 1.0) for r in results)
        return int(round(num / den)) if den else 0


def decide_with_revision_budget(decisions: list[QualityDecision],
                                max_cycles: int | None = None) -> QualityDecision:
    """After max_revision_cycles, send candidates to review with honest scores
    (spec: max_revision_cycles in config; never infinite loops)."""
    if max_cycles is None:
        max_cycles = int(get_config().quality.get("thresholds", {}).get(
            "max_revision_cycles", 2))
    last = decisions[-1]
    if last.decision == "FAIL" or len(decisions) > max_cycles:
        return QualityDecision(
            "REVISE" if last.decision != "FAIL" else "FAIL",
            last.overall_score, last.critic_results,
            last.issues + ([f"revision budget of {max_cycles} exhausted"] if
                           len(decisions) > max_cycles else []),
            last.recommended_changes)
    return last
