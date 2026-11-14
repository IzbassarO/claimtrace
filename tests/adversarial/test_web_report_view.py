"""The report page (app.js renderReport) misleads a reader skimming the summary and open groups.

Runs the real app.js in node with a tiny fake DOM (no network, no browser); skipped without node.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from claimtrace.core.schemas import (
    ClaimCheck,
    EvidenceKind,
    ModelOpinion,
    ProviderOutcome,
    Reference,
    ReferenceAudit,
    RefStatus,
    Report,
    Verdict,
    Work,
)
from claimtrace.web import app as web

JS = Path(web.__file__).parent / "static" / "app.js"

# Minimal DOM: enough for el()/link()/group()/renderReport(); dumps #summary/#refs/#claims as JSON.
HARNESS = r"""
const fs = require("fs"), vm = require("vm");
class Node_ {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.className = ""; }
  append(...cs) { for (const c of cs) this.kids.push(typeof c === "string" ? new Text_(c) : c); }
  replaceChildren(...cs) { this.kids = []; this.append(...cs); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  focus() {}
  get textContent() { return this.kids.map((k) => k.textContent).join(""); }
  set textContent(v) { this.kids = [new Text_(String(v))]; }
  dump() { return { tag: this.tag, cls: this.className, hidden: this.hidden, attrs: this.attrs,
                    kids: this.kids.map((k) => k.dump()) }; }
}
class Text_ {
  constructor(t) { this.t = t; }
  get textContent() { return this.t; }
  dump() { return this.t; }
}
const ids = {};
const document = {
  createElement: (t) => new Node_(t),
  createTextNode: (t) => new Text_(t),
  getElementById: (id) => (ids[id] ||= new Node_("div")),
  addEventListener() {},
};
const ctx = { document, URL, location: { origin: "http://x", pathname: "/" },
              history: { replaceState() {} }, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv.at(-1), "utf8"), ctx);
// stdin: a list of renderReport argument lists, or strings of JS run in the page between calls
for (const c of JSON.parse(fs.readFileSync(0, "utf8")))
  typeof c === "string" ? vm.runInContext(c, ctx) : ctx.renderReport(...c);
const out = {};
for (const id of ["headline", "ledgers", "meters", "timings", "filter-note", "summary", "refs",
                  "claims", "refs-wait",
                  "claims-wait", "live", "live-stage",
                  "saved", "saved-note", "report"]) out[id] = document.getElementById(id).dump();
