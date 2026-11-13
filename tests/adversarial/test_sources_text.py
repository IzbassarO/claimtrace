"""Source text must be what the provider returned: no merged numbers, no deleted spans."""

import claimtrace.sources as src
from claimtrace.core.schemas import Reference, ReferenceAudit, RefStatus, Work

FILLER = b"<p>" + b"Further methods text follows here. " * 80 + b"</p>"


def test_jats_superscript_exponent_does_not_merge_into_a_different_number():
    xml = (
        b"<article><front><article-meta><title-group><article-title>Viral load"
        b"</article-title></title-group></article-meta></front><body><sec>"
        b"<p>Treatment caused a 10<sup>5</sup>-fold drop in viral load.</p>"
        + FILLER
        + b"</sec></body></article>"
    )
    text = src.jats_to_text(xml)
    # A manuscript claiming "a 105-fold drop" would now get a verbatim quote match.
    assert "105-fold" not in text


async def test_crossref_abstract_superscript_does_not_merge_into_a_different_number():
    raw = "<jats:p>Treatment caused a 10<jats:sup>5</jats:sup>-fold drop in viral load.</jats:p>"
    w = Work(provider="crossref", title="Viral load", abstract=raw)
    audit = ReferenceAudit(
        reference=Reference(id="1", raw="x"), status=RefStatus.verified, matched=w
    )
    got = await src.fetch_source(audit, None, None)  # given abstract: no network needed
    assert got is not None
    assert "105-fold" not in got.text


async def test_plain_text_abstract_with_less_than_keeps_its_words():
    # arXiv / Semantic Scholar / OpenAlex abstracts are plain text, '<' is not a tag there.
    raw = "Risk rose for doses < 10 mg per day and fell for doses > 50 mg per day."
    w = Work(provider="arxiv", title="Dose response", abstract=raw)
    audit = ReferenceAudit(
        reference=Reference(id="1", raw="x"), status=RefStatus.verified, matched=w
    )
    got = await src.fetch_source(audit, None, None)
    assert got is not None
    # Today the text is "Risk rose for doses 50 mg per day.": a stitched, opposite statement.
    assert "Risk rose for doses 50 mg" not in got.text
    assert "10 mg" in got.text
