from postgate import normalize, similar, text_key


def test_normalize_strips_cosmetic_rewrites():
    a = "@someone @other  It\u2019s   LIVE: https://example.com/x?y=1 \u2014 go"
    assert normalize(a) == "it's live: - go"


def test_similar_prefix_and_ratio():
    base = "Ship small, verify everything, and never retry a post you haven't confirmed is missing."
    assert similar(base, base.upper())
    assert similar(base, base + " Extra tail")           # same 80-char prefix
    assert similar(base, base.replace("small", "smol"))  # ratio >= 0.9
    assert not similar(base, "Completely different text about gardening.")
    assert not similar("", base)


def test_text_key_prefix():
    assert text_key("x" * 200, 80) == "x" * 80
