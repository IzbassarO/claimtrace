"""Empty report (no references, no claims): one 'nothing found' screen, not empty sections + chat.

Runs the real app.js in node with a tiny fake DOM (same approach as
tests/adversarial/test_web_report_view.py); skipped without node.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from claimtrace.core.schemas import ProviderOutcome, Reference, ReferenceAudit, RefStatus, Report
from claimtrace.web import app as web

STATIC = Path(web.__file__).parent / "static"

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
class Node_ {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.className = "";
    this.hidden = false; this.on = {}; }
  append(...cs) { for (const c of cs) this.kids.push(typeof c === "string" ? new Text_(c) : c); }
  replaceChildren(...cs) { this.kids = []; this.append(...cs); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(ev, fn) { this.on[ev] = fn; }
  focus() {}
  get textContent() { return this.kids.map((k) => k.textContent).join(""); }
  set textContent(v) { this.kids = [new Text_(String(v))]; }
  dump() { return { tag: this.tag, cls: this.className, click: !!this.on.click,
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
ctx.renderReport(JSON.parse(fs.readFileSync(0, "utf8")));
const out = {};
for (const id of ["results-h", "empty", "report", "results", "input"])
  out[id] = { hidden: ids[id].hidden, ...ids[id].dump() };
process.stdout.write(JSON.stringify(out));
"""


def render(report: Report) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    r = subprocess.run(
        [node, "-e", HARNESS, str(STATIC / "app.js")],
        input=report.model_dump_json(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def text(n) -> str:
    return n if isinstance(n, str) else "".join(text(k) for k in n["kids"])


def find(n, tag):
    if isinstance(n, str):
        return []
    return ([n] if n["tag"] == tag else []) + [x for k in n["kids"] for x in find(k, tag)]


def test_empty_report_shows_one_not_found_screen():
    warnings = ["No reference section found.", "Citation c1: unresolved [1]"]
    out = render(Report(id="x", warnings=warnings))
    assert out["results"]["hidden"] is False
    assert (
        out["report"]["hidden"] is True
    )  # summary, sections, exports and chat all live in #report
    assert out["empty"]["hidden"] is False
    assert "No reference list found" in text(out["results-h"])
    empty = out["empty"]
    assert "Works cited" in text(empty)
    (pre,) = find(empty, "pre")
    assert text(pre).startswith("References\n[1] ")
    (button,) = find(empty, "button")
    assert text(button) == "Audit another manuscript" and button["click"]
    # warnings only inside the collapsed technical details
    (details,) = find(empty, "details")
    assert text(find(details, "summary")[0]) == "Technical details (2)"
    outside = text(empty).replace(text(details), "")
    for w in warnings:
        assert w in text(details) and w not in outside


def test_chat_lives_inside_the_hidden_report_block():
    html = (STATIC / "index.html").read_text()
    report, chat, end = html.index('id="report"'), html.index('id="chat"'), html.index("</main>")
    assert report < html.index('id="summary"') < chat < end
    assert html.index('id="empty"') < report


def test_references_without_claims_keep_the_normal_report():
    ref = Reference(id="1", raw="[1] Author A. Title. J. 2005.", title="Title", year=2005)
    audit = ReferenceAudit(
        reference=ref,
        status=RefStatus.unchecked,
        providers=[ProviderOutcome(provider="crossref", ok=False, error="timeout")],
    )
    out = render(Report(id="x", references=[audit]))
    assert out["report"]["hidden"] is False
    assert out["empty"]["hidden"] is True
    assert "No reference list found" not in text(out["results-h"])
