"""Print / "PDF (print)": the printed copy must hold every claim with its full quote, whatever the
reader collapsed, filtered or clamped on screen, and building it must not change the screen.

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

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
class Node_ {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.className = ""; }
  append(...cs) { for (const c of cs) this.kids.push(typeof c === "string" ? new Text_(c) : c); }
  replaceChildren(...cs) { this.kids = []; this.append(...cs); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  focus() {}
  addEventListener() {}
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
// stdin: [report, setup JS run before renderReport]
const [report, setup] = JSON.parse(fs.readFileSync(0, "utf8"));
vm.runInContext(setup, ctx);
ctx.renderReport(report);
const out = { openKeys: vm.runInContext("[...openKeys].sort()", ctx) };
for (const id of ["claims", "refs", "print", "print-report"])
  out[id] = document.getElementById(id).dump();
process.stdout.write(JSON.stringify(out));
"""


def run(report: Report, setup: str = "") -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    r = subprocess.run(
        [node, "-e", HARNESS, str(JS)],
        input=json.dumps([report.model_dump(mode="json"), setup]),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def text(n) -> str:
    return n if isinstance(n, str) else "".join(text(k) for k in n["kids"])


def find(n, cls: str) -> list:
    if isinstance(n, str):
        return []
    return ([n] if cls in n["cls"].split() else []) + [x for k in n["kids"] for x in find(k, cls)]


ok = [ProviderOutcome(provider="crossref", ok=True, candidates=1)]
LONG = " ".join(f"word{i}" for i in range(120))  # far past the 3-line on-screen clamp


def big_report() -> Report:
    refs = [
        ReferenceAudit(
            reference=Reference(id="1", raw="[1] A. Paper. 2020.", title="Paper", year=2020),
            status=RefStatus.verified,
            matched=Work(provider="crossref", title="Paper", year=2020),
            providers=ok,
        )
    ]
    verdicts = [Verdict.unsupported] * 8 + [Verdict.partly_supported] * 6 + [Verdict.supported] * 4
    verdicts += [Verdict.insufficient_evidence] * 3
    claims = [
        ClaimCheck(
            citation_id=f"c{i}",
            ref_id="1",
            sentence=f"Citing sentence number {i} [1].",
            verdict=v,
            quote=None if v == Verdict.insufficient_evidence else f"quote {i} {LONG} end{i}",
            evidence_kind=EvidenceKind.full_text,
            needs_review=i == 0,
            # "needs review" means the two readers disagree (claim 0 only)
            judge=ModelOpinion(verdict=v.value) if i == 0 else None,
            skeptic=ModelOpinion(verdict="partly_supported") if i == 0 else None,
            source_url="https://example.org/paper.pdf",
        )
        for i, v in enumerate(verdicts)
    ]
    return Report(id="big", title="Big report", references=refs, claims=claims)


def test_print_holds_every_claim_with_its_full_quote():
    r = big_report()
    out = run(r)
    rows = find(out["print-report"], "p-row")
    assert len(rows) == len(r.claims) == 21
    printed = text(out["print-report"])
    for c in r.claims:
        assert c.sentence in printed
        if c.quote:
            assert c.quote in printed  # whole quote, not clamped
    # on screen: only the first 5 attention rows, other groups collapsed, one row body open
    assert "Show all 8" in text(out["claims"])
    assert not any("clamp" in q["cls"] for q in find(out["print-report"], "quote"))
    # source link is a real link the print CSS can append its URL to; tags survive
    srcs = find(out["print-report"], "src")
    assert srcs and srcs[0]["attrs"]["href"] == "https://example.org/paper.pdf"
    assert "needs review" in printed
    assert "Big report" in printed and "Shareable link: http://x/r/" in printed


def test_print_ignores_filter_and_leaves_the_screen_as_it_was():
    r = big_report()
    setup = 'filter = "c:supported"; openKeys.add("row-c:c15:1");'
    out = run(r, setup)
    # the print copy has everything although the screen is filtered to 4 supported claims
    assert len(find(out["print-report"], "p-row")) == 21
    assert "Citing sentence number 0 " not in text(out["claims"])
    # building the print copy changes nothing on screen: same DOM and open state as without it
    bare = run(r, setup + "printView = () => [];")
    assert out["claims"] == bare["claims"]
    assert out["refs"] == bare["refs"]
    assert out["openKeys"] == bare["openKeys"]


def test_empty_report_hides_the_print_copy():
    out = run(Report(id="e", warnings=["No reference section found."]))
    assert out["print"]["hidden"] is True
