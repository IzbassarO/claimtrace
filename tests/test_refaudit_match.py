import pytest

from claimtrace.core.schemas import Reference, Work
from claimtrace.refaudit.match import (
    family_name,
    first_author_equal,
    identifies,
    mismatches,
    negations,
    norm_arxiv,
    norm_doi,
    normalize_title,
    title_match,
    year_ok,
)


@pytest.mark.parametrize(
    "cited, found, ok",
    [
        ("Attention is all you need", "Attention Is All You Need", True),
        ("Attention is all you need.", "Attention Is All You Need", True),
        ("Évaluation des modèles", "Evaluation des modeles", True),
        ("Deep residual learning for image recognition", "Deep Residual Learning", False),
        (
            "BERT: Pre-training of deep bidirectional transformers for language understanding",
            "BERT: pre-training of deep bidirectional transformers for language understanding",
            True,
        ),
        # subtitle missing on one side still matches when the main title is substantive
        (
            "Why most published research findings are false",
            "Why Most Published Research Findings Are False: an essay",
            True,
        ),
        # a 1-word main title alone proves nothing
        ("BERT", "BERT: Pre-training of deep bidirectional transformers", False),
        # negation rule
        ("Vaccines cause autism", "Vaccines do not cause autism", False),
        ("Vaccines do not cause autism", "Vaccines don't cause autism", True),
        ("Learning with labels", "Learning without labels", False),
        ("Small cell lung cancer outcomes", "Non-small cell lung cancer outcomes", False),
        ("Statins never reduce mortality", "Statins reduce mortality", False),
        # number rule
        ("Results of the 2019 survey", "Results of the 2020 survey", False),
        (
            "GPT-3 language models are few-shot learners",
            "GPT-4 language models are few-shot learners",
            False,
        ),
        ("COVID-19 outcomes in adults", "COVID 19 outcomes in adults", True),
        # retraction prefix is ignored for matching
        (
            "Ileal-lymphoid-nodular hyperplasia in children",
            "RETRACTED: Ileal-lymphoid-nodular hyperplasia in children",
            True,
        ),
        # retraction/withdrawal prefixes in other spellings
        ("Ileal hyperplasia in children", "WITHDRAWN: Ileal hyperplasia in children", True),
        ("Ileal hyperplasia in children", "Retraction Note: Ileal hyperplasia in children", True),
        # more negation phrasings; "failure" alone is not one
        ("Lack of effect of drug X on pain", "Effect of drug X on pain", False),
        ("Drug X fails to reduce pain in adults", "Drug X reduces pain in adults", False),
        ("Heart failure outcomes in older adults", "Heart failure outcomes in older adults", True),
        (
            "Nonlinear dynamics of coupled oscillators",
            "Non-linear dynamics of coupled oscillators",
            True,
        ),
        # roman / spelled-out numbers compare as numbers
        ("A phase II trial of drug X in melanoma", "A phase 2 trial of drug X in melanoma", True),
        (
            "A phase II trial of drug X in melanoma",
            "A phase III trial of drug X in melanoma",
            False,
        ),
        ("Two-year outcomes of valve replacement", "2-year outcomes of valve replacement", True),
        ("X-ray imaging of the human chest wall", "X-ray imaging of the human chest wall", True),
        # subtitles: omitted (even with a number) matches, a different one does not
        (
            "Mortality after cardiac surgery",
            "Mortality after cardiac surgery: a 10-year study",
            True,
        ),
        ("Deep learning in imaging: promises", "Deep learning in imaging: pitfalls", False),
        # a correction notice is not the paper
        (
            "Effects of drug X on pain in adults",
            "Correction to: Effects of drug X on pain in adults",
            False,
        ),
        (
            "Effects of drug X on pain in adults",
            "Author Correction: Effects of drug X on pain in adults",
            False,
        ),
        ("Correction of scoliosis in adolescents", "Correction of scoliosis in adolescents", True),
        # clearly different
        ("Deep learning", "Guest Editorial: Deep Learning", False),
        (None, "Anything", False),
    ],
)
def test_title_match(cited, found, ok):
    assert title_match(cited, found) is ok


def test_normalize_and_negations():
    assert normalize_title("  The “Quick”, brown—fox!  ") == "the quick brown fox"
    assert negations("It doesn't work without data") == {"not", "without"}
    assert negations("It works") == set()
    assert negations("cannot") == negations("can't") == negations("not") == {"not"}


@pytest.mark.parametrize(
    "cited, found, ok",
    [
        (2020, 2020, True),
        (2020, 2021, True),
        (2020, 2019, True),
        (2020, 2022, False),
        (None, 2020, False),
    ],
)
def test_year_ok(cited, found, ok):
    assert year_ok(cited, found) is ok


@pytest.mark.parametrize(
    "author, fam",
    [
        ("Smith, John", "smith"),
        ("John Smith", "smith"),
        ("J. Smith", "smith"),
        ("Smith J", "smith"),
        ("LeCun Y", "lecun"),
        ("Li J", "li"),
        ("Aidan N. Gomez", "gomez"),
        ("Müller, Jürgen", "muller"),
        ("van der Berg, J.", "berg"),
        ("J. van der Berg", "berg"),
    ],
)
def test_family_name(author, fam):
    assert family_name(author) == fam


