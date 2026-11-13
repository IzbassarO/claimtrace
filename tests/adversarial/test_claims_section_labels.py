"""The '§ section' label next to a verified quote must never name the wrong part of the paper.

Texts below are in the shape jats_to_text / body_text produce: title, author surnames, abstract,
body, captions, one block per JATS <title>/<p>/<label>/<tr>, joined by blank lines.
"""

from claimtrace.claims.retrieval import section_of

QUOTE = "decreased by 6.2 mmHg in the walking group"
FINDING = f"Mean systolic blood pressure {QUOTE} compared with usual care."


def _text(*blocks: str) -> str:
    return "\n\n".join(blocks)


def test_structured_abstract_results_is_not_the_results_section():
    text = _text(
        "Walking and blood pressure in older adults",
        "Moreau Tanaka",
        "Background",
        "Hypertension is common in older adults and walking may help.",
        "Results",
        FINDING,  # only in the abstract
        "Conclusions",
        "A walking programme is a reasonable first step.",
        "Introduction",
        "High blood pressure affects more than half of adults over sixty-five.",
        "Results",
        "Of the 240 participants randomised, 221 completed the follow-up.",
    )
    section = section_of(QUOTE, text)
    # today: "Results", read as the paper's Results section; the quote is abstract text
    assert section is None or "abstract" in section.lower()


def test_long_title_does_not_turn_author_names_into_a_section():
    text = _text(
        "Effect of a supervised walking programme on blood pressure in older adults: "
        "a randomised controlled trial",  # > 12 words, so not front matter
        "Moreau Tanaka Okafor Lindqvist",
        FINDING,  # unstructured abstract, no <title>
        "Participants were recruited from primary care and followed for twelve weeks.",
    )
    # today: "Moreau Tanaka Okafor Lindqvist"
    assert section_of(QUOTE, text) in (None, "Abstract")


def test_caption_after_label_with_period_is_not_discussion():
    text = _text(
        "Walking and blood pressure in older adults",
        "Moreau Tanaka",
        "Hypertension is common in older adults and walking may help.",
        "4 Discussion",
        "A supervised walking programme lowered systolic blood pressure modestly.",
        "Figure 2.",  # <label>Figure 2.</label> in <floats-group>, after the body
        f"Change in blood pressure. {FINDING}",
    )
    # today: "4 Discussion"; the quote is a figure caption
    assert section_of(QUOTE, text) is None


def test_text_after_roman_numeral_table_keeps_no_table_label():
    text = _text(
        "Walking and blood pressure in older adults",
        "Moreau Tanaka",
        "Hypertension is common in older adults and walking may help.",
        "3 Results",
        "Of the 240 participants randomised, 221 completed the follow-up.",
        "Table II",
        "Blood pressure outcomes",
        "Outcome | Walking | Usual care",
        "Systolic | -6.0 | -1.0",
        FINDING,  # Results text after the inline table
        "4 Discussion",
        "A supervised walking programme lowered systolic blood pressure modestly.",
    )
    # today: "Blood pressure outcomes" (the table caption title)
    assert section_of(QUOTE, text) in (None, "3 Results")


def test_table_group_row_is_not_a_section():
    text = _text(
        "Walking and blood pressure in older adults",
        "Moreau Tanaka",
        "Hypertension is common in older adults and walking may help.",
        "3 Results",
        "Of the 240 participants randomised, 221 completed the follow-up.",
        "Table 2",
        "Blood pressure outcomes",
        "Outcome | Walking | Usual care",  # header row ends the float
        "Women",  # spanning group row: one cell, no '|'
        "Systolic | -6.0 | -1.0",
        FINDING,  # Results text after the inline table
        "4 Discussion",
        "A supervised walking programme lowered systolic blood pressure modestly.",
    )
    # today: "Women"
    assert section_of(QUOTE, text) in (None, "3 Results")
