"""Anti-slop v4 families - deterministic detectors researched from 18
open-source post-generation projects (blader/humanizer tells,
avoid-ai-writing patterns, brandonwise/humanizer burstiness gate,
arvindrk/twitter-agent lexicon). Every family is individually toggleable in
config/quality.yml and must flag exactly its own pattern, not good prose.

Also covers the hook critic's staged-run-up rule and the burstiness
calibration on the shipped exemplar bank.
"""
import pytest

from src.critics.antislop import (
    AntiSlopEngine, uniform_rhythm_issue, sentences, _WORD_RE)
from src.config import get_config


def _rules(**overrides) -> dict:
    rules = dict(get_config().quality.get("anti_slop", {}))
    rules.update(overrides)
    return rules


def _check(text: str, **rule_overrides):
    return AntiSlopEngine(_rules(**rule_overrides)).check_text(text)


GOOD_POST = (
    "Same eval, temperature 0, three runs. Three different outputs.\n\n"
    "\"Generally deterministic\" is doing heavy lifting in that doc line.\n\n"
    "We pinned the model, cached the seed, graded with substring asserts. "
    "CI went from 90 minutes to 50.")


# ---------------------------------------------------------------- good prose
def test_good_post_passes_all_v4_families():
    r = _check(GOOD_POST)
    assert r.passed, r.issues


def test_every_family_can_be_disabled():
    slop = ("Let me tell you why this matters: experts say it's not the "
            "price. It's not the features. It's the trust. In summary, "
            "thrilled to share that it serves as a game-changer, "
            "showcasing real value. Thoughts?\n\n"
            "That's the real win.")
    r = _check(slop)
    assert not r.passed and len(r.issues) >= 6
    # with every v4 family off, none of the new families fire
    r2 = _check(slop, ban_staged_runup=False, ban_summary_closers=False,
                ban_countdown_negation=False, ban_borrowed_authority=False,
                ban_chatbot_residue=False, ban_x_lexicon=False,
                ban_ing_riders=False, ban_copula_avoidance=False,
                ban_uniform_rhythm=False)
    v4_marks = ("staged run-up", "summary closer", "countdown", "authority",
                "chatbot", "lexicon", "rider", "copula", "rhythm")
    assert not any(any(m in i for m in v4_marks) for i in r2.issues), r2.issues


# ------------------------------------------------------- staged run-up
@pytest.mark.parametrize("opener", [
    "Let me tell you something about evals.",
    "Here's the thing: latency is a product feature.",
    "Honestly, nobody reads the migration notes.",
    "Real talk: dashboards lie.",
])
def test_staged_runup_flagged(opener):
    body = opener + "\n\nWe cut p95 latency 40% by caching tool outputs " \
        "for 60 seconds. Postgres 16 got the write path for free."
    r = _check(body)
    assert not r.passed
    assert any("staged run-up" in i for i in r.issues), r.issues


# ------------------------------------------------------ summary closers
@pytest.mark.parametrize("closer", [
    "That's the real win.",
    "No hype. Just shipping.",
    "That's it. That's the post.",
    "Period.",
    "Full stop.",
])
def test_summary_closers_flagged(closer):
    body = ("Postgres 16 cut our p95 write latency 40% with one index "
            "change.\n\nThe migration took 11 minutes.\n\n" + closer)
    r = _check(body)
    assert not r.passed
    assert any("summary closer" in i for i in r.issues), r.issues


def test_summary_closer_not_flagged_midpost():
    """A sentence containing 'that's the point' inside a real argument block
    is prose, not a closer; the detector only fires on the final block."""
    body = ("The benchmark looked clean until the second run.\n\n"
            "That's the point where the cache warms. Run three finally "
            "showed the real number: 12ms, not 4ms.")
    r = _check(body)
    assert r.passed, r.issues


# --------------------------------------------------- countdown negation
def test_countdown_negation_flagged():
    body = ("Everyone debates agent pricing.\n\n"
            "It's not the tokens. It's not the seats. It's the retry "
            "budget that nobody sets.\n\n"
            "One team capped retries at 3 and their bill halved.")
    r = _check(body)
    assert not r.passed
    assert any("countdown" in i for i in r.issues), r.issues


# --------------------------------------------------- borrowed authority
@pytest.mark.parametrize("phrase", [
    "Experts say the market will double.",
    "Studies show that caching fixes latency.",
    "Industry leaders agree on one thing.",
    "Everyone knows benchmarks are rigged.",
])
def test_borrowed_authority_flagged(phrase):
    body = ("Postgres partitioning looks scary.\n\n" + phrase + "\n\n"
            "We partitioned 400GB in one afternoon.")
    r = _check(body)
    assert not r.passed
    assert any("authority" in i for i in r.issues), r.issues


# ----------------------------------------------------- chatbot residue
@pytest.mark.parametrize("phrase", [
    "Great question!",
    "I hope this helps.",
    "In summary, the numbers hold.",
    "To summarize: cache it.",
])
def test_chatbot_residue_flagged(phrase):
    body = ("We cached the eval results for 60 seconds.\n\n" + phrase +
            "\n\nLoad dropped to near zero.")
    r = _check(body)
    assert not r.passed
    assert any("chatbot" in i for i in r.issues), r.issues


