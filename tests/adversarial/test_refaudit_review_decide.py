"""Review repros on decide(): false `verified` from incomplete records, failed extraction,
unconfirmed arXiv DOIs and fabricated co-authors."""

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work

OK = [
    ProviderOutcome(provider="crossref", ok=True, candidates=1),
    ProviderOutcome(provider="openalex", ok=True, candidates=1),
]
T = "Deep residual learning for image recognition"


def test_incomplete_record_is_no_advantage():
    ref = Reference(id="1", raw="", title=T, authors=["Wrong, Alice"], year=2016, doi="10.1/x")
    cr = Work(provider="crossref", title=T, authors=["Real, Bob"], year=2016, doi="10.1/x")
    oa = Work(provider="openalex", title=T, authors=[], year=2016, doi="10.1/x")
    a = refaudit.decide(ref, [oa, cr], OK)
    assert a.status == RefStatus.metadata_mismatch
    assert [(m.field, m.cited) for m in a.mismatches] == [("authors", "Wrong, Alice")]
    assert a.matched.authors == ["Real, Bob"]


def test_one_agreeing_record_of_the_same_work_is_enough():
    # preprint year 2015, journal year 2016: citing either version is fine
    ref = Reference(id="1", raw="", title=T, authors=["He, K."], year=2016, doi="10.1/x")
    cr = Work(provider="crossref", title=T, authors=["He, Kaiming"], year=2016, doi="10.1/x")
    ax = Work(provider="arxiv", title=T, authors=["Kaiming He"], year=2014, arxiv_id="1512.03385")
    a = refaudit.decide(ref, [ax, cr], OK)
    assert a.status == RefStatus.verified
    assert a.note.endswith("checked: doi, title, authors (1), year")


def test_doi_only_reference_is_unchecked_not_verified():
    ref = Reference(
        id="1",
        raw="Smith J. Aspirin does not reduce mortality. 2019. doi:10.1/asp",
        doi="10.1/asp",
        year=2019,
    )
    w = Work(
        provider="crossref",
        title="Aspirin reduces mortality",
        authors=["Smith, J"],
        year=2019,
        doi="10.1/asp",
    )
    a = refaudit.decide(ref, [w], OK)
    assert a.status == RefStatus.unchecked
    assert a.note == (
        "identifier resolves to 'Aspirin reduces mortality'; "
        "cited title/authors could not be extracted to compare"
    )


def test_title_found_verbatim_in_raw_counts_as_compared():
    ref = Reference(
        id="1",
        raw="Smith J. Aspirin reduces mortality. 2019. doi:10.1/asp",
        doi="10.1/asp",
        year=2019,
    )
    w = Work(provider="crossref", title="Aspirin Reduces Mortality", year=2019, doi="10.1/asp")
    assert refaudit.decide(ref, [w], OK).status == RefStatus.verified


def test_negation_in_raw_keeps_title_unchecked():
    ref = Reference(
        id="1",
        raw="No evidence that aspirin reduces mortality in the elderly. 2019. doi:10.1/asp",
        doi="10.1/asp",
        year=2019,
    )
    w = Work(
        provider="crossref",
        title="Aspirin reduces mortality in the elderly",
        year=2019,
        doi="10.1/asp",
    )
    assert refaudit.decide(ref, [w], OK).status == RefStatus.unchecked


def test_id_and_title_without_author_or_year_is_unchecked():
    ref = Reference(id="1", raw="", title=T, doi="10.1/x")
    w = Work(provider="crossref", title=T, authors=["He, Kaiming"], year=2016, doi="10.1/x")
    assert refaudit.decide(ref, [w], OK).status == RefStatus.unchecked


def test_unconfirmed_arxiv_doi_is_a_doi_mismatch():
    ref = Reference(id="1", raw="", title=T, year=2016, doi="10.48550/arxiv.9999.99999")
    w = Work(provider="crossref", title=T, year=2016, doi="10.1109/cvpr.2016.90")
    a = refaudit.decide(ref, [w], OK)
    assert a.status == RefStatus.metadata_mismatch
    assert [m.field for m in a.mismatches] == ["doi"]


def test_confirmed_arxiv_doi_next_to_journal_record_is_fine():
    ref = Reference(id="1", raw="", title=T, year=2016, doi="10.48550/arxiv.1512.03385")
    cr = Work(provider="crossref", title=T, year=2016, doi="10.1109/cvpr.2016.90")
    oa = Work(provider="openalex", title=T, year=2015, doi="10.48550/arxiv.1512.03385")
    assert refaudit.decide(ref, [cr, oa], OK).status == RefStatus.verified


def test_fabricated_co_author_is_an_authors_mismatch():
    ref = Reference(id="1", raw="", title=T, authors=["He, K.", "Fabricated, Person"], year=2016)
    w = Work(provider="crossref", title=T, authors=["He, Kaiming", "Zhang, Xiangyu"], year=2016)
    a = refaudit.decide(ref, [w], OK)
    assert a.status == RefStatus.metadata_mismatch
    assert [(m.field, m.cited) for m in a.mismatches] == [("authors", "Fabricated, Person")]


def test_co_authors_beyond_a_truncated_provider_list_are_not_compared():
    ref = Reference(
        id="1", raw="", title=T, authors=["He, K.", "Zhang, X.", "Ren, S.", "et al."], year=2016
    )
    w = Work(provider="crossref", title=T, authors=["Kaiming He", "Xiangyu Zhang"], year=2016)
    assert refaudit.decide(ref, [w], OK).status == RefStatus.verified
