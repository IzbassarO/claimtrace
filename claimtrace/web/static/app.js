"use strict";
// All report data is untrusted (manuscript, provider records, model output):
// it is only ever written with text nodes / textContent / setAttribute, never parsed as HTML.

const $ = (id) => document.getElementById(id);

const STAGES = [
  ["ingest", "Reading the manuscript"],
  ["references", "Checking references"],
  ["sources", "Fetching cited sources"],
  ["claims", "Checking claims against sources"],
];
// value -> [label, level]; level picks the shape (see .s-* in style.css), order = severity
const STATUS = {
  not_found: ["Not found", "bad"],
  retracted: ["Retracted", "bad"],
  metadata_mismatch: ["Details differ", "mismatch"],
  unchecked: ["Could not check", "unknown"],
  verified: ["Verified", "ok"],
};
const VERDICT = {
  unsupported: ["Not supported", "bad"],
  partly_supported: ["Partly supported", "partly"],
  source_not_found: ["Source not found", "unknown"],
  insufficient_evidence: ["Not enough evidence", "unknown"],
  supported: ["Supported", "ok"],
};

let jobId = null;
let chatHistory = [];
const BACKOFF_S = [2, 4, 8, 16, 30]; // ~1 min of retries before showing the Retry button
let failures = 0;
// Streaming: partial reports render while the job runs. Items keep their arrival order (no
// re-sorting under the reader's eyes); `seen` maps item key -> arrival index.
let streamed = false;
let seen = new Map();
let lastShown = "";
const openKeys = new Set(); // <details data-key> the reader opened, kept open across re-renders

// el("p", {className: "x"}, "text", childNode, null) -> element; strings become text nodes.
function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "className") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === "") continue;
    node.append(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return node;
}

// Only http(s) links; anything else (javascript:, data:) is shown as plain text.
function link(url, text) {
  try {
    const u = new URL(url);
    if (u.protocol === "https:" || u.protocol === "http:") {
      return el("a", { href: u.href, target: "_blank", rel: "noopener noreferrer" }, text || u.href);
    }
  } catch (_) { /* not a URL */ }
  return el("span", {}, text || String(url));
}

// Status word + CSS shape (+ colour); verdict words also get their own underline style.
function mark(value, labels, label, levelOverride) {
  const [text, defaultLevel] = labels[value] || [value, "unknown"];
  const level = levelOverride || defaultLevel;
  const v = labels === VERDICT ? ` v u-${value}` : "";
  return el("span", { className: `mark s-${level}${v}` }, el("span", { className: "word" }, label || text));
}

const rank = (labels, value) => { const i = Object.keys(labels).indexOf(value); return i < 0 ? 0 : i; };
// worst first: a claim citing a missing/retracted reference, then verdict/status, then needs
// review, then the cited reference's status
const severity = (labels, key, x) => (!x.reference && refBad(x) ? -1000 : rank(labels, key(x)) * 100)
  + (x.reference ? 0 : (x.needs_review ? 0 : 10) + rank(STATUS, refStatus.get(x.ref_id) || "verified"));

async function apiError(resp) {
  try {
    const body = await resp.json();
    if (typeof body.detail === "string") return body.detail;
  } catch (_) { /* not JSON */ }
  return `Request failed (HTTP ${resp.status}).`;
}

function show(section) {
  for (const id of ["input", "progress", "results"]) $(id).hidden = id !== section;
}

function formMessage(text, kind = "error") {
  $("form-error").className = kind;
  $("form-error").textContent = text;
}

async function submitJob(ev) {
  ev.preventDefault();
  formMessage("");
  const form = new FormData($("job-form"));
  if (!form.get("text").trim() && !$("files").files.length) {
    formMessage("Paste manuscript text or choose at least one file.");
    return;
  }
  $("submit").disabled = true;
  try {
    const resp = await fetch("/api/jobs", { method: "POST", body: form });
    if (resp.status === 429 || resp.status === 503) {
      formMessage(`${await apiError(resp)} Your text is still here.`, "notice");
      describeServer(); // the daily count may have changed
      return;
    }
    if (!resp.ok) throw new Error(await apiError(resp));
    const { id } = await resp.json();
    history.replaceState(null, "", `/r/${encodeURIComponent(id)}`);
    startPolling(id);
  } catch (e) {
    formMessage(e.message);
  } finally {
    $("submit").disabled = false;
  }
}

// Saved example: a precomputed report of sample.txt, shown instantly (no job, no API cost).
async function trySample() {
  formMessage("");
  $("sample").disabled = true;
  try {
    const resp = await fetch("/static/sample-report.json");
    if (!resp.ok) throw new Error(`Could not load the sample (HTTP ${resp.status}).`);
    jobId = null; // the sample is no job: no polling, chat, export or share link for it
    clearView();
    renderReport(await resp.json(), { saved: true });
  } catch (e) {
    formMessage(e.message);
  } finally {
    $("sample").disabled = false;
  }
}

