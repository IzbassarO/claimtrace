"""Morning cosmetics: no ruler once a section is checked, "Not enough evidence" group, the
missing-reference counter's sign, and the model name in a claim's Details."""

from test_web_disagreement import claim, report
from test_web_report_view import find, render_calls, running, text


def rulers(meter) -> list:
    return find(meter, lambda n: n["cls"].split()[:1] == ["ruler"])


def test_ruler_only_while_a_section_is_being_checked():
    r = report()
    done = render_calls([[r.model_dump(mode="json")]])
    for m in done["meters"]["kids"]:
        assert not rulers(m) and "done" in m["cls"].split()
    assert "5 / 5 checked" in text(done["meters"]) and "2 / 2 checked" in text(done["meters"])
    live = render_calls([[r.model_copy(update={"complete": False}).model_dump(mode="json")]])
    claims_meter = live["meters"]["kids"][0]
    assert rulers(claims_meter) and "done" not in claims_meter["cls"].split()
    live = render_calls(
        [[r.model_copy(update={"complete": False}).model_dump(mode="json"), running("claims")]]
    )
    assert rulers(live["meters"]["kids"][0])


def test_could_not_check_group_is_called_not_enough_evidence():
    r = report(bad_ref=False)
    r.claims.append(claim(6, "insufficient_evidence", "insufficient_evidence"))
    out = render_calls([[r.model_dump(mode="json")]])
    groups = [text(g) for g in find(out["claims"], lambda n: n["cls"] == "fine")]
    assert any(g.startswith("Not enough evidence") for g in groups), groups
    assert not any("Could not check" in g for g in groups)


def test_missing_reference_counter_uses_the_source_not_found_sign():
    out = render_calls([[report().model_dump(mode="json")]])
    (btn,) = [
        b
        for b in find(out["meters"]["kids"][0], lambda n: n["tag"] == "button")
        if "missing or retracted" in text(b)
    ]
    (m,) = find(btn, lambda n: "mark" in n["cls"].split())
    assert "s-unknown" in m["cls"].split() and "s-bad" not in m["cls"].split()


def test_details_name_the_model_when_the_opinion_has_one():
    data = report(bad_ref=False).model_dump(mode="json")
    data["claims"][0]["skeptic"]["model"] = "kimi-k2.7-code-highspeed"
    data["claims"][0]["judge"]["model"] = None  # older reports: absent or null -> nothing shown
    open_c1 = 'openKeys.add("row-c:c1:1"); openKeys.add("why-c:c1:1"); renderReport(...lastRender);'
    out = render_calls([[data], open_c1])
    h4s = [text(h) for h in find(out["claims"], lambda n: n["tag"] == "h4")]
    wells = {r: next(h for h in h4s if h.startswith(r)) for r in ("Judge", "Skeptic")}
    assert wells["Skeptic"].startswith("Skeptic · kimi-k2.7-code-highspeed")
    assert "·" not in wells["Judge"] and "None" not in wells["Judge"]