# ----------------------------------------------------------- x-lexicon
@pytest.mark.parametrize("phrase", [
    "Thrilled to share what we shipped.",
    "Excited to announce our Series A.",
    "Let that sink in.",
    "Read that again.",
    "Nobody is talking about this bug.",
])
def test_x_lexicon_flagged(phrase):
    body = ("The migration finished at 3am.\n\n" + phrase + "\n\n"
            "Downtime was 40 seconds.")
    r = _check(body)
    assert not r.passed
    assert any("lexicon" in i for i in r.issues), r.issues


def test_thoughts_ending_flagged():
    body = ("We cut CI from 90 to 50 minutes by caching seeds.\n\n"
            "What are you doing about eval time? Thoughts?")
    r = _check(body)
    assert not r.passed
    assert any("lexicon" in i for i in r.issues), r.issues


# --------------------------------------------------------- -ing riders
def test_ing_rider_flagged():
    body = ("The team shipped the migration on Friday, showcasing real "
            "momentum across the org.\n\n"
            "Downtime was 40 seconds.")
    r = _check(body)
    assert not r.passed
    assert any("rider" in i for i in r.issues), r.issues


# ---------------------------------------------------- copula avoidance
def test_copula_avoidance_flagged():
    body = ("The retry budget serves as the real cost lever in agent "
            "systems.\n\nMost teams never set it. Ours halved the bill.")
    r = _check(body)
    assert not r.passed
    assert any("copula" in i for i in r.issues), r.issues


# ---------------------------------------------------------- burstiness
def test_uniform_rhythm_flagged():
    text = ("The team reviewed the full deployment process this quarter.\n"
            "They found several gaps in the current monitoring setup.\n"
            "The report suggests improvements across all core services.\n"
            "Leadership approved the plan for the next fiscal year.\n"
            "Engineers will begin the migration work next month.")
    issue = uniform_rhythm_issue(text)
    assert issue and "uniform sentence rhythm" in issue


def test_bursty_human_post_passes():
    assert uniform_rhythm_issue(GOOD_POST) is None


def test_short_posts_skip_rhythm_check():
    assert uniform_rhythm_issue("One. Two words here. Three more now.") is None


def test_all_shipped_exemplars_pass_v4_engine():
    """Regression: the exemplars teach style; none may trip the detectors
    (or every generated post imitating them would fail)."""
    cfg = get_config().voice.get("voice", {})
    exemplars = cfg.get("exemplars") or []
    assert len(exemplars) >= 8
    rules = dict(get_config().quality.get("anti_slop", {}))
    for ex in exemplars:
        r = AntiSlopEngine(rules).check_text(str(ex))
        assert r.passed, (str(ex)[:60], r.issues)


# ------------------------------------------------- hook critic run-up
def test_hook_critic_flags_staged_runup():
    from src.critics.critics import HookCritic
    version = {"body": "Honestly, let me tell you something.\n\nReal "
                       "numbers: we cut CI 90 to 50 minutes with seed "
                       "caching in Postgres 16."}
    result = HookCritic().evaluate({"platform": "x"}, version, {})
    assert not result.passed
    assert any("staged run-up" in i for i in result.issues)


def test_hook_critic_passes_clean_hook():
    from src.critics.critics import HookCritic
    version = {"body": "CI went from 90 minutes to 50 with one change.\n\n"
                       "We cached the seeds in Postgres 16 and graded with "
                       "substring asserts. Eval time is now a non-issue."}
    result = HookCritic().evaluate({"platform": "x"}, version, {})
    assert result.passed, result.issues


# ------------------------------------------- sentence-initial anchor fix
def test_sentence_initial_possessive_proper_noun_counts_as_anchor():
    """Live-run finding: 'SQLite's refusal...' scored 0 anchors because the
    gate skipped sentence-initial words entirely; acronyms and possessive
    proper nouns must count (same rule as concrete_anchor_in_sentence)."""
    from src.critics.antislop import concrete_anchors
    assert concrete_anchors("SQLite's refusal to ship replication is a "
                            "feature decision.") != []
    assert concrete_anchors("NASA quietly funded the study.") != []
    # bare capitalized first words are still not anchors
    assert concrete_anchors("Every framework demos thirty tools.") == []
    # and the full post that failed live now passes the gate
    body = ("SQLite's refusal to build replication is a feature decision, "
            "not neglect - durability contracts")
    r = _check(body)
    assert r.passed, r.issues


# ------------------------------------- live-run 34333991666 regressions
def test_canned_punch_echo_at_four_words_is_caught():
    """'Default is the bug.' echoed the exemplar punch 'The default is the
    bug.' with a 4-word run and dodged the old threshold of 5."""
    body = ("El Nino is now stronger than at any point in the last 1,000 "
            "years.\n\nJulie Cole at Michigan reconstructed a thousand-year "
            "record from Galapagos corals.\n\nDefault is the bug.")
    exemplars = get_config().voice.get("voice", {}).get("exemplars") or []
    r = _check(body, exemplars=exemplars)  # AntiSlopCritic injects these
    assert not r.passed
    assert any("punch" in i or "closer" in i for i in r.issues), r.issues


def test_shaper_drops_four_word_punch_echo():
    from src.generation.shaper import normalize_post_shape
    exemplars = ["X.\n\nThe default is the bug."]
    shaped = normalize_post_shape(
        "El Nino broke a thousand-year coral record.\n\nThe eastern Pacific "
        "has never seen this.\n\nDefault is the bug.", 270, exemplars)
    assert "Default is the bug" not in shaped
    assert "coral record" in shaped  # the real content survives