// Same flow as pasted text: fill the textarea (so the user sees what was sent) and submit.
async function runSampleLive() {
  formMessage("");
  show("input");
  try {
    const resp = await fetch("/static/sample.txt");
    if (!resp.ok) throw new Error(`Could not load the sample (HTTP ${resp.status}).`);
    selectTab("paste");
    $("text").value = await resp.text();
    $("files").value = "";
    showFiles();
    $("job-form").requestSubmit();
  } catch (e) {
    formMessage(e.message);
  }
}

// Per-report view state (stream order, opened disclosures, filter, chat) starts fresh per report.
function clearView() {
  chatHistory = [];
  streamed = false;
  seen = new Map();
  lastShown = "";
  openKeys.clear();
  filter = null;
  $("chat-log").replaceChildren();
}

function startPolling(id) {
  jobId = id;
  clearView();
  show("progress");
  renderStages({ status: "queued" });
  resumePolling();
}

function resumePolling() {
  failures = 0;
  $("retry").hidden = true;
  $("conn").textContent = "";
  poll();
}

// Every reference audit has finished (stages overlap: sources/claims start while refs still run).
// The pipeline marks t_references_end in the partial timings only once done == total.
const refsDone = (r) => !!r && (r.complete !== false || "t_references_end" in (r.timings || {}));

// Stage checklist: done / current / to do, plus the live status line and progress bar.
function renderStages(job) {
  const cur = job.status === "queued" ? -1 : job.stage === "done" ? STAGES.length : STAGES.findIndex(([k]) => k === job.stage);
  const refsBusy = job.stage !== "done" && !refsDone(job.report);
  $("stages").replaceChildren(...STAGES.map(([key, label], i) => {
    const state = i < cur && !(key === "references" && refsBusy) ? "done" : i <= cur ? "current" : "todo";
    const count = i === cur && job.total ? ` ${job.done} of ${job.total}` : "";
    return el("li", { className: `st-${state}`, ...(i === cur ? { "aria-current": "step" } : {}) },
      label, count ? el("span", { className: "count" }, count) : null,
      el("span", { className: "sr-only" }, { done: " (done)", current: " (in progress)", todo: "" }[state]));
  }));
  // queue_position / queue_length may be absent (older server): fall back to the generic line
  const pos = Number.isInteger(job.queue_position) && job.queue_position > 0 ? job.queue_position : null;
  const len = Number.isInteger(job.queue_length) && job.queue_length >= pos ? job.queue_length : null;
  const label = job.status === "queued" ? (pos ? `Waiting in queue — position ${pos}${len ? ` of ${len}` : ""}` : "Queued: waiting for a free worker")
    : cur >= STAGES.length ? "Finishing" : (STAGES[cur] || [, "Working"])[1];
  for (const line of ["stage", "live-stage"]) $(line).textContent = job.total ? `${label}: ${job.done} of ${job.total}` : label;
  if (job.total) { $("bar").max = job.total; $("bar").value = job.done; } else $("bar").removeAttribute("value");
}

async function poll() {
  const id = jobId;
  let job;
  let busy = false;
  try {
    const resp = await fetch(`/api/jobs/${encodeURIComponent(id)}`);
    if (!resp.ok && resp.status < 500 && resp.status !== 429) {
      // a definite answer (e.g. unknown report): go back to the form
      const msg = await apiError(resp);
      if (id !== jobId) return;
      show("input");
      formMessage(msg);
      return;
    }
    busy = resp.status === 429 || resp.status === 503;
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    job = await resp.json();
  } catch (_) {
    // network or server trouble: keep the job id and retry with backoff
    if (id !== jobId) return;
    if (failures < BACKOFF_S.length) {
      $("conn").textContent = busy ? "The server is busy, retrying…" : "Connection lost, retrying…";
      setTimeout(poll, BACKOFF_S[failures++] * 1000);
    } else {
      $("conn").textContent = "Connection lost. The audit may still be running on the server.";
      $("retry").hidden = false;
    }
    return;
  }
  if (id !== jobId) return;
  failures = 0;
  $("conn").textContent = "";
  if (job.status === "done") return renderReport(job.report);
  if (job.status === "error") {
    show("input");
    formMessage(`The audit failed: ${job.error || "unknown error"}`);
    return;
  }
  renderStages(job);
  const r = job.report;
  if (r && (r.references.length || r.claims.length)) {
    const shown = JSON.stringify(r);
    if (shown !== lastShown) renderReport(r, { job });
    lastShown = shown;
  }
  setTimeout(poll, 1000);
}