def test_first_author_equal():
    assert first_author_equal(["Vaswani A"], ["Ashish Vaswani", "Noam Shazeer"])
    assert not first_author_equal(["Shazeer, N"], ["Ashish Vaswani", "Noam Shazeer"])
    assert not first_author_equal([], ["Ashish Vaswani"])


@pytest.mark.parametrize(
    "raw, norm",
    [
        ("https://doi.org/10.1038/Nature14539", "10.1038/nature14539"),
        ("http://dx.doi.org/10.1038/nature14539", "10.1038/nature14539"),
        ("doi:10.1038/nature14539.", "10.1038/nature14539"),
        ("DOI: 10.1038/NATURE14539", "10.1038/nature14539"),
        (None, None),
    ],
)
def test_norm_doi(raw, norm):
    assert norm_doi(raw) == norm


@pytest.mark.parametrize(
    "raw, norm",
    [
        ("1706.03762v7", "1706.03762"),
        ("arXiv:1706.03762", "1706.03762"),
        ("https://arxiv.org/abs/1706.03762v2", "1706.03762"),
        ("https://arxiv.org/pdf/1706.03762v2.pdf", "1706.03762"),
        ("hep-th/9711200v3", "hep-th/9711200"),
    ],
)
def test_norm_arxiv(raw, norm):
    assert norm_arxiv(raw) == norm


TF = "Attention Is All You Need"
JOURNAL = Work(
    provider="crossref", title=TF, authors=["Vaswani, Ashish"], year=2017, doi="10.5555/nips.2017"
)
PREPRINT = Work(
    provider="arxiv",
    title=TF,
    authors=["Ashish Vaswani"],
    year=2017,
    arxiv_id="1706.03762",
    doi="10.48550/arxiv.1706.03762",
)


@pytest.mark.parametrize(
    "ref, work, ident, fields",
    [
        # DOI equal, everything agrees
        (
            Reference(id="1", raw="", title=TF, year=2017, doi="10.5555/NIPS.2017"),
            JOURNAL,
            True,
            [],
        ),
        # title + year within 1, no ids
        (Reference(id="1", raw="", title=TF, year=2018), JOURNAL, True, []),
        # title + first author, year way off -> identified with a year mismatch
        (
            Reference(id="1", raw="", title=TF, year=2020, authors=["Vaswani A"]),
            JOURNAL,
            True,
            ["year"],
        ),
        # title + year, wrong first author -> authors mismatch
        (
            Reference(id="1", raw="", title=TF, year=2017, authors=["Shazeer N"]),
            JOURNAL,
            True,
            ["authors"],
        ),
        # title only, year off, author wrong -> not identified
        (Reference(id="1", raw="", title=TF, year=2020, authors=["Doe J"]), JOURNAL, False, None),
        # DOI resolves to a different title -> identified, title mismatch
        (
            Reference(
                id="1", raw="", title="Something else entirely here", doi="10.5555/nips.2017"
            ),
            JOURNAL,
            True,
            ["title"],
        ),
        # negated title with the right DOI -> title mismatch
        (
            Reference(
                id="1",
                raw="",
                title="Attention Is Not All You Need",
                year=2017,
                doi="10.5555/nips.2017",
            ),
            JOURNAL,
            True,
            ["title"],
        ),
        # preprint vs journal: cited journal DOI, found arXiv record -> identified by title+year,
        # the arXiv DOI is a version difference, not a doi mismatch
        (
            Reference(id="1", raw="", title=TF, year=2017, doi="10.5555/nips.2017"),
            PREPRINT,
            True,
            [],
        ),
        # cited arXiv id with version equals record id
        (Reference(id="1", raw="", title=TF, arxiv_id="1706.03762v5"), PREPRINT, True, []),
        # cited arXiv id differs
        (
            Reference(id="1", raw="", title=TF, year=2017, arxiv_id="1706.99999"),
            PREPRINT,
            True,
            ["arxiv_id"],
        ),
        # different DOI, same title+year -> doi mismatch
        (
            Reference(id="1", raw="", title=TF, year=2017, doi="10.1234/other"),
            JOURNAL,
            True,
            ["doi"],
        ),
    ],
)
def test_identity_and_mismatches(ref, work, ident, fields):
    assert identifies(ref, work) is ident
    if ident:
        assert [m.field for m in mismatches(ref, work)] == fields


def test_norm_doi_decodes_once_and_drops_invisible_chars():
    from claimtrace.refaudit.match import all_dois, norm_doi

    assert norm_doi("https://doi.org/10.1016/S0140-6736%2820%2930183-5") == (
        "10.1016/s0140-6736(20)30183-5"
    )
    assert norm_doi("10.1/a%2528b") == "10.1/a%28b"  # decoded once, not twice
    assert norm_doi("doi:10.1/a​b­c﻿") == "10.1/abc"
    assert all_dois("10.1/A 10.1/B.") == ["10.1/a", "10.1/b"]
    assert norm_doi("10.1/A 10.1/B") == "10.1/a"
