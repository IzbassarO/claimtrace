"""`_published_elsewhere` (PR #63) must not split a preprint from its own journal version.

arXiv's DOI field may hold several space-separated DOIs (the article and its erratum). The
preprint's "own journal DOI" is then the whole string, which never equals the journal record's
DOI, so the preprint counts as "published elsewhere", is no longer a version of the cited
paper, and its arXiv id is reported as belonging to a different work.
"""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.refaudit.providers import parse_arxiv

OK = [ProviderOutcome(provider=p, ok=True, candidates=1) for p in ("crossref", "openalex", "arxiv")]
T = "Gravitational waves from binary black holes"
ARXIV = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/0705.1234v2</id><published>2007-05-09T00:00:00Z</published>
<title>Gravitational waves from binary black holes</title>
<author><name>Ann Lee</name></author>
<arxiv:doi>10.1103/PhysRevD.76.044016 10.1103/PhysRevD.77.029903</arxiv:doi>
<arxiv:journal_ref>Phys. Rev. D 76, 044016 (2007); Erratum ibid. 77, 029903</arxiv:journal_ref>
</entry></feed>"""


@pytest.mark.xfail(
    strict=True, reason="BUG: arXiv record with article+erratum DOIs flags a correct citation"
)
def test_preprint_listing_article_and_erratum_dois_is_a_version_of_the_article():
    [pre] = parse_arxiv(ARXIV)
    journal = Work(
        provider="crossref",
        title=T,
        authors=["Lee, Ann"],
        year=2007,
        doi="10.1103/physrevd.76.044016",
    )
    # a fully correct citation: journal DOI + the arXiv id of the same paper
    ref = Reference(
        id="1",
        raw="",
        title=T,
        authors=["Lee, A."],
        year=2007,
        doi="10.1103/PhysRevD.76.044016",
        arxiv_id="0705.1234",
    )
    a = refaudit.decide(ref, [journal, pre], OK)
    # today: metadata_mismatch, arxiv_id "resolves to: Gravitational waves from binary black holes"
    assert a.status == RefStatus.verified, (a.mismatches, a.note)