// ref id -> audit, set by renderReport: a claim is only as good as the reference it cites
let refAudits = new Map();
const refStatus = { get: (id) => (refAudits.get(id) || {}).status };
const REF_NOTE = {
  retracted: "The cited source is retracted.",
  not_found: "The cited reference was not found.",
  unchecked: "The cited reference could not be confirmed.",
};
const differs = (a) => `Details differ from the found record${a.mismatches.length ? ` (${a.mismatches.map((x) => x.field).join(", ")})` : ""}.`;
const refNote = (c) => {
  const a = refAudits.get(c.ref_id);
  return !a ? null : a.status === "metadata_mismatch" ? `Cited reference: ${differs(a).charAt(0).toLowerCase()}${differs(a).slice(1)}` : REF_NOTE[a.status] || null;
};
// the cited work is missing or retracted: whatever the source text said, the citation is a problem
const refBad = (c) => ["not_found", "retracted"].includes(refStatus.get(c.ref_id));
const refAttention = (a) => a.status !== "verified";
// counter/filter value of a claim: one citing a missing or retracted reference is never counted
// under its (possibly green) verdict
const claimKey = (c) => (refBad(c) ? "badref" : c.verdict);
const claimAttention = (c) => c.verdict === "unsupported" || c.needs_review || refBad(c);
// partly supported, not checkable, or citing a doubtful reference: shown open, below the claims that need attention
const claimMiddle = (c) => !claimAttention(c) && (c.verdict !== "supported" || refNote(c) !== null);
const MIDDLE = "Partly supported, not checkable, or citing a doubtful reference";
const need = (n) => (n === 1 ? "needs" : "need");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
// first sentence; "et al." / "e.g." / "Fig." do not end one
const firstSentence = (s) => String(s || "").split(/(?<!\b(?:al|e\.g|i\.e|cf|vs|Fig|Eq|No)\.)(?<=[.!?])\s+(?=[A-Z0-9“"(])/)[0];

const CLAIM_COLS = ["Citation", "Citing sentence", "Source says", "Verdict"];
const REF_COLS = ["Reference", "Cited as", "Found", "Status"];
const cell = (cols, i, ...children) => el("div", { className: i === 0 ? "id" : i === 3 ? "verdict" : "" },
  el("span", { className: "cell-label" }, cols[i]), children);

// Reader state that survives the re-render on every streamed snapshot.
let filter = null; // "c:<verdict>", "c:review", "r:<status>" or null (show everything)
let lastRender = null; // [report, opts] for re-rendering when a filter changes
let fresh = new Set(); // keys that arrived in this render (briefly highlighted)
const filterMatch = (x) => (filter === "c:review" ? !!x.needs_review : filter.slice(2) === (x.reference ? x.status : claimKey(x)));

function rowEl(key, ...children) {
  return el("li", { className: fresh.has(key) ? "row new" : "row" }, children);
}

function foundLine(a) {
  const m = a.matched;
  const failed = a.providers.some((p) => !p.ok);
  // unchecked although every provider answered: the audit note says why (possible match, nothing to compare)
  const explained = a.status === "unchecked" && !failed && a.note;
  if (m) {
    return el("p", { className: "found" }, el("span", { className: "title" }, m.title || "(untitled)"),
      m.year ? ` (${m.year})` : "", ` · ${m.provider}`,
      m.doi ? [" · ", link(`https://doi.org/${m.doi}`, `doi:${m.doi}`)] : m.url ? [" · ", link(m.url, "record")] : null);
  }
  return el("p", { className: "found" }, a.status !== "unchecked" ? "No matching record was found."
    : failed ? "The lookup failed, so this reference could not be checked."
      : explained || "The providers answered, but this reference could not be confirmed or ruled out.");
}

function refReason(a) {
  const failed = a.providers.filter((p) => !p.ok).map((p) => p.provider);
  if (a.status === "metadata_mismatch" || a.mismatches.length) return differs(a);
  if (failed.length && a.status === "unchecked") return `${failed.join(", ")} did not answer; this is not a finding against the reference.`;
  if (a.status === "unchecked" && a.note && !failed.length) return null; // already the "found" text
  return a.note ? firstSentence(a.note) : null;
}

function fieldsTable(a) {
  if (!a.mismatches.length) return null;
  return el("table", { className: "fields" },
    el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Field"), el("th", { scope: "col" }, "Cited"),
      el("th", { scope: "col" }, `Found${a.matched ? ` (${a.matched.provider})` : ""}`))),
    el("tbody", {}, a.mismatches.map((x) => el("tr", {}, el("th", { scope: "row" }, x.field), el("td", {}, x.cited), el("td", {}, x.found)))));
}

function lookups(a) {
  const m = a.matched;
  return el("div", { className: "well" }, el("h4", {}, "Lookups"),
    el("ul", {}, a.providers.map((p) => el("li", {}, p.ok ? `${p.provider}: ${plural(p.candidates, "candidate")}`
      : `${p.provider} failed${p.error ? `: ${p.error}` : ""}`))),
    m && m.authors.length ? el("p", {}, `Found authors: ${m.authors.join("; ")}`) : null,
    m && m.venue ? el("p", {}, `Found venue: ${m.venue}`) : null,
    a.note ? el("p", {}, a.note) : null);
}

function refRow(a) {
  const ref = a.reference;
  const key = itemKey(a);
  const failed = a.providers.some((p) => !p.ok);
  const reason = refReason(a);
  const table = fieldsTable(a);
  return rowEl(key,
    cell(REF_COLS, 0, `ref ${ref.id}`),
    cell(REF_COLS, 1, el("p", { className: "cited" }, ref.raw)),
    cell(REF_COLS, 2, foundLine(a)),
    cell(REF_COLS, 3, mark(a.status, STATUS, a.status === "unchecked" && failed ? "Could not check (lookup failed)" : null)),
    reason ? el("p", { className: "reason" }, reason) : null,
    details(`ref-${key}`, { className: "more" }, el("summary", { "data-fk": `ref-${key}` }, "Details"),
      el("div", { className: "more-body" }, table ? el("div", { className: "well" }, el("h4", {}, "Fields"), table) : null, lookups(a))));
}

const itemKey = (x) => (x.reference ? `r:${x.reference.id}` : `c:${x.citation_id}:${x.ref_id}`);

// <details> that stays open across re-renders of a streaming report if the reader opened it.
function details(key, props, ...children) {
  return el("details", { ...props, "data-key": key, ...(openKeys.has(key) ? { open: "" } : {}) }, ...children);
}

function opinion(role, o, shown) {
  if (!o) return el("div", { className: "well" }, el("h4", {}, role), el("p", {}, "No answer (model call failed)."));
  return el("div", { className: "well" },
    el("h4", {}, role, mark(o.verdict, VERDICT)),
    o.rationale ? el("p", {}, o.rationale) : null,
    o.quote && o.quote !== shown ? el("p", { className: "quiet" }, `Other excerpt it quoted (not verified verbatim): “${o.quote}”`) : null);
}

// "Vaswani et al. 2017, Attention is all you need" from the cited reference's parsed fields
function refShort(id) {
  const a = refAudits.get(id);
  if (!a) return null;
  const r = a.reference;
  const who = r.authors.length ? `${r.authors[0].split(",")[0].trim().split(" ").at(-1)}${r.authors.length > 1 ? " et al." : ""}` : "";
  return [[who, r.year].filter(Boolean).join(" "), r.title].filter(Boolean).join(", ") || null;
}

function claimReason(c) {
  if (c.needs_review) return "The judge and the skeptic disagree; the more cautious verdict is shown.";
  if (c.note) return firstSentence(c.note);
  const o = [c.judge, c.skeptic].find((x) => x && x.verdict === c.verdict && x.rationale);
  return o ? firstSentence(o.rationale) : null;
}

function claimRow(c) {
  const key = itemKey(c);
  const kind = { full_text: "full text", abstract: "abstract" }[c.evidence_kind];
  const long = { full_text: "Checked against the full text", abstract: "Checked against the abstract only" }[c.evidence_kind];
  const note = refNote(c);
  const reason = claimReason(c);
  const a = refAudits.get(c.ref_id);
  const src = refShort(c.ref_id);
  return rowEl(key,
    cell(CLAIM_COLS, 0, `${c.citation_id} → ref ${c.ref_id}`, kind ? el("span", { className: "kind" }, kind) : null),
    cell(CLAIM_COLS, 1, el("p", { className: "sentence" }, c.sentence)),
    cell(CLAIM_COLS, 2, c.quote ? el("blockquote", { className: "quote" }, c.quote)
      : el("p", { className: "quiet" }, "No verbatim quote from the source backs a verdict."),
    src || c.source_url ? el("p", { className: "provenance" }, src, src && c.source_url ? " · " : "", c.source_url ? link(c.source_url, "source") : null) : null),
    cell(CLAIM_COLS, 3, refBad(c) ? mark(c.verdict, VERDICT, null, "unknown") : mark(c.verdict, VERDICT),
      c.needs_review ? el("span", { className: "mark s-review" }, el("span", { className: "word" }, "Needs review")) : null,
      note ? el("p", { className: "ref-note" }, note) : null),
    reason ? el("p", { className: "reason" }, reason) : null,
    details(`why-${key}`, { className: "more" }, el("summary", { "data-fk": `why-${key}` }, "Details"),
      el("div", { className: "more-body" },
        c.judge || c.skeptic ? [opinion("Judge", c.judge, c.quote), opinion("Skeptic", c.skeptic, c.quote)] : null,
        el("div", { className: "well" }, el("h4", {}, "Source"),
          el("p", {}, [long, c.section ? `§ ${c.section}` : null].filter(Boolean).join(" · ") || "No source text was read."),
          c.source_url ? el("p", {}, link(c.source_url)) : null,
          c.note ? el("p", {}, c.note) : null),
        a ? el("div", { className: "well" }, el("h4", {}, `Reference ${a.reference.id}`, mark(a.status, STATUS)),
          el("p", {}, a.reference.raw), fieldsTable(a)) : null)));
}

function ledger(cols, rows) {
  return [el("div", { className: "ledger-head", "aria-hidden": "true" }, cols.map((h) => el("span", {}, h))),
    el("ol", { className: "ledger" }, rows)];
}

// Problems first (worst first), everything fine collapsed in a <details>. With a filter on this
// section: one ledger of the matching rows only.
function group(items, isProblem, labels, key, render, noun, fineNoun, isMiddle, pending, cols, prefix, running) {
  const order = (xs) => xs.sort(running ? (a, b) => seen.get(itemKey(a)) - seen.get(itemKey(b))
    : (a, b) => severity(labels, key, a) - severity(labels, key, b) || seen.get(itemKey(a)) - seen.get(itemKey(b)));
  if (!items.length) return [el("p", { className: "none" }, pending ? `No ${noun} checked yet.` : `No ${noun} were found in this manuscript.`)];
  if (filter && filter.startsWith(prefix)) {
    const hits = order(items.filter(filterMatch));
    return hits.length ? ledger(cols, hits.map(render)) : [el("p", { className: "none" }, `No ${noun} match this filter.`)];
  }
  const bad = order(items.filter(isProblem));
  const mid = order(items.filter((x) => !isProblem(x) && isMiddle(x)));
  const fine = items.filter((x) => !isProblem(x) && !isMiddle(x));
  return [
    bad.length ? [el("h3", {}, "Needs your attention"), ledger(cols, bad.map(render))]
      : el("p", { className: "none" }, `No ${noun} need attention${pending ? " so far" : ""}.`),
    mid.length ? [el("h3", {}, `${MIDDLE} (${mid.length})`), ledger(cols, mid.map(render))] : null,
    fine.length ? details(`fine-${noun}`, { className: "fine" }, el("summary", { "data-fk": `fine-${noun}` }, `Show ${plural(fine.length, fineNoun)}`),
      ledger(cols, fine.map(render))) : null,
  ].flat(Infinity).filter(Boolean);
}

// One counter per status present; each is a toggle button that filters its section.
function counters(items, labels, prefix, valueOf, extras = []) {
  const n = new Map();
  for (const x of items) n.set(valueOf(x), (n.get(valueOf(x)) || 0) + 1);
  const entries = Object.keys(labels).filter((v) => n.get(v)).map((v) => [v, n.get(v), mark(v, labels, `${labels[v][0].toLowerCase()}`)]);
  entries.push(...extras.filter(Boolean));
  return el("ul", { className: "counts", "aria-label": "Filter by status" }, entries.map(([v, count, m]) => {
    const f = `${prefix}${v}`;
    return el("li", {}, el("button", { type: "button", "data-filter": f, "data-fk": `f:${f}`, "aria-pressed": String(filter === f) },
      el("span", { className: "num" }, String(count)), " ", m));
  }));
}

// Readout meter: one tick per item, pending ticks while the total is known but not reached.
function meter(name, items, total, done, countsNode, waitNote) {
  const n = items.length;
  const of = total && total > n ? total : done ? n : null;
  return el("div", { className: "meter" },
    el("p", { className: "meter-head" }, el("strong", {}, name),
      el("span", { className: "n" }, of !== null ? `${n} / ${of} checked` : `${n} checked so far`)),
    el("div", { className: (of || n) > 80 ? "ruler dense" : "ruler", role: "img", "aria-label": of !== null ? `${n} of ${of} checked` : `${n} checked so far` },
      Array.from({ length: Math.max(of || 0, n) }, (_, i) => el("span", { className: i < n ? "tick" : "tick todo" }))),
    done ? countsNode : el("p", { className: "meter-note" }, waitNote));
}

const TIMED = [["ingest", "Reading the manuscript"], ["references", "Checking references"],
  ["sources", "Fetching cited sources"], ["claims", "Checking claims"]];
function timingRows(t) {
  const rows = TIMED.filter(([k]) => `t_${k}_start` in t && `t_${k}_end` in t)
    .map(([k, label]) => [label, `${(t[`t_${k}_end`] - t[`t_${k}_start`]).toFixed(1)} s`]);
  if ("t_total" in t) rows.push(["Total", `${t.t_total.toFixed(1)} s`]);
  return rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]);
}

