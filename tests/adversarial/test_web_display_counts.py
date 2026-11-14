"""Display wave (#129/#131): the headline, the counters, the filters, the Markdown export and the
chat prompt must count the same claims the same way. A claim citing a missing reference is
counted as "cite a missing reference" by the counters, but the headline still counts it as
"needs review" / "not supported", the filter for that word finds nothing, and the Markdown and
the chat prompt count it (or a disagreement) under yet another word."""

import json
import re

from test_web_disagreement import claim, render, rows
from test_web_report_view import find, ok, ref, text

from claimtrace import pipeline
from claimtrace.core.schemas import Manuscript, ReferenceAudit, RefStatus, Report
from claimtrace.web import chat, export


def refs() -> list[ReferenceAudit]:
    return [
        ReferenceAudit(reference=ref("1", "Real"), status=RefStatus.verified, providers=ok),
        ReferenceAudit(reference=ref("9", "Ghost"), status=RefStatus.not_found, providers=ok),
    ]


def counters(out) -> dict[str, int]:
    """'1 need review' -> {'need review': 1}, from the claim counter buttons."""
    got = {}
    for b in find(out["meters"]["kids"][0], lambda n: n["tag"] == "button"):
        n, word = text(b).split(" ", 1)
        got[word] = int(n)
    return got


def shown(r: Report, f: str) -> list[str]:
    out = render(r, f'filter = "{f}"; renderReport(...lastRender);')
    return sorted(set(re.findall(r"Sentence number (\d)", text(out["claims"]))))


def test_missing_reference_plus_disagreement_counts_the_same_in_headline_counters_filter():
    # c1 cites a reference that was not found AND the two readers disagree on it
    r = Report(
        id="x",
        references=refs(),
        claims=[claim(1, "supported", "unsupported", rid="9"), claim(2, "supported", "supported")],
    )
    out = render(r)
    head = text(out["headline"])  # "1 reference not found · 1 claim needs review."
    row = next(x for x in rows(out["claims"]) if "Sentence number 1" in text(x))
    labelled_review = "Needs review" in text(row)
    in_headline = re.search(r"(\d+) claims? needs? review", head)
    # the headline promises N claims needing review: the counters and the filter must show them
    if in_headline:
        assert counters(out).get("need review") == int(in_headline[1]), (head, counters(out))
    # a row labelled "Needs review" must be found by the "need review" filter
    if labelled_review:
        assert shown(r, "c:review") == ["1"]


def test_missing_reference_plus_agreed_unsupported_counts_the_same_in_headline_and_counters():
    r = Report(
        id="x",
        references=refs(),
        claims=[
            claim(1, "unsupported", "unsupported", rid="9"),
            claim(2, "supported", "supported"),
        ],
    )
    out = render(r)
    head = text(out["headline"])  # "1 reference not found · 1 claim not supported."
    m = re.search(r"(\d+) claims? not supported", head)
    if m:  # the headline counts it as not supported: so must the counter and the filter
        assert counters(out).get("not supported") == int(m[1]), (head, counters(out))
        assert shown(r, "c:unsupported") == ["1"]


def md_claim_counts(r: Report) -> dict[str, int]:
    line = next(x for x in export.to_markdown(r).splitlines() if x.startswith("- Claims:"))
    parts = (p.split(":") for p in line[len("- Claims:") :].split(","))
    return {k.strip(): int(v) for k, v in parts}


def test_markdown_claim_counts_match_the_screen_counters():
    # c1: supported, but cites a reference that was not found; c2: plainly supported
    r = Report(
        id="x",
        references=refs(),
        claims=[claim(1, "supported", "supported", rid="9"), claim(2, "supported", "supported")],
    )
    screen, md = counters(render(r)), md_claim_counts(r)
    assert md.get("supported") == screen.get("supported"), (md, screen)  # md 2, screen 1
    # missing reference + disagreement: screen "cite a missing reference", md "needs review"
    r.claims[0] = claim(1, "supported", "unsupported", rid="9")
    screen, md = counters(render(r)), md_claim_counts(r)
    assert md.get("needs review", 0) == screen.get("need review", 0), (md, screen)


def test_chat_prompt_summary_does_not_count_a_disagreement_as_unsupported():
    # c1 agreed unsupported; c2 judge supported vs skeptic unsupported (needs review)
    checks = [claim(1, "unsupported", "unsupported"), claim(2, "supported", "unsupported")]
    r = pipeline._report("x", Manuscript(body=""), refs()[:1], checks, complete=True)
    sent = json.loads(chat.report_json(r))
    # the UI says "1 claim not supported · 1 needs review"; the prompt's precomputed summary must
    # not hand the model "unsupported: 2" (it answers "how many are not supported?" from it)
    assert sent["summary"].get("claims", {}).get("unsupported", 0) <= 1, sent["summary"]


def test_js_and_python_claim_classification_agree_on_every_combination():
    # app.js claimKey and export.claim_key must be the same rule: every reference status x
    # (agreed verdicts, disagreements, one model, no model)
    from test_web_report_view import render_calls

    from claimtrace.core.schemas import ClaimCheck, ModelOpinion, Verdict

    statuses = list(RefStatus)
    audits = [
        ReferenceAudit(reference=ref(str(i), f"T{i}"), status=s, providers=ok)
        for i, s in enumerate(statuses)
    ]
    verdicts = ["supported", "partly_supported", "unsupported", "insufficient_evidence"]
    pairs = [(j, s) for j in verdicts for s in verdicts] + [(v, None) for v in verdicts]
    pairs += [(None, None)]
    claims = []
    for i, _ in enumerate(statuses):
        for k, (j, s) in enumerate(pairs):
            ops = [o for o in (j, s) if o]
            claims.append(
                ClaimCheck(
                    citation_id=f"c{i}-{k}",
                    ref_id=str(i),
                    sentence="S.",
                    verdict=Verdict(max(ops, key=verdicts.index) if ops else "source_not_found"),
                    judge=ModelOpinion(verdict=j) if j else None,
                    skeptic=ModelOpinion(verdict=s) if s else None,
                )
            )
    r = Report(id="x", references=audits, claims=claims)
    js = render_calls(
        [
            [r.model_dump(mode="json")],
            '$("headline").textContent = JSON.stringify(lastRender[0].claims.map(claimKey));',
        ]
    )
    status = {a.reference.id: a.status.value for a in audits}
    assert json.loads(text(js["headline"])) == [export.claim_key(c, status) for c in claims]
    assert {"badref", "review", "unsupported", "supported"} <= set(json.loads(text(js["headline"])))
