"""'does not resolve' is a claim about doi.org, not about Crossref/OpenAlex coverage."""

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work


def test_datacite_doi_is_not_reported_unresolvable():
    t = "A benchmark of protein language models"
    # deposited on Zenodo (DataCite DOI, resolves at doi.org), indexed by OpenAlex only via arXiv
    ref = Reference(
        id="1", raw="", title=t, authors=["Lee, K."], year=2022, doi="10.5281/zenodo.6543210"
    )
    preprint = Work(
        provider="openalex",
        title=t,
        authors=["Kim Lee"],
        year=2022,
        doi="10.48550/arxiv.2205.01234",
    )
    ok = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex")]
    a = refaudit.decide(ref, [preprint], ok)
    assert not any(m.found == "does not resolve" for m in a.mismatches)
    assert a.status != RefStatus.metadata_mismatch
