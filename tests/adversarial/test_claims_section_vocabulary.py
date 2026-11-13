"""The '§ section' label next to a verified quote, after the fixed-vocabulary fix (PR #76).

Texts are in the shape jats_to_text produces: one block per JATS <title>/<p>/<label>/<tr>,
joined by blank lines. A wrong label tells the reader a quote is a Result when it is a caption,
an abstract summary or the Discussion's interpretation.
"""

import pytest

from claimtrace.claims.retrieval import section_of

QUOTE = "decreased by 6.2 mmHg in the walking group"
FINDING = f"Mean systolic blood pressure {QUOTE} compared with usual care."
FRONT = ("Walking and blood pressure in older adults", "Moreau Tanaka", "Hypertension is common.")


def _text(*blocks: str) -> str:
    return "\n\n".join(blocks)


@pytest.mark.xfail(
    strict=True,
    reason="BUG: text under an unrecognised top-level heading inherits the previous section",
)
@pytest.mark.parametrize(
    ("before", "heading"),
    [
        ("1 Introduction", "2 Material and methods"),  # singular 'Material': very common
        ("1 Introduction", "2 Patients and methods"),  # clinical papers
        ("Background", "Case presentation"),  # BMC / case reports
        ("3 Results", "4 Discussion and conclusions"),  # interpretation shown as a Result
    ],
)
def test_unrecognised_section_heading_does_not_inherit_previous_label(before, heading):
    text = _text(
        *FRONT,
        before,
        "High blood pressure affects more than half of adults over sixty-five.",
        heading,
        FINDING,
    )
    # today: `before` ("1 Introduction", "Background", "3 Results")
    assert section_of(QUOTE, text) in (None, heading)


@pytest.mark.xfail(
    strict=True,
    reason="BUG: structured abstract whose headings never recur in the body is labelled as body",
)
def test_structured_abstract_without_repeated_heading_is_not_results():
    text = _text(
        "Walking and blood pressure in older adults",
        "Moreau Tanaka",
        "Abstract",
        "Background",
        "Hypertension is common in older adults.",
        "Methods",
        "We randomised 240 adults to walking or usual care.",
        "Results",
        FINDING,  # abstract only
        "Conclusions",
        "A walking programme is a reasonable first step.",
        "1 Introduction",
        "High blood pressure affects more than half of adults over sixty-five.",
        "2 Materials and methods",  # != 'Methods'
        "Adults were recruited from primary care.",
        "3 Results and discussion",  # != 'Results'; no Conclusions section
        "Of the 240 participants randomised, 221 completed the follow-up.",
    )
    # today: "Results" (the abstract's sub-heading, read as the paper's Results section)
    assert section_of(QUOTE, text) is None


@pytest.mark.xfail(
    strict=True,
    reason="BUG: 'Extended Data Fig. 1' / 'Supplementary Figure 1.' / 'S1 Fig' not float labels",
)
@pytest.mark.parametrize(
    "label", ["Extended Data Fig. 1", "Supplementary Figure 1.", "S1 Fig", "Supplementary Table 2"]
)
def test_caption_after_supplementary_float_label_is_not_methods(label):
    text = _text(
        *FRONT,
        "Discussion",
        "A supervised walking programme lowered systolic blood pressure modestly.",
        "Methods",  # Nature-style: Methods last, then the extended-data / supplementary floats
        "Adults were recruited from primary care.",
        label,
        f"Change in blood pressure. {FINDING}",  # caption
    )
    # today: "Methods"; the quote is a figure / table caption
    assert section_of(QUOTE, text) is None