const REF_EXAMPLE = `References
[1] Author A, Author B. Title of the paper. Journal. 2020;12:34-56. doi:10.1234/abcd
[2] Author C. Title of the book. Publisher; 2019.`;

// Nothing to audit (no references, no claims): one screen saying what to fix, no empty sections.
function renderEmpty(r) {
  $("results-h").textContent = "No reference list found in this manuscript";
  const again = el("button", { type: "button" }, "Audit another manuscript");
  again.addEventListener("click", reset);
  $("empty").replaceChildren(
    el("p", {}, "ClaimTrace needs the manuscript's reference list. Put it at the end, under a heading line such as ",
      "“References”, “Bibliography”, “Literature cited” or “Works cited” ",
      "(it may be numbered), with one entry per line, for example:"),
    el("pre", { className: "example" }, REF_EXAMPLE),
    el("div", { className: "actions" }, again),
    r.warnings.length ? el("details", { className: "tech" }, el("summary", {}, `Technical details (${r.warnings.length})`),
      el("ul", { className: "warnings" }, r.warnings.map((w) => el("li", {}, w)))) : null);
}

// The worst counts in plain words. Claim counts overlap (a claim can be not supported AND need
// review), so the line says so when they do.
function headline(r, pending) {
  const count = (xs, f) => xs.filter(f).length;
  const notSup = count(r.claims, (c) => c.verdict === "unsupported");
  const review = count(r.claims, (c) => c.needs_review);
  const both = count(r.claims, (c) => c.verdict === "unsupported" && c.needs_review);
  const parts = [
    [count(r.references, (a) => a.status === "retracted"), "reference", "retracted"],
    [count(r.references, (a) => a.status === "not_found"), "reference", "not found"],
    [notSup, "claim", "not supported"],
    [review, "claim", review === 1 ? "needs review" : "need review"],
    [count(r.references, (a) => a.status === "metadata_mismatch"), "reference", "with details that differ"],
    // not findings, but nothing was verified for them: never "No problems found" over them
    [count(r.references, (a) => a.status === "unchecked"), "reference", "could not be checked"],
    [count(r.claims, (c) => !c.needs_review && !refBad(c) && ["insufficient_evidence", "source_not_found"].includes(c.verdict)),
      "claim", "could not be checked"],
  ].filter(([n]) => n).map(([n, noun, what]) => `${plural(n, noun)} ${what}`);
  const lead = pending ? "So far: " : "";
  if (!parts.length) {
    return `${lead}No problems found${pending ? " yet" : ""} in ${plural(r.claims.length, "claim")} and ${plural(r.references.length, "reference")}.`;
  }
  return lead + parts.join(" · ") + (both ? ` (${plural(both, "claim")} ${both === 1 ? "is" : "are"} in both counts).` : ".");
}