process.stdout.write(JSON.stringify(out));
"""


def render(report: Report) -> dict:
    return render_calls([[report.model_dump(mode="json")]])


def render_calls(calls: list) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    r = subprocess.run(
        [node, "-e", HARNESS, str(JS)],
        input=json.dumps(calls),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def text(n) -> str:
    return n if isinstance(n, str) else "".join(text(k) for k in n["kids"])


def collapsed(n) -> str:
    """Text inside the collapsed <details class="fine"> group(s)."""
    if isinstance(n, str):
        return ""
    if n["tag"] == "details" and n["cls"] == "fine":
        return text(n)
    return "".join(collapsed(k) for k in n["kids"])


def open_text(n) -> str:
    """Text a reader sees without expanding the collapsed group."""
    if isinstance(n, str):
        return n
    if n["tag"] == "details" and n["cls"] == "fine":
        return ""
    return "".join(open_text(k) for k in n["kids"])


def ref(i: str, title: str) -> Reference:
    return Reference(id=i, raw=f"[{i}] Author A. {title}. J. 2005.", title=title, year=2005)


ok = [ProviderOutcome(provider="crossref", ok=True, candidates=3)]


def test_supported_claim_citing_retracted_paper_is_not_hidden():
    # refaudit says ref 1 is retracted; sources still fetch it, so the claim can come back
    # 'supported' with a verbatim quote from the retracted paper
    w = Work(provider="crossref", title="Fabricated stem cells", year=2005, is_retracted=True)
    report = Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=ref("1", "Fabricated stem cell results"),
                status=RefStatus.retracted,
                matched=w,
                providers=ok,
            )
        ],
        claims=[
            ClaimCheck(
                citation_id="c1",
                ref_id="1",
                sentence="Stem cells cure diabetes in mice [1].",
                verdict=Verdict.supported,
                quote="stem cells cured diabetes in all treated mice",
                evidence_kind=EvidenceKind.abstract,
            )
        ],
    )
    out = render(report)
    # the claim card must be visible (not collapsed) and say the source is retracted
    assert "Stem cells cure diabetes" not in collapsed(out["claims"])
    assert "The cited source is retracted" in open_text(out["claims"])
    assert "1 of 1 claim needs attention" in text(out["summary"])


def test_supported_claim_citing_mismatched_reference_is_shown_with_a_note():
    w = Work(provider="crossref", title="Deep residual learning", year=2009)
    report = Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=ref("1", "Deep residual learning"),
                status=RefStatus.metadata_mismatch,
                matched=w,
                providers=ok,
            )
        ],
        claims=[
            ClaimCheck(
                citation_id="c1",
                ref_id="1",
                sentence="Residual learning eases training [1].",
                verdict=Verdict.supported,
                quote="residual learning framework to ease the training of networks",
                evidence_kind=EvidenceKind.abstract,
            )
        ],
    )
    out = render(report)
    shown = open_text(out["claims"])
    assert "details differ from the found record" in shown
    assert "Residual learning eases training" in shown
    # counted by its own verdict: a mismatch alone is not a claim problem
    assert "0 of 1 claim need attention" in text(out["summary"])


def test_unchecked_with_all_providers_ok_is_not_called_a_lookup_failure():
    # refaudit returns unchecked without any failure for: a title that exists with another
    # year/author, a possible match, an ambiguous truncated title, nothing to look up
    report = Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=ref("1", "Why most published research findings are false"),
                status=RefStatus.unchecked,
                providers=ok,
                note="crossref has a work with this title (1998, Smith, J.) but neither year "
                "(±1) nor first author confirm it is the cited work",
            )
        ],
    )
    shown = text(render(report)["refs"]).lower()
    # every provider answered: telling the user "the lookup failed" sends them to retry later
    # instead of checking a likely miscited reference
    assert "lookup failed" not in shown
    assert "could not check" in shown
    assert "neither year (±1) nor first author confirm it" in shown


def test_unchecked_after_provider_failure_says_lookup_failed():
    report = Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=ref("1", "Deep residual learning"),
                status=RefStatus.unchecked,
                providers=[ProviderOutcome(provider="crossref", ok=False, error="timeout")],
            )
        ],
    )
    shown = text(render(report)["refs"])
    assert "Could not check (lookup failed)" in shown
    assert "crossref failed: timeout" in shown


def test_summary_does_not_report_all_clear_when_no_claim_was_checked():
    # e.g. citation markers the parser did not recognise: references audited, no claims checked
    w = Work(provider="crossref", title="Deep residual learning", year=2005)
    report = Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=ref("1", "Deep residual learning"),
                status=RefStatus.verified,
                matched=w,
                providers=ok,
            )
        ],
    )
    summary = text(render(report)["summary"])
    assert "0 of 0 claims" not in summary, summary
    assert "No citing sentences were found or checked" in summary


def test_collapsed_group_count_is_pluralised():
    w = Work(provider="crossref", title="Deep residual learning", year=2005)
    report = Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=ref("1", "Deep residual learning"),
                status=RefStatus.verified,
                matched=w,
                providers=ok,
            )
        ],
        claims=[
            ClaimCheck(
                citation_id="c1",
                ref_id="1",
                sentence="Residual learning eases training [1].",
                verdict=Verdict.supported,
                quote="residual learning framework to ease the training of networks",
                evidence_kind=EvidenceKind.abstract,
                judge=ModelOpinion(verdict="supported"),
                skeptic=ModelOpinion(verdict="supported"),
            )
        ],
    )
    out = render(report)
    assert "1 verified references" not in text(out["refs"])
    assert "1 supported claims" not in text(out["claims"])
    assert "1 verified reference" in text(out["refs"])
    assert "1 supported claim" in text(out["claims"])


def test_supported_claim_citing_unchecked_reference_is_not_hidden():
    # ref 1 matched by title, but its cited DOI could not be confirmed (doi.org failed):
    # unchecked; sources still fetch the matched record, so the claim comes back 'supported'
    w = Work(provider="openalex", title="Ocean warming trends", year=2005)
    failed = ok + [ProviderOutcome(provider="doi.org", ok=False, error="ProviderError: HTTP 429")]
    report = Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=ref("1", "Ocean warming trends"),
                status=RefStatus.unchecked,
                matched=w,
                providers=failed,
                note="identified via openalex; cited DOI not in Crossref/OpenAlex, "
                "not confirmed by doi.org",
            )
        ],
        claims=[
            ClaimCheck(
                citation_id="c1",
                ref_id="1",
                sentence="Oceans warmed by 2 degrees [1].",
                verdict=Verdict.supported,
                quote="the oceans warmed by 2 degrees over the period",
                evidence_kind=EvidenceKind.abstract,
            )
        ],
    )
    out = render(report)
    # shown open in the middle group, with a note about the reference
    assert "Oceans warmed" not in collapsed(out["claims"])
    assert "Oceans warmed" in text(out["claims"])
    assert "The cited reference could not be confirmed." in text(out["claims"])


# ---------- streaming: partial snapshots rendered while the job runs ----------


def audit(i: str, status: RefStatus) -> ReferenceAudit:
    return ReferenceAudit(reference=ref(i, f"Title {i}"), status=status, providers=ok)


def partial(*audits: ReferenceAudit, complete=False) -> dict:
    return Report(id="x", references=list(audits), complete=complete).model_dump(mode="json")


def running(stage: str) -> dict:
    return {"job": {"status": "running", "stage": stage, "done": 1, "total": 3}}


def ref_order(out) -> list[str]:
    return re.findall(r"ref (\d+)", text(out["refs"]))


def test_partial_report_counts_only_what_is_done_and_marks_sections():
    both = partial(audit("1", RefStatus.not_found), audit("2", RefStatus.verified))
    out = render_calls([[both, running("references")]])
    summary = text(out["summary"])
    assert summary.startswith("So far: 1 of 2 references needs attention"), summary
    assert "no claims checked yet" in summary
    assert "No citing sentences were found" not in summary  # not a final statement yet
    assert out["refs-wait"]["hidden"] is False and out["claims-wait"]["hidden"] is False
    assert "Still checking" in (JS.parent / "index.html").read_text()
    assert out["live"]["hidden"] is False and out["report"]["cls"] == "running"
    # stages overlap: at "claims" the references are still running until t_references_end
    out = render_calls([[partial(audit("1", RefStatus.not_found)), running("claims")]])
    assert out["refs-wait"]["hidden"] is False
    # references are final once every audit finished (t_references_end in the partial timings)
    done = partial(audit("1", RefStatus.not_found)) | {"timings": {"t_references_end": 1.0}}
    out = render_calls([[done, running("claims")]])
    assert out["refs-wait"]["hidden"] is True and out["claims-wait"]["hidden"] is False


def test_streamed_items_keep_arrival_order_until_the_run_finishes():
    # severity order would put not_found (ref 2) above metadata_mismatch (ref 1)
    a1, a2 = audit("1", RefStatus.metadata_mismatch), audit("2", RefStatus.not_found)
    out = render_calls(
        [[partial(a1), running("references")], [partial(a1, a2), running("sources")]]
    )
    assert ref_order(out) == ["1", "2"]
    out = render_calls([[partial(a1), running("references")], [partial(a1, a2, complete=True)]])
    # once final: sorted worst first, the same order the permalink of this report shows
    assert ref_order(out) == ["2", "1"]
    assert out["claims-wait"]["hidden"] is True and out["report"]["cls"] == ""
    assert text(out["live-stage"]) == "Audit complete."
    assert not text(out["summary"]).startswith("So far")
    # a report loaded already finished is still sorted worst first
    assert ref_order(render_calls([[partial(a1, a2, complete=True)]])) == ["2", "1"]


def test_opened_group_stays_open_across_partial_renders():
    v1, v2 = audit("1", RefStatus.verified), audit("2", RefStatus.verified)
    out = render_calls(
        [
            [partial(v1), running("references")],
            'openKeys.add("fine-references")',  # the reader opened the collapsed group
            [partial(v1, v2), running("references")],
        ]
    )
    fine = [k for k in out["refs"]["kids"] if k["cls"] == "fine"][0]
    assert "open" in fine["attrs"] and "2 verified references" in text(fine)


def test_saved_sample_is_labelled_as_saved():
    r = Report(id="sample", references=[audit("1", RefStatus.verified)])
    r.created_at = r.created_at.replace(year=2026, month=11, day=13, hour=12)
    out = render_calls([[r.model_dump(mode="json"), {"saved": True}]])
    assert out["saved"]["hidden"] is False and out["report"]["cls"] == "saved"
    assert text(out["saved-note"]) == (
        "Saved example — computed on 13 November 2026; results may differ in a live run."
    )
    assert out["live"]["hidden"] is True


# ---------- progressive disclosure, filters, readout ----------


def find(n, pred) -> list:
    if isinstance(n, str):
        return []
    return ([n] if pred(n) else []) + [x for k in n["kids"] for x in find(k, pred)]


def visible(n) -> str:
    """Text shown without opening any <details> (summaries stay visible)."""
    if isinstance(n, str):
        return n
    if n["tag"] == "details" and "open" not in n["attrs"]:
        return "".join(
            visible(k) for k in n["kids"] if not isinstance(k, str) and k["tag"] == "summary"
        )
    return "".join(visible(k) for k in n["kids"])


def two_claims(**kw) -> dict:
    w = Work(provider="crossref", title="Deep residual learning", year=2005)
    judge = ModelOpinion(
        verdict="unsupported",
        rationale="The source reports a different depth. It never says 1000 layers.",
    )
    skeptic = ModelOpinion(verdict="unsupported", rationale="SKEPTIC-ONLY rationale text.")
    claims = [
        ClaimCheck(
            citation_id="c1",
            ref_id="1",
            sentence="Residual nets train 1000 layers [1].",
            verdict=Verdict.unsupported,
            quote="we train residual networks with depth of 152 layers",
            evidence_kind=EvidenceKind.full_text,
            judge=judge,
            skeptic=skeptic,
        ),
        ClaimCheck(
            citation_id="c2",
            ref_id="1",
            sentence="Residual learning eases training [1].",
            verdict=Verdict.supported,
            quote="residual learning framework to ease the training of networks",
            evidence_kind=EvidenceKind.abstract,
        ),
    ]
    refs = [
        ReferenceAudit(
            reference=ref("1", "Deep residual learning"),
            status=RefStatus.verified,
            matched=w,
            providers=ok,
        )
    ]
    return Report(id="x", references=refs, claims=claims, **kw).model_dump(mode="json")


def test_claim_row_shows_one_reason_and_hides_rationales_behind_details():
    out = render_calls([[two_claims()]])
    shown = visible(out["claims"])
    assert "Residual nets train 1000 layers" in shown
    assert "we train residual networks with depth of 152 layers" in shown
    assert "Not supported" in shown
    # one sentence of reason by default; the rest and the skeptic only behind "Details"
    assert "The source reports a different depth." in shown
    assert "It never says 1000 layers" not in shown and "SKEPTIC-ONLY" not in shown
    assert "SKEPTIC-ONLY" in text(out["claims"])
    (more,) = [d for d in find(out["claims"], lambda n: n["cls"] == "more") if "SKEPTIC" in text(d)]
    assert text(find(more, lambda n: n["tag"] == "summary")[0]) == "Details"


def test_opened_details_survive_a_stream_update():
    key = "why-c:c1:1"
    out = render_calls(
        [
            [two_claims(complete=False), running("claims")],
            f'openKeys.add("{key}")',
            [two_claims(complete=False), running("claims")],
        ]
    )
    (d,) = find(out["claims"], lambda n: n["attrs"].get("data-key") == key)
    assert "open" in d["attrs"]


def test_counters_are_toggle_buttons_that_filter_the_ledger():
    out = render_calls([[two_claims()]])
    buttons = find(out["meters"], lambda n: n["tag"] == "button")
    by = {b["attrs"]["data-filter"]: b for b in buttons}
    assert {"c:unsupported", "c:supported", "r:verified"} <= set(by)
    assert all(b["attrs"]["aria-pressed"] == "false" for b in buttons)
    assert text(by["c:supported"]).startswith("1")
    out = render_calls([[two_claims()], 'filter = "c:supported"; renderReport(...lastRender);'])
    claims = text(out["claims"])
    assert "Residual learning eases training" in claims and "1000 layers" not in claims
    pressed = [
        b
        for b in find(out["meters"], lambda n: n["tag"] == "button")
        if b["attrs"]["aria-pressed"] == "true"
    ]
    assert [b["attrs"]["data-filter"] for b in pressed] == ["c:supported"]
    assert "Show all" in text(out["filter-note"])
    # references are not filtered by a claim filter
    assert "ref 1" in text(out["refs"])


def test_partial_report_shows_no_per_verdict_counts_and_timings_are_listed():
    timings = {"t_references_start": 1.0, "t_references_end": 3.5, "t_total": 9.0}
    out = render_calls([[two_claims(complete=False, timings=timings), running("claims")]])
    claims_meter = out["meters"]["kids"][0]
    assert not find(claims_meter, lambda n: n["tag"] == "button")
    assert "Counts by verdict appear when every claim is checked." in text(claims_meter)
    assert "2 / 3 checked" in text(claims_meter)  # job total for the running stage
    t = text(out["timings"])
    assert "Checking references2.5 s" in t and "Total9.0 s" in t


def test_reason_line_does_not_stop_at_et_al():
    out = render_calls(
        [
            [two_claims()],
            'lastRender[0].claims[0].judge.rationale = "Wu et al. 2016 say twice. Not +50.";'
            "renderReport(...lastRender);",
        ]
    )
    shown = visible(out["claims"])
    assert "Wu et al. 2016 say twice." in shown and "Not +50." not in shown


def missing_ref_report(verdict=Verdict.supported) -> dict:
    report = Report(
        id="x",
        references=[audit("1", RefStatus.not_found), audit("2", RefStatus.verified)],
        claims=[
            ClaimCheck(
                citation_id="c1",
                ref_id="1",
                sentence="Quantum folding guides songbirds [1].",
                verdict=verdict,
                quote="quantum folding of circadian proteins guides migration",
                evidence_kind=EvidenceKind.abstract,
            )
        ],
    )
    return report.model_dump(mode="json")


def test_claim_citing_a_missing_reference_needs_attention_and_is_never_green():
    tag = 'document.getElementById("refs-sec").className = "REFS";'
    out = render_calls([tag, [missing_ref_report()]])
    claims = out["claims"]
    attention = text(claims).split("Needs your attention", 1)
    assert len(attention) == 2 and "Quantum folding guides songbirds" in attention[1]
    assert "Quantum folding" not in collapsed(claims)
    assert "The cited reference was not found." in open_text(claims)
    marks = find(claims, lambda n: "mark" in n["cls"].split())
    assert marks and not any("s-ok" in m["cls"] for m in marks)
    # the worst finding leads: references first, and the headline names it in plain words
    assert out["ledgers"]["kids"][0]["cls"] == "REFS"
    assert text(out["headline"]) == "1 reference not found."


def test_headline_counts_say_when_they_overlap():
    r = Report.model_validate(two_claims())
    r.claims[0].needs_review = True
    out = render_calls([[r.model_dump(mode="json")]])
    assert text(out["headline"]) == (
        "1 claim not supported · 1 claim needs review (1 claim is in both counts)."
    )
    ok_only = Report.model_validate(two_claims())
    ok_only.claims = ok_only.claims[1:]
    out = render_calls([[ok_only.model_dump(mode="json")]])
    assert text(out["headline"]) == "No problems found in 1 claim and 1 reference."
