"""Originality engine - wording change must NOT escape detection (spec 19)."""
from src.similarity.engine import (OriginalityEngine, cosine_bow, exact_digest,
                                   hook_signature, jaccard, trigram_cosine)


HIST = [
    {"post_uid": "X-2026-00001", "body": "Most teams ship AI features before they know what failure looks like. The demo is easy; the fallout is not."}
]


def test_exact_duplicate_detected():
    eng = OriginalityEngine(history=HIST)
    r = eng.check("Most teams ship AI features before they know what failure looks like. The demo is easy; the fallout is not.")
    assert r.duplicated and r.level == "exact"


def test_near_duplicate_detected_with_different_words():
    eng = OriginalityEngine(history=HIST)
    r = eng.check("Most teams ship AI features before they know what failure looks like! The demo is easy, the fallout is not.")
    assert r.duplicated


def test_semantic_duplicate_detected_reworded():
    """Same idea reworded: the trigram semantic path catches it (word-order and
    morphology robust)."""
    eng = OriginalityEngine(history=HIST)
    r = eng.check("Shipping AI features comes before understanding failure modes "
                  "for most teams. Demos are simple; consequences are not.")
    assert r.duplicated and r.level == "semantic", r


def test_semantic_uses_embeddings_when_available():
    """With an embed_fn (NIM embeddings in production), genuinely reworded
    duplicates are caught even when surface words differ."""
    # deterministic toy embedding: shared concepts -> shared vectors
    vocab = {"ship": 0, "ai": 1, "feature": 2, "failure": 3, "demo": 4,
             "fallout": 5, "consequence": 6, "team": 7, "easy": 8, "simple": 9}
    def embed(texts):
        out = []
        for t in texts:
            v = [0.0] * len(vocab)
            for tok in t.lower().replace(";", " ").replace(".", " ").split():
                for key, idx in vocab.items():
                    if key in tok:
                        v[idx] = 1.0
            out.append(v)
        return out
    eng = OriginalityEngine(history=HIST, embed_fn=embed)
    r = eng.check("Most teams ship AI features before they understand what "
                  "failure means. The demo is easy, the consequences are not.")
    assert r.duplicated and r.level == "semantic"


def test_genuinely_new_content_passes():
    eng = OriginalityEngine(history=HIST)
    r = eng.check("Vector databases are mostly a caching problem in disguise, and cache invalidation remains the hard part.")
    assert not r.duplicated


def test_hook_duplication_detected():
    hist = [{"post_uid": "X-1", "body": "Everyone is wrong about context windows. Here is why size is not the point."}]
    eng = OriginalityEngine(history=hist)
    r = eng.check("Everyone is wrong about context limits. Here is why size is not the point.")
    assert r.duplicated


def test_thread_flat_body_checked():
    hist = [{"post_uid": "X-2", "body": "post one\npost two"}]
    eng = OriginalityEngine(history=hist)
    r = eng.check("ignored", thread_posts=["post one", "post two"])
    assert r.duplicated and r.level == "exact"


def test_empty_history_never_flags():
    eng = OriginalityEngine(history=[])
    assert not eng.check("anything at all").duplicated


def test_helpers():
    assert jaccard(["a", "b"], ["a", "b"]) == 1.0
    assert exact_digest("Hi there") == exact_digest("hi there")
    assert hook_signature("First sentence here. Second one.") == "first sentence here"


def test_cosine_bow_zero_on_disjoint():
    assert cosine_bow(["a", "b"], ["c", "d"]) == 0.0