// opts.job: the running job (r is a partial snapshot); opts.saved: r is the saved sample report.
function renderReport(r, opts = {}) {
  lastRender = [r, opts];
  const pending = r.complete === false;
  const empty = !r.references.length && !r.claims.length;
  const first = $("results").hidden;
  $("empty").hidden = !empty;
  $("report").hidden = empty;
  if (empty) {
    renderEmpty(r);
    show("results");
    $("results-h").setAttribute("tabindex", "-1");
    $("results-h").focus();
    return;
  }
  // keep keyboard focus on the same control (counter, Details) across the re-render
  const ae = document.activeElement;
  const focusKey = ae && ae.getAttribute ? ae.getAttribute("data-fk") : null;
  if (pending) streamed = true;
  const hadItems = seen.size > 0;
  fresh = new Set();
  for (const x of [...r.references, ...r.claims]) {
    if (seen.has(itemKey(x))) continue;
    seen.set(itemKey(x), seen.size);
    if (streamed && hadItems) fresh.add(itemKey(x));
  }
  $("report").className = pending ? "running" : opts.saved ? "saved" : "";
  $("saved").hidden = !opts.saved;
  if (opts.saved) {
    const day = new Date(r.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
    $("saved-note").textContent = `Saved example — computed on ${day}; results may differ in a live run.`;
  }
  // live line: while running, and kept (as "complete") after a streamed run so nothing shifts
  $("live").hidden = !streamed;
  if (streamed && !pending) {
    $("live-stage").textContent = "Audit complete.";
  }
  // references are final only once every audit has finished, whatever stage the job shows
  const refsPending = pending && !refsDone(r);
  $("refs-wait").hidden = !refsPending;
  $("claims-wait").hidden = !pending;
  $("results-h").textContent = r.title || "Report";
  // the saved sample is no job: no export or shareable link (they would point at another report)
  for (const id of ["export-json", "export-md", "copy-link", "share"]) $(id).hidden = !!opts.saved;
  if (!opts.saved) {
    $("export-json").href = `/api/jobs/${encodeURIComponent(jobId)}/export.json`;
    $("export-md").href = `/api/jobs/${encodeURIComponent(jobId)}/export.md`;
  }
  refAudits = new Map(r.references.map((a) => [a.reference.id, a]));
  const nr = r.references.filter(refAttention).length;
  const nc = r.claims.filter(claimAttention).length;
  $("summary").textContent = (pending ? "So far: " : "") + [
    r.references.length ? `${nr} of ${plural(r.references.length, "reference")} ${need(nr)} attention`
      : refsPending ? "no references checked yet" : "No references were found",
    r.claims.length ? `${nc} of ${plural(r.claims.length, "claim")} ${need(nc)} attention`
      : pending ? "no claims checked yet" : "No citing sentences were found or checked",
  ].join(" · ");
  $("headline").textContent = headline(r, pending);
  // readout: totals are known only for the stage the job is in right now
  const job = opts.job || {};
  const total = (stage) => (job.stage === stage && job.total ? job.total : null);
  const review = r.claims.filter((c) => c.needs_review).length;
  const badref = r.claims.filter(refBad).length;
  const extraMark = (cls, word) => el("span", { className: `mark ${cls}` }, el("span", { className: "word" }, word));
  $("meters").replaceChildren(
    meter("Claims", r.claims, total("claims"), !pending,
      counters(r.claims, VERDICT, "c:", claimKey, [
        badref ? ["badref", badref, extraMark("s-bad", "cite a missing or retracted reference")] : null,
        review ? ["review", review, extraMark("s-review", "need review")] : null]),
      "Counts by verdict appear when every claim is checked."),
    meter("References", r.references, total("references"), !refsPending,
      counters(r.references, STATUS, "r:", (a) => a.status), "Counts by status appear when every reference is checked."));
  $("claims-n").textContent = String(r.claims.length);
  $("refs-n").textContent = String(r.references.length);
  if (filter) {
    const [labels, v] = filter.startsWith("c:") ? [VERDICT, filter.slice(2)] : [STATUS, filter.slice(2)];
    const name = v === "review" ? "needs review" : v === "badref" ? "citing a missing or retracted reference"
      : (labels[v] || [v])[0].toLowerCase();
    $("filter-note").replaceChildren(`Showing only ${filter.startsWith("c:") ? "claims" : "references"}: ${name}.`,
      el("button", { type: "button", className: "link", "data-filter": "", "data-fk": "f:" }, "Show all"));
  } else $("filter-note").replaceChildren();
  $("warnings").replaceChildren(...r.warnings.map((w) => el("li", {}, w)));
  $("timings").replaceChildren(...timingRows(r.timings || {}));
  $("refs").replaceChildren(...group(r.references, refAttention, STATUS, (a) => a.status, refRow,
    "references", "verified reference", () => false, refsPending, REF_COLS, "r:", pending));
  $("claims").replaceChildren(...group(r.claims, claimAttention, VERDICT, (c) => c.verdict, claimRow,
    "citing sentences", "supported claim", claimMiddle, pending, CLAIM_COLS, "c:", pending));
  // a missing or retracted reference is the worst finding: its section leads
  const refsFirst = r.references.some((a) => a.status === "not_found" || a.status === "retracted");
  $("ledgers").replaceChildren(...(refsFirst ? [$("refs-sec"), $("claims-sec")] : [$("claims-sec"), $("refs-sec")]));
  if (!opts.saved) showPermalink();
  show("results");
  if (first) { // focus moves (and the page scrolls) only when the report first appears
    $("results-h").setAttribute("tabindex", "-1");
    $("results-h").focus();
  } else if (focusKey !== null && document.querySelector) {
    const again = document.querySelector(`[data-fk="${CSS.escape(focusKey)}"]`);
    if (again) again.focus();
  }
}

// Shareable link to this report; anyone holding it can read the report while it is kept.
function showPermalink() {
  const url = `${location.origin}/r/${encodeURIComponent(jobId)}`;
  history.replaceState(null, "", url);
  $("permalink").href = url;
  $("permalink").textContent = url;
  $("copy-status").textContent = "";
}

async function copyLink() {
  try {
    await navigator.clipboard.writeText($("permalink").href);
    $("copy-status").textContent = "Copied.";
  } catch (_) {
    $("copy-status").textContent = "Copy failed: select the link and copy it manually.";
  }
}

async function ask(ev) {
  ev.preventDefault();
  const q = $("question").value.trim();
  if (!q || !jobId) return;
  const btn = $("chat-form").querySelector("button");
  btn.disabled = true;
  $("chat-log").append(el("p", { className: "user" }, q));
  try {
    const resp = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, history: chatHistory.slice(-20) }),
    });
    if (!resp.ok) throw new Error(await apiError(resp));
    const { answer } = await resp.json();
    $("chat-log").append(el("p", {}, answer));
    chatHistory.push({ role: "user", content: q.slice(0, 4000) }, { role: "assistant", content: answer.slice(0, 4000) });
    $("question").value = "";
  } catch (e) {
    $("chat-log").append(el("p", { className: "error" }, e.message));
  } finally {
    btn.disabled = false;
  }
}

