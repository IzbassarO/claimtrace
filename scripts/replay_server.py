"""Dev-only: the real FastAPI app with `pipeline.run` replaced by a replay of a saved run.

Any submitted job streams the partial snapshots of `.cache/live/final-run.json` (references first,
then claims) on that run's own `timings` marks, squeezed into ~150 s. No LLM or network calls.

Also serves, for design screenshots only:
  /dev/show/<name>?theme=light|dark&open=1   the real page, with report <name> rendered into it
      names: home (no report), sample, final, empty, long, refs200
  /dev/frame?src=<path>&w=390                 the page inside a 390 px iframe (phone width)

Usage: uv run python scripts/replay_server.py [--port 8791] [--seconds 150] [--run PATH]
"""

import argparse
import asyncio
import html
import json
from pathlib import Path

import uvicorn
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from claimtrace import pipeline
from claimtrace.core.schemas import (
    ClaimCheck,
    EvidenceKind,
    Mismatch,
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

HERE = Path(__file__).resolve()


def find_run() -> Path:
    for d in HERE.parents:
        p = d / ".cache" / "live" / "final-run.json"
        if p.exists():
            return p
    raise SystemExit("no .cache/live/final-run.json found above this script; pass --run")


def make_replay(final: Report, seconds: float):
    t = final.timings
    scale = seconds / t["t_total"]
    refs, claims = final.references, final.claims
    # (original time, kind, index): one event per finished item, spread over its stage's marks
    events = [(0.0, "ingest", 0)]
    r0, r1 = t["t_references_first"], t["t_references_end"]
    events += [(r0 + (r1 - r0) * (i + 1) / len(refs), "ref", i + 1) for i in range(len(refs))]
    c0, c1 = t["t_claims_first"], t["t_claims_end"]
    events += [(c0 + (c1 - c0) * (j + 1) / len(claims), "claim", j + 1) for j in range(len(claims))]
    events.sort()

    async def run(text, files=None, on_progress=None, report_id=None, on_partial=None):
        progress = on_progress or (lambda *_: None)
        rid = report_id or "replay"
        nr = nc = 0
        now = 0.0
        for at, kind, n in events:
            await asyncio.sleep(max(0.0, (at - now) * scale))
            now = at
            if kind == "ingest":
                progress("ingest", 0, 1)
                continue
            if kind == "ref":
                nr = n
                progress("references", nr, len(refs))
            else:
                nc = n
                progress("claims", nc, len(claims))
            marks = {k: v for k, v in t.items() if k.startswith("t_") and v <= at}
            if on_partial:
                on_partial(
                    final.model_copy(
                        update={
                            "id": rid,
                            "references": refs[:nr],
                            "claims": claims[:nc],
                            "timings": marks,
                            "summary": {},
                            "complete": False,
                        }
                    )
                )
        return final.model_copy(update={"id": rid})

    return run


# ---------- synthetic reports for layout tests ----------

OK = [ProviderOutcome(provider="crossref", ok=True, candidates=3)]


def long_cases() -> Report:
    sentence = (
        "Across the twelve randomised cohorts pooled in the meta-analysis, which together enrolled "
        "more than eighteen thousand adult participants from community clinics on four continents "
        "and followed them for a median of seven years, daily low-dose supplementation reduced the "
        "incidence of first cardiovascular events by roughly a third relative to placebo, an "
        "effect "
        "that the authors describe as robust to every sensitivity analysis they attempted, "
        "including the exclusion of trials at high risk of bias and of industry-funded trials [7]."
    )
    quote = (
        "In the pooled analysis of all eligible trials the hazard ratio for a first major "
        "cardiovascular event was 0.91 (95% CI 0.84 to 0.99), corresponding to a relative "
        "reduction of nine percent; the estimate was attenuated and no longer statistically "
        "significant after the exclusion of trials judged to be at high risk of bias, and the "
        "heterogeneity between trials was moderate (I² = 41%), so that the evidence for a "
        "reduction of the size reported in earlier observational studies remains weak, and the "
        "absolute benefit in a primary-prevention population is small relative to the increased "
        "risk of major bleeding observed in the same trials over the same follow-up period."
    )
    url = "https://example.org/" + "a-very-long-unbroken-path-segment-" * 6 + "fulltext.pdf"
    raw = (
        "[7] Abernathy-Lindqvist, M. A., Oyelaran-Featherstonehaugh, T., Szczepańska-Kowalczyk, "
        "J., et al. Low-dose supplementation for the primary prevention of cardiovascular events "
        "in adults: a systematic review and individual-participant-data meta-analysis of "
        "randomised trials with long-term follow-up. Journal of Long Titles in Cardiovascular "
        "Epidemiology and Preventive Medicine. 2019;112(14):3341-3389. "
        "doi:10.1234/jltcepm.2019.112.14.3341-3389.supplementary-appendix-version-2"
    )
    ref = Reference(id="7", raw=raw, title="Low-dose supplementation", year=2019)
    audit = ReferenceAudit(
        reference=ref,
        status=RefStatus.metadata_mismatch,
        matched=Work(
            provider="crossref",
            title="Low-dose supplementation for the primary prevention of cardiovascular events",
            year=2021,
            doi="10.1234/jltcepm.2019.112.14.3341-3389.supplementary-appendix-version-2",
        ),
        mismatches=[Mismatch(field="year", cited="2019", found="2021")],
        providers=OK,
    )
    judge = ModelOpinion(verdict="partly_supported", quote=quote, rationale=quote)
    skeptic = ModelOpinion(verdict="unsupported", quote=quote[:200], rationale=sentence)
    claim = ClaimCheck(
        citation_id="c1",
        ref_id="7",
        sentence=sentence,
        verdict=Verdict.unsupported,
        quote=quote,
        evidence_kind=EvidenceKind.full_text,
        source_url=url,
        section="Results: pooled analysis, sensitivity analyses and heterogeneity between trials",
        needs_review=True,
        judge=judge,
        skeptic=skeptic,
    )
    return Report(
        id="long",
        title="A manuscript whose title is long enough to wrap onto a second or third line on a "
        "phone: supplementation, cardiovascular outcomes and the limits of meta-analysis",
        references=[audit],
        claims=[claim],
        warnings=["Citation c9: unresolved marker [99] " + "x" * 120],
    )


def refs200() -> Report:
    cycle = [RefStatus.verified] * 7 + [
        RefStatus.metadata_mismatch,
        RefStatus.unchecked,
        RefStatus.not_found,
    ]
    audits = []
    for i in range(1, 201):
        st = cycle[i % len(cycle)]
        ref = Reference(id=str(i), raw=f"[{i}] Author {i}, B. Synthetic study number {i}. J. 2020.")
        failed = st == RefStatus.unchecked
        audits.append(
            ReferenceAudit(
                reference=ref,
                status=st,
                matched=Work(provider="openalex", title=f"Synthetic study number {i}", year=2020)
                if st in (RefStatus.verified, RefStatus.metadata_mismatch)
                else None,
                mismatches=[Mismatch(field="year", cited="2020", found="2018")]
                if st == RefStatus.metadata_mismatch
                else [],
                providers=[ProviderOutcome(provider="crossref", ok=not failed, error="timeout")]
                if failed
                else OK,
            )
        )
    verdicts = [Verdict.supported] * 3 + [Verdict.partly_supported, Verdict.insufficient_evidence]
    claims = [
        ClaimCheck(
            citation_id=f"c{j}",
            ref_id=str(j * 7),
            sentence=f"Synthetic claim {j} says something about study {j * 7} [{j * 7}].",
            verdict=verdicts[j % len(verdicts)],
            quote=f"the synthetic study number {j * 7} reports this finding"
            if verdicts[j % len(verdicts)] != Verdict.insufficient_evidence
            else None,
            evidence_kind=EvidenceKind.abstract,
        )
        for j in range(1, 29)
    ]
    return Report(
        id="refs200", title="Synthetic report with 200 references", references=audits, claims=claims
    )


def reports(final: Report) -> dict[str, Report]:
    static = Path(web.__file__).parent / "static"
    return {
        "sample": Report.model_validate_json((static / "sample-report.json").read_text()),
        "final": final,
        "empty": Report(id="empty", warnings=["No reference section found.", "No citations."]),
        "long": long_cases(),
        "refs200": refs200(),
    }


SHOW = """<script>
(() => {
  const q = new URLSearchParams(location.search);
  if (q.get("theme")) document.documentElement.dataset.theme = q.get("theme");
  const name = %s;
  if (!name) return;
  addEventListener("load", async () => {
    const r = await (await fetch("/dev/data/" + name + ".json")).json();
    if (name === "sample") renderReport(r, { saved: true });
    else { jobId = "demo"; renderReport(r); }
    if (q.get("open")) document.querySelector(".ledger .more")?.setAttribute("open", "");
  });
})();
</script></body>"""


def add_dev_routes(data: dict[str, Report]) -> None:
    page = (Path(web.__file__).parent / "static" / "index.html").read_text()

    @web.app.get("/dev/data/{name}.json")
    async def dev_data(name: str):
        if name not in data:
            raise HTTPException(404)
        return JSONResponse(data[name].model_dump(mode="json"))

    @web.app.get("/dev/show/{name}")
    async def dev_show(name: str):
        if name != "home" and name not in data:
            raise HTTPException(404)
        js_name = json.dumps(None if name == "home" else name)
        return HTMLResponse(page.replace("</body>", SHOW % js_name))

    @web.app.get("/dev/frame")
    async def dev_frame(src: str, w: int = 390, h: int = 4000):
        if not src.startswith("/dev/show/"):
            raise HTTPException(400)
        src = html.escape(src, quote=True)
        return HTMLResponse(
            f'<!doctype html><body style="margin:0;background:#888">'
            f'<iframe src="{src}" style="border:0;display:block;width:{w}px;height:{h}px">'
            f"</iframe></body>"
        )

    # dev routes must win over the catch-all static mount registered by the app
    web.app.router.routes.sort(key=lambda r: getattr(r, "path", "").startswith("/static"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--seconds", type=float, default=150.0)
    ap.add_argument("--run", type=Path, default=None)
    args = ap.parse_args()
    final = Report.model_validate(json.loads((args.run or find_run()).read_text())["report"])
    pipeline.run = make_replay(final, args.seconds)  # web.app calls pipeline.run(...) at job time
    add_dev_routes(reports(final))
    uvicorn.run(web.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
