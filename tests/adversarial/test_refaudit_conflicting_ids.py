"""Adversarial: a reference whose DOI and arXiv id point to two different papers."""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work

OK = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex", "arxiv")]
ATTN = "Attention is all you need"
DEEP = "Deep learning"
# what the cited DOI resolves to (Crossref / OpenAlex DOI lookup)
BY_DOI = Work(
    provider="crossref", title=DEEP, year=2015, authors=["LeCun, Yann"], doi="10.1038/nature14539"
)
# what the cited arXiv id resolves to (arXiv batch / OpenAlex DataCite DOI lookup)
BY_ARXIV = Work(
    provider="arxiv", title=ATTN, year=2017, authors=["Ashish Vaswani"], arxiv_id="1706.03762"
)


@pytest.mark.parametrize(
    ("title", "year", "author"),
    [(ATTN, 2017, "Vaswani A"), (DEEP, 2015, "LeCun Y")],
    ids=["doi-points-elsewhere", "arxiv-points-elsewhere"],
)
def test_identifier_pointing_to_another_paper_is_not_verified(title, year, author):
    # Both identifiers are cited; one of them belongs to a different paper. The user's
    # bibliography links to the wrong paper, so the reference must not come out clean.
    ref = Reference(
        id="1",
        raw="",
        title=title,
        year=year,
        authors=[author],
        doi="10.1038/nature14539",
        arxiv_id="1706.03762",
    )
    a = refaudit.decide(ref, [BY_DOI, BY_ARXIV], OK)
    assert a.status == RefStatus.metadata_mismatch  # today: verified, mismatches == []
    assert {m.field for m in a.mismatches} & {"doi", "arxiv_id"}
    wrong = "doi" if title == ATTN else "arxiv_id"
    m = next(m for m in a.mismatches if m.field == wrong)
    assert m.cited in ("10.1038/nature14539", "1706.03762")
    assert (DEEP if wrong == "doi" else ATTN) in m.found


def test_preprint_and_journal_ids_of_same_work_stay_verified():
    # journal DOI + arXiv id of the same paper; the two records carry slightly different titles
    ref = Reference(
        id="1",
        raw="",
        title=DEEP,
        year=2015,
        authors=["LeCun Y"],
        doi="10.1038/nature14539",
        arxiv_id="1501.00001",
    )
    pre = Work(
        provider="arxiv",
        title="Deep learning.",
        year=2014,
        authors=["Yann LeCun"],
        arxiv_id="1501.00001",
    )
    a = refaudit.decide(ref, [BY_DOI, pre], OK)
    assert a.status == RefStatus.verified, a.mismatches