function reset() {
  jobId = null;
  $("saved").hidden = true;
  history.replaceState(null, "", "/");
  show("input");
  $($("panel-paste").hidden ? "files" : "text").focus();
}

// Home: "Upload files" / "Paste text" tabs (WAI-ARIA tabs, arrow keys move between them).
function selectTab(name, focus = false) {
  for (const t of ["upload", "paste"]) {
    const on = t === name;
    $(`tab-${t}`).setAttribute("aria-selected", String(on));
    $(`tab-${t}`).tabIndex = on ? 0 : -1;
    $(`panel-${t}`).hidden = !on;
    if (on && focus) $(`tab-${t}`).focus();
  }
}

function showFiles() {
  const names = [...$("files").files].map((f) => f.name);
  $("file-list").textContent = names.length ? `Selected: ${names.join(", ")}` : "";
}

function initDrop() {
  const drop = $("drop");
  const over = (on) => (e) => { e.preventDefault(); drop.classList.toggle("over", on); };
  drop.addEventListener("dragover", over(true));
  drop.addEventListener("dragleave", over(false));
  drop.addEventListener("drop", (e) => {
    over(false)(e);
    if (e.dataTransfer && e.dataTransfer.files.length) $("files").files = e.dataTransfer.files;
    showFiles();
  });
  $("files").addEventListener("change", showFiles);
}

