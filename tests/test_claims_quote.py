import pytest

from claimtrace.claims.quote import normalize, verify_quote, word_count

SRC = (
    "In the treatment arm, 110 mg of drug X reduced systolic pressure by 12 mmHg (p<0.05). "
    "Atmospheric CO₂ levels rose sharply during the study period. "
    "The loss scales with x² for all tested models. "
    "We did not observe any “significant” effect in the control group. "
    "Patients didn't respond to placebo at all in this trial. "
    "The dose was 1.5 mg/kg in all adult patients enrolled. "
    "Mortality fell by 50% in the intervention group overall. "
    "Concentrations of 10⁻³ M were toxic to all cell lines tested. "
    "A dose-dependent increase was seen in every cohort we examined."
)


@pytest.mark.parametrize(
    "quote",
    [
        "110 mg of drug X reduced systolic pressure",
        "reduced systolic pressure by 12 mmHg (p<0.05)",
        "reduced systolic pressure by 12 mmHg",
        "Atmospheric CO₂ levels rose sharply",
        "The loss scales with x² for all tested models.",
        'We did not observe any "significant" effect',  # straight vs curly
        "We did not observe any “significant” effect",  # curly in quote too
        "we   did not\nobserve any “significant” effect",  # whitespace / line breaks
        "WE DID NOT OBSERVE ANY “SIGNIFICANT” EFFECT",  # case
        "Patients didn't respond to placebo",
        "Patients didn’t respond to placebo",  # curly apostrophe
        "The dose was 1.5 mg/kg in all adult patients",
        "Mortality fell by 50% in the intervention",
        "Concentrations of 10⁻³ M were toxic",
        "A dose-dependent increase was seen",
        '"Atmospheric CO₂ levels rose sharply"',  # model wrapped it in quotes
        "in the intervention group overall.",
    ],
)
def test_genuine_quotes_pass(quote):
    assert verify_quote(quote, SRC)


@pytest.mark.parametrize(
    "quote",
    [
        "10 mg of drug X reduced systolic pressure",  # 10 vs 110
        "reduced systolic pressure by 12 mmHg (p<0.01)",  # p value changed
        "reduced systolic pressure by 2 mmHg (p<0.05)",  # 12 -> 2
        "0.05). Atmospheric CO₂ levels",  # starts mid-number "<0.05"
        "Atmospheric CO2 levels rose sharply",  # CO2 vs CO₂ (no NFKC)
        "The loss scales with x2 for all",  # x2 vs x²
        "The loss scales with x for all tested",  # dropped superscript
        "Patients did respond to placebo at all",  # negation removed
        "Patients didn respond to placebo",
        "The dose was 1 mg/kg in all adult",
        "the dose was 1. 5 mg/kg in all",
        "The dose was 1.5 mg in all adult patients",
        "Mortality fell by 50 in the intervention",
        "Concentrations of 10 M were toxic",
        "Concentrations of 10⁻³ M were nontoxic",
        "treatment arm, 110 mg of drug X ... systolic pressure",  # ellipsis
        "treatment arm, 110 mg of drug X … systolic pressure",  # unicode ellipsis
        "treatment arm, 110 mg . . . systolic pressure",
        "Atmospheric CO₂ levels rose",  # 4 words
        "reduced systolic pressure by",  # 4 words
        "eatment arm, 110 mg of drug",  # partial word start
        "In the treatment arm, 110 m",  # partial word end
        "dependent increase was seen in every",  # starts inside "dose-dependent"
        "A dose-dependent increase was seen in every coh",
        "",
        "   ",
        "This sentence is not in the source at all.",
    ],
)
def test_bad_quotes_fail(quote):
    assert not verify_quote(quote, SRC)


@pytest.mark.parametrize(
    ("quote", "source", "ok"),
    [
        ("we gave 10 mg to all patients", "we gave 110 mg to all patients", False),
        ("we gave 10 mg to all patients", "We gave 10 mg to all patients.", True),
        ("gave 5 mg to all patients", "we gave -5 mg to all patients", False),
        ("gave 5 mg to all patients", "we gave −5 mg to all patients", False),
        ("the effect was 0.5 in all", "the effect was .5 in all", False),
        ("5 in all of the groups", "the effect was 0.5 in all of the groups", False),
        ("10 to 20 patients per site enrolled", "10–20 patients per site enrolled", False),
        ("the rate increased 5 in treated", "the rate increased 5-fold in treated", False),
        # truncations that are clean substrings but cut a token in half
        ("mortality fell sharply by 50", "mortality fell sharply by 50% overall", False),
        ("the observed range was 10", "the observed range was 10\u201320 here", False),
        ("the dose was 1.5 mg", "the dose was 1.5 mg/kg in adults", False),
        ("toxic at concentrations of 10", "toxic at concentrations of 10\u207b\u00b3 M", False),
        ("in this group patients did", "in this group patients didn't respond", False),
        ("in this group patients did", "in this group patients did respond", True),
        ("the rate increased by 5", "the rate increased by 5. Next we did more", True),
        ("the rate increased by 5.", "the rate increased by 5.2 in all", False),
        ("x is defined as the index i", "x is defined as the index i₁ here", False),
        ("x is defined as the index i₁", "x is defined as the index i1 here", False),
        # invisible characters are dropped on both sides
        (
            "an effective treatment for all patients",
            "an effec­tive treat​ment for all patients",
            True,
        ),
        # NBSP counts as whitespace
        (
            "an effective treatment for all patients",
            "an effective treatment for all patients",
            True,
        ),
        # first occurrence is mid-word, second is clean
        (
            "cat sat on the mat today",
            "bobcat sat on the mat today; a cat sat on the mat today",
            True,
        ),
        # no NFKC: ligature and fullwidth digits stay distinct
        ("the ﬁrst five patients were", "the first five patients were", False),
        ("dose of ５ mg in adults", "dose of 5 mg in adults", False),
    ],
)
def test_quote_table(quote, source, ok):
    assert verify_quote(quote, source) is ok


def test_normalize_only_allowed_changes():
    assert normalize("  A­B​ “x”\n\t‘y’ ") == "ab \"x\" 'y'"
    # digits, sub/superscripts, signs and ligatures untouched
    assert normalize("CO₂ x² −5 ﬁ ５") == "co₂ x² −5 ﬁ ５"


def test_word_count_ignores_punctuation_tokens():
    assert word_count("a - b = c ; d") == 4
    assert word_count("p < 0.05 in all five") == 5
