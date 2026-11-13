import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `eval` is a top-level folder, not an installed package

from claimtrace.core.schemas import ClaimCheck, Reference, ReferenceAudit, Report  # noqa: E402
from claimtrace.ingest.text import find_citations, split_entries, split_manuscript  # noqa: E402
from eval import bench, run  # noqa: E402

HTML = """<html><body><nav>Skip me</nav><article class="ltx_document">
<div class="ltx_para"><p>Licence notice above the title.</p></div>
<h1 class="ltx_title ltx_title_document">Attention Is All You Need</h1>
<section class="ltx_section"><h2 class="ltx_title"><span class="ltx_tag">1</span>Intro</h2>
<div class="ltx_para"><p>Recurrent
   models <cite>[<a href="#bib.bib1">1</a>, <a href="#bib.bib2">2</a>]</cite> scale with
<math alttext="O(n)"><mi>O</mi></math> steps.<span class="ltx_note">Footnote text.</span></p>
<br><img src="x.png"><table><tr><td>Table cell</td></tr></table>
<figure><figcaption>Figure caption</figcaption></figure></div></section>
<section class="ltx_bibliography"><h2>References</h2><ul>
<li class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[1]</span>
<span class="ltx_bibblock">Ba, Kiros.

</span><span class="ltx_bibblock">Layer normalization.</span></li>
<li class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[2]</span>
<span class="ltx_bibblock">Bahdanau.</span><span class="ltx_bibblock">NMT.</span></li>
</ul></section>
<section class="ltx_appendix"><h2>Attention Visualizations</h2></section>
</article></body></html>"""

JATS = b"""<article><front><article-meta><title-group><article-title>A <italic>TRP</italic>
review</article-title></title-group><abstract><p>Short abstract.</p></abstract></article-meta>
</front><body><sec><title>1. Intro</title><p>Channels matter [<xref ref-type="bibr" rid="B1">1
</xref>,<xref ref-type="bibr" rid="B2">2</xref>].<fig><caption><p>Fig text</p></caption></fig>
</p><table-wrap><table><tr><td>cell</td></tr></table></table-wrap></sec></body>
<back><ref-list><ref id="B1"><label>1.</label><mixed-citation><named-content>Steiner T.J.
Headache. BMJ. 2002;325:881-886.</named-content><ext-link/></mixed-citation></ref>
<ref id="B2"><element-citation>Doe J. Pain. 2001.</element-citation></ref></ref-list></back>
</article>"""


def test_html_to_text_numbered_manuscript():
    text = bench.html_to_text(HTML)
    assert text.startswith("Attention Is All You Need\n\n1 Intro\n\n")
    assert "Recurrent models [1, 2] scale with O(n) steps." in text
    for gone in ("Licence notice", "Skip me", "Footnote", "Table cell", "Figure caption"):
        assert gone not in text
    assert "Attention Visualizations" not in text  # appendix after the bibliography dropped
    body, section, warn = split_manuscript(text)
    assert warn is None
    assert split_entries(section) == [
        ("1", "Ba, Kiros. Layer normalization."),
        ("2", "Bahdanau. NMT."),
    ]
    refs = [Reference(id="1", raw="a"), Reference(id="2", raw="b")]
    cites, _ = find_citations(body, refs)
    assert [c.ref_ids for c in cites] == [["1", "2"]]


def test_jats_to_text():
    text = bench.jats_to_text(JATS)
    assert text.startswith("A TRP review\n\nShort abstract.\n\n1. Intro\n\nChannels matter [1 ,2].")
    assert "Fig text" not in text and "cell" not in text
    _, section, _ = split_manuscript(text)
    assert split_entries(section) == [
        ("1", "Steiner T.J. Headache. BMJ. 2002;325:881-886."),
        ("2", "Doe J. Pain. 2001."),
    ]


def test_stage_rows_idle_and_ops(monkeypatch):
    monkeypatch.setattr(run.pipeline, "SOURCE_CONCURRENCY", 6)
    t = {
        "t_ingest_start": 0.0,
        "t_ingest_end": 2.0,
        "busy_ingest": 2.0,
        "n_ingest": 1.0,
        "t_sources_start": 2.0,
        "t_sources_first": 3.0,
        "t_sources_end": 12.0,
        "busy_source_fetch": 30.0,
        "n_source_fetch": 10.0,
        "t_references_start": 2.0,
        "t_references_end": 5.0,
        "n_verdict_cache_hit": 3.0,
    }
    rows = {r["stage"]: r for r in run.stage_rows(t)}
    assert rows["ingest"]["idle"] == 0.0
    assert (rows["sources"]["wall"], rows["sources"]["slots"]) == (10.0, 6)
    assert rows["sources"]["idle"] == 30.0  # 10 s x 6 slots - 30 s busy
    assert rows["references"]["wall"] == 3.0 and rows["references"]["idle"] is None
    assert rows["claims"]["wall"] is None and rows["claims"]["idle"] is None
    ops = {o["op"]: o for o in run.op_rows(t)}
    assert ops["source_fetch"] == {"op": "source_fetch", "n": 10, "busy": 30.0, "mean": 3.0}
    assert ops["verdict_cache_hit"]["busy"] is None


def _report(complete: bool, n_claims: int = 0) -> Report:
    audit = ReferenceAudit(reference=Reference(id="1", raw="x"), status="verified")
    checks = [
        ClaimCheck(citation_id="c", ref_id="1", sentence="s", verdict="insufficient_evidence")
    ] * n_claims
    return Report(id="r", references=[audit], claims=checks, complete=complete)


def test_time_doc_streaming_and_limit_warnings(monkeypatch):
    async def fake_run(text, files, on_partial=None):
        on_partial(Report(id="r", complete=False))  # nothing finished yet
        on_partial(_report(False))
        on_partial(_report(False, 1))
        final = _report(True, 2)
        final.warnings = ["Only the first 150 of 300 citation pairs checked.", "other"]
        final.timings = {"t_total": 1.0, "n_verdict_cache_hit": 1.0}
        return final

    monkeypatch.setattr(run.pipeline, "run", fake_run)
    row = asyncio.run(run.time_doc("text", []))
    assert row["partials"] == 3
    assert row["first_reference_s"] <= row["first_claim_s"] <= row["total_s"]
    assert row["limit_warnings"] == ["Only the first 150 of 300 citation pairs checked."]
    assert row["cache"] == "warm" and row["n_claims"] == 2 and row["peak_rss_mb"] > 0


def test_time_doc_without_streaming_uses_final_report(monkeypatch):
    async def fake_run(text, files, on_partial=None):
        return _report(True, 1)

    monkeypatch.setattr(run.pipeline, "run", fake_run)
    row = asyncio.run(run.time_doc("text", []))
    assert row["partials"] == 0 and row["cache"] == "cold"
    assert row["first_reference_s"] == row["first_claim_s"] == row["total_s"]


def test_run_timing_skips_and_renders(monkeypatch):
    def fake_load(name):
        if name == "attention-pdf":
            raise bench.Skip("GROBID not reachable")
        return "text", []

    async def fake_run(text, files, on_partial=None):
        return _report(True)

    monkeypatch.setattr(run.bench, "load", fake_load)
    monkeypatch.setattr(run.pipeline, "run", fake_run)
    res = asyncio.run(run.run_timing(["attention-pdf", "attention-text"], repeat=2))
    assert res["metrics"] == {"runs": 2, "skipped": 1}
    md = "\n".join(run.render_timing(res))
    assert "skipped `attention-pdf`: GROBID not reachable" in md
    assert "| attention-text#1 | cold |" in md
    assert "| claims |" in md