// Retention of shareable links, from the server (no number baked into the page).
// Every field is optional: an older server without jobs_today / daily_limit just shows less.
async function describeServer() {
  let info = {};
  try { info = (await (await fetch("/api/info")).json()) || {}; } catch (_) { /* keep generic text */ }
  const n = info.max_reports;
  const kept = Number.isInteger(n) ? `kept for the newest ${n} reports on this server` : "kept for a limited number of recent reports";
  $("share-note").textContent = `Shareable link (anyone with it can view this report; ${kept}): `;
  $("keep-note").textContent = `How long: ${Number.isInteger(n) ? `the newest ${n} reports are kept` : "only a limited number of recent reports are kept"}; older ones are deleted.`;
  const { jobs_today: used, daily_limit: cap } = info;
  $("usage").textContent = Number.isInteger(used) && Number.isInteger(cap) && cap > 0 ? `${Math.min(used, cap)} of ${cap} audits used today` : "";
}

// "Try a sample" description, counted from the saved sample report itself.
async function describeSample() {
  try {
    const r = await (await fetch("/static/sample-report.json")).json();
    const tally = (xs, key, labels) => Object.keys(labels).map((v) => [xs.filter((x) => key(x) === v).length, labels[v][0].toLowerCase()])
      .filter(([n]) => n).map(([n, w]) => `${n} ${w}`).join(", ");
    $("sample-hint").textContent = `Shows a saved audit of a short sample manuscript: ${plural(r.references.length, "reference")} (${tally(r.references, (a) => a.status, STATUS)}) and ${plural(r.claims.length, "claim")} (${tally(r.claims, (c) => c.verdict, VERDICT)}).`;
  } catch (_) { /* the button still works; it just has no description */ }
}

