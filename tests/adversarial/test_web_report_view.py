"""The report page (app.js renderReport) misleads a reader skimming the summary and open groups.

Runs the real app.js in node with a tiny fake DOM (no network, no browser); skipped without node.
"""

import json
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
  dump() { return { tag: this.tag, cls: this.className, kids: this.kids.map((k) => k.dump()) }; }
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
ctx.renderReport(JSON.parse(fs.readFileSync(0, "utf8")));
const out = {};
for (const id of ["summary", "refs", "claims"]) out[id] = ids[id].dump();
process.stdout.write(JSON.stringify(out));
"""


def render(report: Report) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    r = subprocess.run(
        [node, "-e", HARNESS, str(JS)],
        input=report.model_dump_json(),
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
    assert "Reference metadata does not match the cited work" in shown
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
    assert "unchecked" in shown
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
    assert "Unchecked (lookup failed)" in shown
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
