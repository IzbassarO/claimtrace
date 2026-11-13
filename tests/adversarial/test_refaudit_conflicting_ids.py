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


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a cited DOI/arXiv id resolving to a different paper is ignored -> verified",
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
