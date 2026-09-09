"""Anti-AI-slop engine (spec section 23) - first-class, layered.

Layer 1 (deterministic): banned phrases, emoji/em-dash/exclamation caps,
engagement bait, fake urgency, hashtag caps, meta-labels ("As an opinion:"),
"not X, it's Y" constructions, press-release cliches, abstract-noun soup,
and a concrete-anchor requirement (a post must contain at least one number,
quote, or named specific) - from config/quality.yml.
Layer 2 (NIM, in critics): "does this sound like a real person with a reason
to post?" and "does it carry an actual observation/opinion/insight?"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F02F"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF]")

ENGAGEMENT_BAIT = [
    r"\brt if\b", r"\blike if\b", r"\bfollow (me|for)\b", r"\bcomment below\b",
    r"\btag someone\b", r"\bshare this\b", r"\bsmash that\b", r"\blink in bio\b",
]
FAKE_URGENCY = [
    r"\bbefore it'?s too late\b", r"\byou won'?t believe\b", r"\bact now\b",
    r"\bhurry\b", r"\blast chance\b", r"\bthis changes everything\b",
    r"\bshocking\b", r"\bgoing viral\b",
]
GENERIC_OPENERS = [
    r"^in today'?s (fast-paced |modern )?world",
    r"^we'?ve all been there",
    r"^let'?s (talk about|dive into|deep dive)",
    r"^hot take:",
    r"^as an ai\b",
]
# Meta-labels: the writer announcing what it is about to do instead of doing it.
META_LABELS = [
    r"\bas an (opinion|aside|observation|aside)\b",
    r"^(hot take|my take|real talk|unpopular opinion|serious question)\s*:",
    r"\bhere'?s (the thing|my take|why this matters)\s*:",
    r"\bopinion\s*:",
    r"\ba (thread|thought|observation)\s*:",
    r"^(hook|punch|setup|context|point|takeaway)\s*:",   # labeling the post's parts
]
# "not X, it's Y" / "isn't a luxury - they're the critical path" constructions.
NOT_X_ITS_Y = [
    r"\bnot (just |only |merely )?[\w' \-]{2,40},?\s*(it'?s|it is|they'?re|they are)\b",
    r"\b(aren'?t|isn'?t) (just |only |merely )?(a|an|the) [\w' \-]{2,40}[—–,]?\s*"
    r"(it'?s|it is|they'?re|they are)\b",
    r"\bless about [\w' \-]{2,40},? more about\b",
]
# Press-release cadence: phrases that mark corporate/PR prose, not a person.
PRESS_RELEASE = [
    r"\bopens? (up )?(a )?new design space\b",
    r"\bmoves? the field (from|forward|beyond)\b",
    r"\bthe real (value|story|question|issue|action) (lies|is|begins)\b",
    r"\ba new era of\b",
    r"\bat the intersection of\b",
    r"\bpaves? the way (for|toward)\b",
    r"\bbridges? the gap between\b",
    r"\bunleash(es|ing)?\b",
    r"\bsupercharg(es|ing|e)\b",
    r"\bharness(es|ing)? the power\b",
    r"\bin the realm of\b",
    r"\braises? important questions\b",
    r"\ba testament to\b",
    r"\binders? the importance of\b",
    r"\bunderscores? the (importance|need)\b",
    r"\bsparks? (an? )?(important )?(conversation|debate)\b",
    r"\bthe future of [\w ]+ is (here|now)\b",
    r"\bunlock(s|ing)? (the |a |new )?(potential|power|value|future)\b",
    r"\b(heralds?|ushers? in) a new\b",
    r"\bpoised to (transform|revolutionize|disrupt)\b",
]
# Staged run-up: warming up the audience instead of starting the post.
# (blader/humanizer tells; "Let me tell you" / "Here's the thing:" patterns)
STAGED_RUNUP = [
    r"^(let me tell you|let me be (clear|honest)|here'?s (the thing|why this|the deal)\s*[:.]?)",
    r"^(honestly|frankly|seriously|look)\s*[,:.]",
    r"^(okay|alright|so)\s*,?\s*(so|here|listen|real talk)\b",
    r"^(real talk|truth be told|let'?s be honest)\s*[:.,]",
]
# One-line summary closers: a self-satisfied final line that points back at
# the post instead of making a point ("That's the real win.").
SUMMARY_CLOSERS = [
    r"^that'?s (it|all)\.?\s*$",
    r"^that'?s the (real )?(win|point|story|difference|whole game)\.?\s*$",
    r"^that'?s it\.? that'?s the (post|tweet|whole post)\.?\s*$",
    r"^no hype[.,]? ?(just|only|pure) (shipping|truth|facts|execution)\.?\s*$",
    r"^(period|full stop)\.?\s*$",
    r"^that'?s the (whole )?(game|post)\.?\s*$",
]
# Countdown negation: "It's not the price. It's not the features. It's the
# trust." - two or more consecutive 'it's not' sentences before the reveal.
COUNTDOWN_NEGATION = [
    r"\bit'?s not\b[^.!?]{2,80}[.!?]\s*(and )?it'?s not\b",
    r"\bnot (the|a|about)\b[^.!?]{2,60}[.!?]\s*(it'?s|it is|this is) not\b",
]
# Borrowed authority: appeal to unnamed consensus instead of a real source.
BORROWED_AUTHORITY = [
    r"\bexperts (believe|say|agree|warn|predict)\b",
    r"\bindustry (leaders|insiders) (agree|say|believe)\b",
    r"\bstudies (show|have shown|suggest)( that)?\b",
    r"\bpeople (are saying|have been saying)\b",
    r"\beveryone knows\b",
]
# Chatbot residue: assistant-isms that leak into generated posts.
CHATBOT_RESIDUE = [
    r"\bgreat question\b",
    r"\bi hope this helps\b",
    r"\bhere'?s what you need to know\b",
    r"\bin (summary|conclusion|a nutshell)\b",
    r"\bto summarize\b",
    r"\bcertainly\s*!",
]
# X-lexicon: engagement-cliche phrases common on x that mark generated posts.
X_LEXICON = [
    r"\bthrilled to (share|announce)\b",
    r"\bexcited to (share|announce)\b",
    r"\blet that sink in\b",
    r"\bread that (again|one more time)\b",
    r"\bnobody (is|'s) (talking|posting) about (this|it)\b",
    r"\bthoughts\?\s*$",
    r"\bgame[ -]?chang(er|ing)\b",
    r"\bworth a look\b",
]
# -ing riders: a comma + trailing participle doing fake-work at sentence end.
ING_RIDERS = [
    r",\s+(showcasing|symbolizing|highlighting|emphasizing|demonstrating|"
    r"reflecting|signaling|paving|underscoring|elevating|empowering)\b",
]
# Copula avoidance: Latinate verbs where plain "is/has" is stronger.
COPULA_AVOIDANCE = [
    r"\bserves? as\b",
    r"\bstands? as a testament\b",
]
# Abstract-noun soup: sentences built from these instead of specifics.
ABSTRACT_NOUNS = [
    "landscape", "ecosystem", "paradigm", "realm", "sphere", "leverage",
    "synergy", "journey", "narrative", "conversation", "tapestry", "era",
    "palette", "afterlife", "critical path", "design space", "value chain",
    "framework", "toolkit", "north star", "moonshot", "inflection point",
    "asset", "monetization", "digital exhaust",
]

# Sentence must be long enough to carry meaning before we judge it.
_MIN_SENTENCE_WORDS = 4

# Quoted phrases: double quotes, or single quotes NOT mid-word (so
# "Landauer's principle ... today's" never parses as a quote).
_QUOTE_RE = re.compile(
    r"\"([^\"]{3,60})\"|(?<![\w'\u2019])'([^']{3,60})'(?![\w'\u2019])")
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


@dataclass
class SlopReport:
    passed: bool
    issues: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.!?\n]", text or "") if s.strip()]


def concrete_anchors(text: str) -> list[str]:
    """Concrete specifics a reader can grab: numbers, quotes, named things.

    An anchor is any of:
    - a digit (measurement, year, version, count, percentage)
    - a quoted phrase (a real citation or a named concept in quotes)
    - a capitalized token NOT at sentence start and NOT a stopword/pronoun
      (heuristic proper noun: a company, product, paper, person, place)

    Returns the matched anchor strings (deduplicated, order preserved).
    """
    anchors: list[str] = []
    for m in re.finditer(r"\d[\d.,%+]*", text or ""):
        anchors.append(m.group(0))
    for m in _QUOTE_RE.finditer(text or ""):
        q = (m.group(1) or m.group(2) or "").strip()
        if q:
            anchors.append(f'"{q}"')
    for s in sentences(text or ""):
        words = _WORD_RE.findall(s)
        for idx, w in enumerate(words):
            if not w[0].isupper() or len(w) < 3:
                continue
            if w.lower() in _NON_PROPER:
                continue
            # Sentence start: only acronyms (NASA) and possessive proper
            # nouns ("SQLite's", "Hubble's") count - a bare capitalized
            # first word is usually just capitalization (live-run finding:
            # "SQLite's refusal..." wrongly scored 0 anchors otherwise).
            if idx == 0 and not (w.isupper() or w.rstrip("s").endswith("'")):
                continue
            anchors.append(w)
    seen: set[str] = set()
    out = []
    for a in anchors:
        k = a.lower()
        if k not in seen:
            seen.add(k)
            out.append(a)
    return out


_NON_PROPER = {
    "the", "a", "an", "and", "but", "or", "so", "if", "it", "its", "it's",
    "this", "that", "these", "those", "there", "here", "then", "when", "while",
    "because", "which", "who", "what", "why", "how", "not", "no", "yes",
    "i", "i'm", "i've", "i'd", "i'll", "you", "your", "we", "our", "they",
    "their", "he", "she", "his", "her", "in", "on", "at", "to", "for", "of",
    "with", "from", "by", "as", "is", "are", "was", "were", "be", "been",
    "being", "do", "does", "did", "don't", "doesn't", "didn't", "can", "can't",
    "cannot", "could", "would", "should", "will", "won't", "shall", "may",
    "might", "must", "have", "has", "had", "haven't", "hasn't", "hadn't",
    "my", "me", "mine", "them", "us", "him", "than", "too", "very", "just",
    "also", "even", "still", "only", "about", "after", "before", "between",
    "through", "under", "over", "again", "once", "during", "without", "into",
    "most", "some", "such", "own", "same", "other", "another", "each", "few",
    "more", "both", "all", "any", "every", "one", "two", "new", "old", "now",
    "today", "yesterday", "tomorrow", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "january", "february",
    "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december",
}


def _abstract_soup_sentence(sentence: str) -> bool:
    """A sentence built from >=2 abstract nouns with no concrete anchor in it."""
    low = sentence.lower()
    hits = sum(1 for n in ABSTRACT_NOUNS if n in low)
    if hits < 2:
        return False
    if re.search(r"\d", sentence):
        return False
    if _QUOTE_RE.search(sentence):
        return False
    # a proper-noun anchor rescues the sentence
    words = _WORD_RE.findall(sentence)
    for idx, w in enumerate(words):
        if idx > 0 and w[0].isupper() and len(w) >= 3 and w.lower() not in _NON_PROPER:
            return False
    return True


def _word_run_overlap(a: str, b: str) -> int:
    """Length of the longest verbatim word run shared by two texts (DP, both
    texts are short: posts and exemplars)."""
    aw = [w.lower() for w in _WORD_RE.findall(a or "")]
    bw = [w.lower() for w in _WORD_RE.findall(b or "")]
    if not aw or not bw:
        return 0
    prev = [0] * (len(bw) + 1)
    best = 0
    for i in range(1, len(aw) + 1):
        cur = [0] * (len(bw) + 1)
        for j in range(1, len(bw) + 1):
            if aw[i - 1] == bw[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


class AntiSlopEngine:
    def __init__(self, rules: dict | None = None):
        if rules is None:
            from src.config import get_config
            rules = get_config().quality.get("anti_slop", {})
        self.rules = rules

    def check_text(self, text: str, allow_personal_experience: bool = True) -> SlopReport:
        issues: list[str] = []
        evidence: list[str] = []
        if not text or not text.strip():
            return SlopReport(False, ["empty text"])

        banned = self.rules.get("banned_phrases", [])
        low = text.lower()
        for phrase in banned:
            if phrase.lower() in low:
                issues.append(f"banned phrase: '{phrase}'")
                evidence.append(phrase)

        emoji_count = len(EMOJI_RE.findall(text))
        if emoji_count > int(self.rules.get("max_emoji_count", 0)):
            issues.append(f"{emoji_count} emojis (max {self.rules.get('max_emoji_count', 0)})")

        em_dashes = text.count("—") + text.count("–")
        if em_dashes > int(self.rules.get("max_em_dash_per_post", 1)):
            issues.append(f"{em_dashes} em dashes (max {self.rules.get('max_em_dash_per_post', 1)})")

        excl = text.count("!")
        if excl > int(self.rules.get("max_exclamation_marks", 0)):
            issues.append(f"{excl} exclamation marks (max {self.rules.get('max_exclamation_marks', 0)})")

        hashtags = text.count("#")
        if hashtags > int(self.rules.get("max_hashtags", 2)):
            issues.append(f"{hashtags} hashtags (max {self.rules.get('max_hashtags', 2)})")

        if self.rules.get("ban_engagement_bait"):
            for pat in ENGAGEMENT_BAIT:
                if re.search(pat, low):
                    issues.append("engagement bait: " + pat)
        if self.rules.get("ban_fake_urgency"):
            for pat in FAKE_URGENCY:
                if re.search(pat, low):
                    issues.append("fake urgency: " + pat)

        for pat in GENERIC_OPENERS:
            if re.search(pat, low):
                issues.append("generic opener pattern: " + pat)

        if self.rules.get("ban_meta_labels", True):
            for pat in META_LABELS:
                if re.search(pat, low, re.MULTILINE):
                    issues.append(f"meta-label instead of writing: {pat}")
                    evidence.append(pat)

        if self.rules.get("ban_not_x_its_y", True):
            for pat in NOT_X_ITS_Y:
                if re.search(pat, low):
                    issues.append("rhetorical 'not X, it's Y' construction")
                    evidence.append(pat)

        if self.rules.get("ban_press_release", True):
            for pat in PRESS_RELEASE:
                if re.search(pat, low):
                    issues.append("press-release cliche: " + pat)
                    evidence.append(pat)

        if self.rules.get("ban_feature_enumeration", True):
            for s in sentences(text):
                if len(_WORD_RE.findall(s)) >= _MIN_SENTENCE_WORDS and \
                        _feature_enumeration_sentence(s):
                    issues.append(
                        "press-release feature enumeration "
                        f"(supports/includes + comma list): '{s[:70]}...'")
                    evidence.append(s[:70])

        if self.rules.get("ban_abstract_soup", True):
            for s in sentences(text):
                if len(_WORD_RE.findall(s)) >= _MIN_SENTENCE_WORDS and \
                        _abstract_soup_sentence(s):
                    issues.append(f"abstract noun soup in: '{s[:70]}...'")
                    evidence.append(s[:70])

        min_anchors = int(self.rules.get("min_concrete_anchors", 1))
        if min_anchors > 0:
            anchors = concrete_anchors(text)
            if len(anchors) < min_anchors:
                issues.append(
                    f"no concrete anchor (number/quote/named specific) - "
                    f"found {len(anchors)}, need {min_anchors}")
                evidence.append("concrete_anchors: " + ", ".join(anchors[:5]))

        # Exemplar overlap: the style exemplars in the writer prompt must never
        # leak into output as near-verbatim text.
        exemplars = self.rules.get("exemplars") or []
        for ex in exemplars:
            run = _word_run_overlap(text, str(ex))
            if run >= int(self.rules.get("max_exemplar_word_run", 8)):
                issues.append(
                    f"copies a style exemplar verbatim ({run}-word run)")
                evidence.append(str(ex)[:80])
                break
        # Stock punch line: the post's LAST block must not reuse an exemplar's
        # closing formula (a live run ended three different posts with the
        # same exemplar punch). Even a short closing formula reused verbatim
        # reads as canned.
        last_block = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
        if last_block and exemplars:
            tail = last_block[-1].strip()
            for ex in exemplars:
                ex_blocks = [b for b in re.split(r"\n\s*\n", str(ex).strip()) if b.strip()]
                if not ex_blocks:
                    continue
                run = _word_run_overlap(tail, ex_blocks[-1])
                if run >= int(self.rules.get("max_punch_word_run", 5)):
                    issues.append(
                        f"stock punch line reused from a style exemplar "
                        f"({run}-word verbatim closing)")
                    evidence.append(tail[:60])
                    break

        # Fabricated personal experience: first-person experience verbs without
        # a backing PERSONAL_EXPERIENCE claim.
        if not allow_personal_experience and \
                re.search(r"\b(i (built|shipped|tried|tested|learned)|my (team|project))\b", low):
            issues.append("first-person experience claim without PERSONAL_EXPERIENCE claim")

        # Research-derived families (blader/humanizer, avoid-ai-writing,
        # arvindrk/twitter-agent): each is individually gated so operators can
        # tune without touching code.
        if self.rules.get("ban_staged_runup", True):
            for pat in STAGED_RUNUP:
                if re.search(pat, low, re.MULTILINE):
                    issues.append("staged run-up opener: " + pat)
                    evidence.append(pat)
        if self.rules.get("ban_summary_closers", True):
            blocks = [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
            if blocks:
                for pat in SUMMARY_CLOSERS:
                    if re.search(pat, blocks[-1].lower()):
                        issues.append(f"self-satisfied summary closer: '{blocks[-1][:60]}'")
                        evidence.append(blocks[-1][:60])
                        break
        if self.rules.get("ban_countdown_negation", True):
            for pat in COUNTDOWN_NEGATION:
                if re.search(pat, low):
                    issues.append("countdown negation ('it's not... it's not... it's')")
                    evidence.append(pat)
        if self.rules.get("ban_borrowed_authority", True):
            for pat in BORROWED_AUTHORITY:
                if re.search(pat, low):
                    issues.append("borrowed authority (unnamed consensus): " + pat)
                    evidence.append(pat)
        if self.rules.get("ban_chatbot_residue", True):
            for pat in CHATBOT_RESIDUE:
                if re.search(pat, low):
                    issues.append("chatbot residue: " + pat)
                    evidence.append(pat)
        if self.rules.get("ban_x_lexicon", True):
            for pat in X_LEXICON:
                if re.search(pat, low, re.MULTILINE):
                    issues.append("engagement-cliche x lexicon: " + pat)
                    evidence.append(pat)
        if self.rules.get("ban_ing_riders", True):
            for pat in ING_RIDERS:
                if re.search(pat, low):
                    issues.append("-ing participle rider at sentence end")
                    evidence.append(pat)
        if self.rules.get("ban_copula_avoidance", True):
            for pat in COPULA_AVOIDANCE:
                if re.search(pat, low):
                    issues.append("copula avoidance ('serves as' -> 'is')")
                    evidence.append(pat)
        if self.rules.get("ban_uniform_rhythm", True):
            rhythm = uniform_rhythm_issue(text)
            if rhythm:
                issues.append(rhythm)
                evidence.append("burstiness")

        return SlopReport(not issues, issues, evidence)


def uniform_rhythm_issue(text: str) -> str | None:
    """Detect AI's most statistical fingerprint: uniform sentence rhythm.

    Human posts vary sentence length wildly (fragments next to long lines).
    Generated prose sits in a narrow band (brandonwise/humanizer's
    'burstiness' gate). Flag when the post has enough sentences to judge
    (>=5) and >=80% of them are within +/-2 words of the median length -
    i.e. the rhythm never breaks. Calibrated so all shipped exemplars pass.
    """
    lens = [len(_WORD_RE.findall(s)) for s in sentences(text or "")]
    lens = [n for n in lens if n > 0]
    if len(lens) < 5:
        return None
    med = sorted(lens)[len(lens) // 2]
    band = [n for n in lens if abs(n - med) <= 2]
    if len(band) / len(lens) >= 0.8 and (max(lens) - min(lens)) <= 4:
        return (f"uniform sentence rhythm: {len(band)}/{len(lens)} sentences "
                f"near {med} words - mix fragments with longer lines")
    return None


# An imperative opening is an actionable instruction ("Wash charge off
# before it arcs.") - mechanism/consequence sentences are the opposite of
# filler. Anchored on the first word so "Washing ..." does not match.
_IMPERATIVE_OPEN_RE = re.compile(
    r"^(wash|rinse|stop|start|skip|avoid|measure|check|charge|ground|run|"
    r"write|ship|test|ask|read|build|delete|cache|pin|set|use|keep|drop|"
    r"pick|note|count|watch|store|split|rotate|revoke|audit|compare|"
    r"divide|multiply|think|treat|replace|turn|switch|try|don'?t)\b",
    re.IGNORECASE)

# Demonstrative anaphora: "That voltage punches through paint." inherits the
# substance of the concrete sentence before it. The second word must be
# noun-ish - "That's the real win." / "This changes everything." stay slop.
_DEMONSTRATIVE_OPEN_RE = re.compile(
    r"^(that|this|these|those)\s+([A-Za-z][A-Za-z'-]*)\b", re.IGNORECASE)
_DEMONSTRATIVE_STOP = {
    "is", "are", "was", "were", "means", "changed", "changes", "matters",
    "feels", "seems", "happens", "happened", "said", "sounds", "looks",
    "gets", "got", "kind", "sort", "way", "one", "ones", "thing", "things"}


# Press-release feature list: "It supports 24 languages, light/dark modes,
# React inspection, cache/hard reload, infinite-scroll stop guard, and
# dark-mode PDF export." Enumeration verb + comma list is marketing copy,
# not a post - exactly the 'AI slop' shape a live run shipped at score 92
# (run 34342374809) because no detector recognized it.
_FEATURE_ENUM_RE = re.compile(
    r"\b(supports?|includes?|offers?|features|boasts?|provides?|comes with)\b"
    r"[^.!?\n]*,[^!?\n]*,[^!?\n]*", re.IGNORECASE)


def _feature_enumeration_sentence(sentence: str) -> bool:
    return bool(_FEATURE_ENUM_RE.search(sentence))


def _demonstrative_anaphora(sentence: str) -> bool:
    m = _DEMONSTRATIVE_OPEN_RE.match(sentence.strip())
    if not m:
        return False
    return m.group(2).lower() not in _DEMONSTRATIVE_STOP


def substantive_ratio(text: str) -> float:
    """Share of sentences that carry actual content (spec section 23 questions).

    A sentence counts as substantive when it contains a number, a proper-noun
    anchor (a named company/product/person/place - what concrete_anchors
    recognizes), an analysis/opinion marker, or a real question word - and is
    long enough to carry meaning at all (>= 4 words, so fragments like
    "Strong take." never count). Calibrated on live drafts: dense analytical
    sentences and named-specific posts without the original marker words must
    count, or every well-written opinion post gets flagged as slop.

    Live run 34338004018 false positive: "That voltage punches through
    paint. / Wash charge off before it arcs." scored as filler although those
    are the mechanism and the action - the most substantive lines of the
    post. Two additions:
    - an imperative opening counts (an instruction is substance);
    - a demonstrative + noun opening counts when the sentence before it was
      substantive (anaphora carries the referent forward).
    """
    sents = [s for s in sentences(text)
             if len(_WORD_RE.findall(s)) >= _MIN_SENTENCE_WORDS]
    if not sents:
        return 0.0
    markers = re.compile(
        r"\d|\b(because|which|means|should|must|isn'?t|doesn'?t|actually|"
        r"instead|problem|reason|evidence|data|results?|worse|better|wrong|right|"
        r"cost|trade|trade-?offs?|ownership|owning|profit|power|control|trust|"
        r"privacy|surveillance|anxiety|risk|attention|asset|persists?|outlasts?|"
        r"erodes?|overrides?|solves?|limits?|bottleneck|floor|"
        r"study|studies|shows?|found|measured|researchers?|survey|"
        r"fix|stop|avoid|skip|never|always|don'?t|"
        r"who|why|how)\b",
        re.IGNORECASE)
    hit = 0
    prev_hit = False
    for s in sents:
        counted = bool(markers.search(s))
        if not counted:
            # a proper-noun anchor carries substance even without marker
            # words: "Max Planck Institute", "NASA", "GraphQL" - anything
            # concrete_anchors would list as a named specific.
            counted = concrete_anchor_in_sentence(s)
        if not counted:
            counted = bool(_IMPERATIVE_OPEN_RE.match(s.strip()))
        if not counted and prev_hit:
            counted = _demonstrative_anaphora(s)
        prev_hit = counted
        if counted:
            hit += 1
    return hit / len(sents)


def concrete_anchor_in_sentence(sentence: str) -> bool:
    """True when the sentence contains a proper-noun anchor: a capitalized
    mid-sentence token that is not a stopword, a sentence-initial acronym
    (NASA, GraphQL - all-caps), or a sentence-initial possessive proper noun
    ("Roman's", "Hubble's" - common openings in shaped posts)."""
    words = _WORD_RE.findall(sentence)
    for idx, w in enumerate(words):
        if not (w[0].isupper() and len(w) >= 3 and w.lower() not in _NON_PROPER):
            continue
        if idx > 0:
            return True
        if idx == 0 and (w.isupper() or w.rstrip("s").endswith("'")):
            return True
    return False
