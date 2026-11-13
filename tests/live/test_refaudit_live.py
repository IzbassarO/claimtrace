from claimtrace import refaudit
from claimtrace.core.http import make_client
from claimtrace.core.schemas import Reference, RefStatus


async def test_live_real_retracted_negated_fabricated():
    refs = [
        Reference(
            id="lecun",
            raw="",
            title="Deep learning",
            authors=["LeCun Y", "Bengio Y", "Hinton G"],
            year=2015,
            doi="10.1038/nature14539",
        ),
        Reference(
            id="vaswani",
            raw="",
            title="Attention is all you need",
            authors=["Vaswani A"],
            year=2017,
            arxiv_id="1706.03762",
        ),
        Reference(
            id="wakefield",
            raw="",
            title="Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive "
            "developmental disorder in children",
            authors=["Wakefield AJ"],
            year=1998,
            doi="10.1016/S0140-6736(97)11096-0",
        ),
        Reference(
            id="negated",
            raw="",
            title="Why most published research findings are not false",
            authors=["Ioannidis JPA"],
            year=2005,
            doi="10.1371/journal.pmed.0020124",
        ),
        Reference(
            id="fake",
            raw="",
            title="Quantum entanglement of migratory zebra finches in lattice gauge supergravity",
            authors=["Nobody Q"],
            year=2021,
        ),
    ]
    async with make_client() as client:
        out = {a.reference.id: a for a in await refaudit.audit_references(refs, client)}
    for a in out.values():
        print(a.reference.id, a.status, a.note, [(p.provider, p.ok, p.error) for p in a.providers])
    assert out["lecun"].status == RefStatus.verified
    assert out["vaswani"].status == RefStatus.verified
    assert out["wakefield"].status == RefStatus.retracted
    assert out["negated"].status == RefStatus.metadata_mismatch
    assert [m.field for m in out["negated"].mismatches] == ["title"]
    fake = out["fake"]
    all_ok = all(p.ok for p in fake.providers)
    assert fake.status == (RefStatus.not_found if all_ok else RefStatus.unchecked)