// Light / dark: follows prefers-color-scheme until the reader picks one (remembered if storage works).
function initTheme() {
  const root = document.documentElement;
  const media = matchMedia("(prefers-color-scheme: dark)");
  const dark = () => (root.dataset.theme ? root.dataset.theme === "dark" : media.matches);
  // the label names the action ("Light theme" while dark is on); no aria-pressed, whose state
  // would contradict a changing label
  const sync = () => { $("theme").textContent = dark() ? "Light theme" : "Dark theme"; };
  sync();
  media.addEventListener("change", sync);
  $("theme").addEventListener("click", () => {
    root.dataset.theme = dark() ? "light" : "dark";
    try { localStorage.setItem("ct-theme", root.dataset.theme); } catch (_) { /* storage blocked */ }
    sync();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  $("job-form").addEventListener("submit", submitJob);
  $("chat-form").addEventListener("submit", ask);
  $("new-job").addEventListener("click", reset);
  $("copy-link").addEventListener("click", copyLink);
  $("sample").addEventListener("click", trySample);
  $("run-live").addEventListener("click", runSampleLive);
  for (const t of ["upload", "paste"]) $(`tab-${t}`).addEventListener("click", () => selectTab(t));
  $("tab-upload").parentNode.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "Home" || e.key === "End") {
      e.preventDefault();
      selectTab(e.key === "ArrowLeft" || e.key === "Home" ? "upload" : "paste", true);
    }
  });
  initDrop();
  initTheme();
  describeServer();
  describeSample();
  // toggle does not bubble: listen in the capture phase
  document.addEventListener("toggle", (e) => {
    const key = e.target.getAttribute && e.target.getAttribute("data-key");
    if (key) (e.target.open ? openKeys.add(key) : openKeys.delete(key));
  }, true);
  // summary counters: toggle buttons that filter the ledger; "Show all" clears
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-filter]");
    if (!b || !lastRender) return;
    const f = b.getAttribute("data-filter");
    const was = filter;
    filter = f && f !== filter ? f : null;
    renderReport(...lastRender);
    const sec = (filter || was || "").startsWith("r:") ? "refs-sec" : "claims-sec";
    if (filter) $(sec).scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    const again = document.querySelector(`[data-fk="${CSS.escape(b.getAttribute("data-fk") || "")}"]`);
    if (again) again.focus({ preventScroll: true });
  });
  $("retry").addEventListener("click", resumePolling);
  const m = location.pathname.match(/^\/r\/([\w-]+)$/);
  if (m) startPolling(m[1]);
});
