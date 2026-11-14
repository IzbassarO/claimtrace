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
const openKeys = new Set(); // rows / <details data-key> the reader opened, kept open across re-renders
const autoOpened = new Set(); // defaults applied once (first attention row), never re-applied
const NARROW = "(max-width: 48rem)"; // same breakpoint as the phone layout in style.css
const ATTENTION_FIRST = 5; // "Needs your attention" shows this many rows until "Show all"

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
// worst first: a claim citing a missing/retracted reference, then verdict/status (claims the two
// readers disagree on after every agreed one), then the cited reference's status
const severity = (labels, key, x) => (!x.reference && refBad(x) ? -1000 : rank(labels, key(x)) * 100)
  + (x.reference ? 0 : (disagree(x) && !refBad(x) ? 1000 : 0) + rank(STATUS, refStatus.get(x.ref_id) || "verified"));

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
  autoOpened.clear();
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
// under its (possibly green) verdict; one the two readers disagree on is counted as "review", never
// under its cautious verdict
const claimKey = (c) => (refBad(c) ? "badref" : disagree(c) ? "review" : c.verdict);
// counts shown beside the per-verdict ones (screen counters and print): [value, count, words, mark class]
const claimExtras = (cs) => [["badref", cs.filter(refBad).length, "cite a missing or retracted reference", "s-bad"],
  ["review", cs.filter((c) => claimKey(c) === "review").length, "need review", "s-review"]].filter(([, n]) => n);
// "Needs review" means the two readers disagree, nothing else: the backend's needs_review is also
// true when only one model answered (or none), which is shown as "judge only" / "skeptic only"
const disagree = (c) => !!(c.judge && c.skeptic && c.judge.verdict !== c.skeptic.verdict);
const verdictWord = (v) => (VERDICT[v] || [v])[0].toLowerCase();
const readers = (c) => `Judge: ${verdictWord(c.judge.verdict)} · Skeptic: ${verdictWord(c.skeptic.verdict)}`;
// main label: "Needs review" when the readers disagree (both verdicts shown beside it), else the verdict
const claimMark = (c) => (disagree(c) ? el("span", { className: "mark s-review" }, el("span", { className: "word" }, "Needs review"))
  : refBad(c) ? mark(c.verdict, VERDICT, null, "unknown") : mark(c.verdict, VERDICT));
const claimAttention = (c) => c.verdict === "unsupported" || disagree(c) || refBad(c);
// collapsed claim groups below "Needs your attention", in this order
const CLAIM_GROUPS = [
  ["partly", "Partly supported", "s-partly v u-partly_supported", (c) => c.verdict === "partly_supported"],
  ["supported", "Supported", "s-ok v u-supported", (c) => c.verdict === "supported"],
  ["unchecked", "Could not check", "s-unknown v u-insufficient_evidence", (c) => !["partly_supported", "supported"].includes(c.verdict)],
];
// header order: "References · 38 verified · 1 differs · 1 unchecked"
const REF_SHORT = { verified: "verified", metadata_mismatch: "differs", unchecked: "unchecked", not_found: "not found", retracted: "retracted" };
const REF_TAG = { not_found: "reference not found", retracted: "reference retracted", metadata_mismatch: "reference differs", unchecked: "reference unchecked" };
const need = (n) => (n === 1 ? "needs" : "need");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
// "38 verified, 1 details differ": one count per status present, in severity order
const tally = (xs, key, labels, sep = ", ") => Object.keys(labels).map((v) => [xs.filter((x) => key(x) === v).length, labels[v][0].toLowerCase()])
  .filter(([n]) => n).map(([n, w]) => `${n} ${w}`).join(sep);
// first sentence; "et al." / "e.g." / "Fig." do not end one
const firstSentence = (s) => String(s || "").split(/(?<!\b(?:al|e\.g|i\.e|cf|vs|Fig|Eq|No)\.)(?<=[.!?])\s+(?=[A-Z0-9“"(])/)[0];

// Reader state that survives the re-render on every streamed snapshot.
let filter = null; // "c:<verdict>", "c:review", "r:<status>" or null (show everything)
let lastRender = null; // [report, opts] for re-rendering when a filter changes
let fresh = new Set(); // keys that arrived in this render (briefly highlighted)
const filterMatch = (x) => filter.slice(2) === (x.reference ? x.status : claimKey(x));

// One ledger row: a full-width toggle button (the collapsed line) and, only when expanded, its body.
// The open state lives in openKeys, so a streamed re-render keeps it.
function rowEl(key, head, body) {
  const k = `row-${key}`;
  const open = openKeys.has(k);
  return el("li", { className: `row${open ? " open" : ""}${fresh.has(key) ? " new" : ""}` },
    el("button", { type: "button", className: "row-head", "data-toggle": k, "data-fk": k, "aria-expanded": String(open),
      ...(open ? { "aria-controls": `body-${key}` } : {}) }, head, el("span", { className: "cue" }, open ? "Less" : "More")),
    open ? el("div", { className: "row-body", id: `body-${key}` }, body()) : null);
}

const tag = (word, cls = "") => el("span", { className: `tag ${cls}` }, word);

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
  const failed = a.providers.some((p) => !p.ok);
  const reason = refReason(a);
  const m = a.matched;
  // the collapsed line: why it is not verified, or where it was found
  const aside = reason || (a.status === "unchecked" && !failed && a.note) || (m ? [m.provider, m.year].filter(Boolean).join(" ") : "");
  return rowEl(itemKey(a),
    [mark(a.status, STATUS, a.status === "unchecked" && failed ? "Could not check (lookup failed)" : null),
      el("span", { className: "id" }, `ref ${ref.id}`),
      el("span", { className: "line" }, ref.title || ref.raw),
      el("span", { className: "aside" }, aside)],
    () => {
      const table = fieldsTable(a);
      return [el("p", { className: "cited" }, ref.raw), foundLine(a),
        reason ? el("p", { className: "reason" }, reason) : null,
        table ? el("div", { className: "well" }, el("h4", {}, "Fields"), table) : null, lookups(a)];
    });
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

// "Vaswani et al. 2017, Attention is all you need" (long) or "Vaswani 2017" (short, collapsed row)
function refShort(id, short = false) {
  const a = refAudits.get(id);
  if (!a) return null;
  const r = a.reference;
  const who = r.authors.length ? `${r.authors[0].split(",")[0].trim().split(" ").at(-1)}${r.authors.length > 1 && !short ? " et al." : ""}` : "";
  const name = [who, r.year].filter(Boolean).join(" ");
  return short ? name || null : [name, r.title].filter(Boolean).join(", ") || null;
}

// only one of the two models answered: the claims module's note says which ("skeptic unavailable — judge only")
const singleModel = (c) => (!c.judge === !c.skeptic ? null : c.judge ? "judge only" : "skeptic only");
// one line above the report when the second reader was missing for some claims; M = claims with
// at least one model opinion (a claim with no source or both models failed was read by neither)
function oneModelNote(r) {
  const n = r.claims.filter(singleModel).length;
  const m = r.claims.filter((c) => c.judge || c.skeptic).length;
  return n ? `The second reader was unavailable for ${n} of ${plural(m, "claim")}; they were checked by one model. `
    + "Run the audit again later for the two-reader check." : null;
}

function claimReason(c) {
  if (!c.judge !== !c.skeptic) return c.note ? c.note.split(";")[0] : "Only one of the two models answered.";
  if (disagree(c)) return "The judge and the skeptic disagree: read the quote and decide.";
  if (c.note) return firstSentence(c.note);
  const o = [c.judge, c.skeptic].find((x) => x && x.verdict === c.verdict && x.rationale);
  return o ? firstSentence(o.rationale) : null;
}

function claimRow(c) {
  const key = itemKey(c);
  const a = refAudits.get(c.ref_id);
  const one = singleModel(c);
  return rowEl(key,
    [claimMark(c),
      el("span", { className: "line" },
        one ? tag(one) : null,
        a && REF_TAG[a.status] ? tag(REF_TAG[a.status]) : null,
        el("span", { className: "text" }, c.sentence)),
      el("span", { className: "aside" }, refShort(c.ref_id, true) || `ref ${c.ref_id}`),
      disagree(c) ? el("span", { className: "readers" }, readers(c)) : null],
    () => claimBody(c, key, a));
}

function claimBody(c, key, a) {
  const kind = { full_text: "full text", abstract: "abstract" }[c.evidence_kind];
  const long = { full_text: "Checked against the full text", abstract: "Checked against the abstract only" }[c.evidence_kind];
  const note = refNote(c);
  const reason = claimReason(c);
  const src = refShort(c.ref_id);
  const qk = `quote-${key}`;
  const full = openKeys.has(qk);
  return [
    el("p", { className: "sentence" }, c.sentence),
    c.quote ? [el("blockquote", { className: full ? "quote" : "quote clamp", id: `q-${key}` }, c.quote),
      el("button", { type: "button", className: "link more-quote", "data-toggle": qk, "data-fk": qk, "data-quote": `q-${key}`,
        "aria-expanded": String(full), "aria-controls": `q-${key}` }, full ? "Show less of the quote" : "Show full quote")]
      : el("p", { className: "quiet" }, "No verbatim quote from the source backs a verdict."),
    reason ? el("p", { className: "reason" }, reason) : null,
    note ? el("p", { className: "ref-note" }, note) : null,
    el("p", { className: "provenance" }, `${c.citation_id} → ref ${c.ref_id}`, kind ? ` · ${kind}` : "",
      src ? ` · ${refShort(c.ref_id, true)}` : "", c.source_url ? [" · ", link(c.source_url, "open the source")] : null),
    details(`why-${key}`, { className: "more" }, el("summary", { "data-fk": `why-${key}` }, "Details"),
      el("div", { className: "more-body" },
        c.judge || c.skeptic ? [opinion("Judge", c.judge, c.quote), opinion("Skeptic", c.skeptic, c.quote)] : null,
        el("div", { className: "well" }, el("h4", {}, "Source"),
          el("p", {}, [long, c.section ? `§ ${c.section}` : null].filter(Boolean).join(" · ") || "No source text was read."),
          c.source_url ? el("p", {}, link(c.source_url)) : null,
          c.note ? el("p", {}, c.note) : null),
        a ? el("div", { className: "well" }, el("h4", {}, `Reference ${a.reference.id}`, mark(a.status, STATUS)),
          el("p", {}, a.reference.raw), fieldsTable(a)) : null)),
  ];
}

const ledger = (rows) => el("ol", { className: "ledger" }, rows);
const groupHead = (key, ...children) => el("summary", { "data-fk": key }, ...children);
// "· 2 cite a reference that differs or could not be checked": never hidden inside a closed group
const doubtful = (xs) => {
  const n = xs.filter((c) => refNote(c) !== null).length;
  return n ? el("span", { className: "group-note" }, `· ${n} ${n === 1 ? "cites" : "cite"} a reference that differs or could not be checked`) : null;
};
const order = (xs, labels, key, running) => xs.sort(running ? (a, b) => seen.get(itemKey(a)) - seen.get(itemKey(b))
  : (a, b) => severity(labels, key, a) - severity(labels, key, b) || seen.get(itemKey(a)) - seen.get(itemKey(b)));
const filtered = (items, labels, key, render, noun, running) => {
  const hits = order(items.filter(filterMatch), labels, key, running);
  return hits.length ? [ledger(hits.map(render))] : [el("p", { className: "none" }, `No ${noun} match this filter.`)];
};

// Claims: "Needs your attention" open (first row expanded once, first 5 rows until "Show all"),
// every other group a collapsed header with its count. With a claim filter: the matching rows only.
function claimGroups(items, pending, running) {
  const key = (c) => c.verdict;
  if (!items.length) return [el("p", { className: "none" }, pending ? "No citing sentences checked yet." : "No citing sentences were found in this manuscript.")];
  if (filter && filter.startsWith("c:")) return filtered(items, VERDICT, key, claimRow, "citing sentences", running);
  const bad = order(items.filter(claimAttention), VERDICT, key, running);
  // first row open by default, once; on a phone it starts collapsed so several problem rows fit on screen
  if (bad.length && !autoOpened.has("first")) {
    if (typeof matchMedia !== "function" || !matchMedia(NARROW).matches) openKeys.add(`row-${itemKey(bad[0])}`);
    autoOpened.add("first");
  }
  const all = openKeys.has("all-attention");
  const shown = all ? bad : bad.slice(0, ATTENTION_FIRST);
  const rest = items.filter((c) => !claimAttention(c));
  return [
    bad.length ? [el("h3", {}, "Needs your attention ", el("span", { className: "n" }, String(bad.length)),
      pending ? el("span", { className: "group-note" }, " · still checking") : null), ledger(shown.map(claimRow)),
      bad.length > ATTENTION_FIRST ? el("button", { type: "button", className: "link show-all", "data-toggle": "all-attention",
        "data-fk": "all-attention", "aria-expanded": String(all) }, all ? `Show the first ${ATTENTION_FIRST}` : `Show all ${bad.length}`) : null]
      : el("p", { className: "none" }, `No citing sentences need attention${pending ? " so far" : ""}.`),
    CLAIM_GROUPS.map(([g, word, cls, test]) => {
      const xs = order(rest.filter((c) => test(c) && !CLAIM_GROUPS.slice(0, CLAIM_GROUPS.findIndex((x) => x[0] === g)).some((x) => x[3](c))), VERDICT, key, running);
      return xs.length ? details(`grp-${g}`, { className: "fine" },
        groupHead(`grp-${g}`, el("span", { className: `mark ${cls}` }, el("span", { className: "word" }, word)),
          el("span", { className: "count" }, `· ${xs.length}`), doubtful(xs)),
        ledger(xs.map(claimRow))) : null;
    }),
  ].flat(Infinity).filter(Boolean);
}

// References: one collapsed header with the counts by status; inside, one line per reference.
function refGroup(items, pending, running) {
  const key = (a) => a.status;
  if (!items.length) return [el("p", { className: "none" }, pending ? "No references checked yet." : "No references were found in this manuscript.")];
  if (filter && filter.startsWith("r:")) return filtered(items, STATUS, key, refRow, "references", running);
  const n = new Map();
  for (const a of items) n.set(a.status, (n.get(a.status) || 0) + 1);
  const counts = Object.keys(REF_SHORT).filter((v) => n.get(v)).map((v) => `${n.get(v)} ${REF_SHORT[v]}`);
  return [details("fine-references", { className: "fine" },
    groupHead("fine-references", el("span", { className: "group-name" }, "References"), el("span", {}, `· ${counts.join(" · ")}`),
      pending ? el("span", { className: "group-note" }, "· still checking") : null),
    ledger(order(items.slice(), STATUS, key, running).map(refRow)))];
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
    el("div", { className: (of || n) > 40 ? "ruler dense" : "ruler", role: "img", "aria-label": of !== null ? `${n} of ${of} checked` : `${n} checked so far` },
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

// At most the two worst counts in plain words: one for references, one for claims (the counters
// below give every count). A missing or retracted reference leads.
function headline(r, pending) {
  const count = (xs, f) => xs.filter(f).length;
  const gone = r.references.filter((a) => a.status === "not_found" || a.status === "retracted");
  const retracted = count(gone, (a) => a.status === "retracted");
  const refAtt = count(r.references, refAttention);
  const refPart = gone.length
    ? `${plural(gone.length, "reference")} ${!retracted ? "not found" : retracted === gone.length ? "retracted" : "not found or retracted"}`
    // not findings, but nothing was verified for them: never "No problems found" over them
    : refAtt ? `${plural(refAtt, "reference")} ${need(refAtt)} attention` : null;
  // "not supported" only where the readers agree (or one model read it); disagreements are "review"
  const notSup = count(r.claims, (c) => c.verdict === "unsupported" && !disagree(c));
  const review = count(r.claims, disagree);
  const unchecked = count(r.claims, (c) => !disagree(c) && !refBad(c) && ["insufficient_evidence", "source_not_found"].includes(c.verdict));
  const partly = count(r.claims, (c) => c.verdict === "partly_supported" && !disagree(c));
  const oneModel = count(r.claims, singleModel);
  const read = count(r.claims, (c) => c.judge || c.skeptic);
  // nothing contradicted, but not everything plainly backed (or read by only one model):
  // never "No problems found" / a bare "No claims contradicted" over it
  const clean = ["No claims contradicted", partly ? `${partly} partly supported` : null,
    oneModel ? `checked by one model for ${oneModel} of ${read}` : null].filter(Boolean);
  const claimPart = notSup || review ? null : unchecked ? `${plural(unchecked, "claim")} could not be checked`
    : clean.length > 1 ? clean.join(" · ") : null;
  // the two worst counts, in this order
  const parts = [gone.length ? refPart : null, notSup ? `${plural(notSup, "claim")} not supported` : null,
    review ? (notSup ? `${review} ${need(review)} review` : `${plural(review, "claim")} ${need(review)} review`) : null,
    claimPart, gone.length ? null : refPart].filter(Boolean).slice(0, 2);
  const lead = pending ? "So far: " : "";
  if (!parts.length) {
    return `${lead}No problems found${pending ? " yet" : ""} in ${plural(r.claims.length, "claim")} and ${plural(r.references.length, "reference")}.`;
  }
  return `${lead}${parts.join(" · ")}.`;
}

// opts.job: the running job (r is a partial snapshot); opts.saved: r is the saved sample report.
function renderReport(r, opts = {}) {
  lastRender = [r, opts];
  const pending = r.complete === false;
  const empty = !r.references.length && !r.claims.length;
  const first = $("results").hidden;
  $("empty").hidden = !empty;
  $("report").hidden = empty;
  $("exports").hidden = empty;
  $("print").hidden = empty;
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
  $("exports").className = `exports ${$("report").className}`;
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
  for (const id of ["downloads", "copy-link", "share"]) $(id).hidden = !!opts.saved;
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
  $("one-model").textContent = oneModelNote(r) || "";
  $("one-model").hidden = !oneModelNote(r);
  // readout: totals are known only for the stage the job is in right now
  const job = opts.job || {};
  const total = (stage) => (job.stage === stage && job.total ? job.total : null);
  const extraMark = (cls, word) => el("span", { className: `mark ${cls}` }, el("span", { className: "word" }, word));
  $("meters").replaceChildren(
    meter("Claims", r.claims, total("claims"), !pending,
      counters(r.claims, VERDICT, "c:", claimKey, claimExtras(r.claims).map(([v, n, word, cls]) => [v, n, extraMark(cls, word)])),
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
  $("refs").replaceChildren(...refGroup(r.references, refsPending, pending));
  $("claims").replaceChildren(...claimGroups(r.claims, pending, pending));
  // a missing or retracted reference is the worst finding: its section leads
  const refsFirst = r.references.some((a) => a.status === "not_found" || a.status === "retracted");
  $("ledgers").replaceChildren(...(refsFirst ? [$("refs-sec"), $("claims-sec")] : [$("claims-sec"), $("refs-sec")]));
  if (!opts.saved) showPermalink();
  $("print-report").replaceChildren(...printView(r, opts, refsFirst));
  show("results");
  // "Show full quote" only where the 3-line clamp actually cuts the quote
  // ponytail: measured once per render, not on resize; a resize to narrower shows no button until the next render
  if (document.querySelectorAll) {
    for (const b of document.querySelectorAll("[data-quote]")) {
      const q = $(b.getAttribute("data-quote"));
      b.hidden = b.getAttribute("aria-expanded") !== "true" && q.scrollHeight <= q.clientHeight + 1;
    }
  }
  if (first) { // focus moves (and the page scrolls) only when the report first appears
    $("results-h").setAttribute("tabindex", "-1");
    $("results-h").focus();
  } else if (focusKey !== null && document.querySelector) {
    const again = document.querySelector(`[data-fk="${CSS.escape(focusKey)}"]`);
    if (again) again.focus();
  }
}

// Print / "PDF (print)": a flat copy of the whole report that only @media print shows. Built from
// the data, so every group and row is expanded with its full quote while the reader's on-screen
// open rows, filter and "Show all" stay exactly as they were.
function printView(r, opts, refsFirst) {
  const src = (url) => Object.assign(link(url, "source"), { className: "src" }); // print shows its URL
  const claim = (c) => {
    const a = refAudits.get(c.ref_id);
    const one = singleModel(c);
    const reason = claimReason(c);
    const note = refNote(c);
    const kind = { full_text: "full text", abstract: "abstract only" }[c.evidence_kind];
    return el("li", { className: "p-row" },
      el("p", { className: "p-head" }, claimMark(c), disagree(c) ? el("span", { className: "readers" }, readers(c)) : null,
        one ? tag(one) : null, a && REF_TAG[a.status] ? tag(REF_TAG[a.status]) : null),
      el("p", { className: "p-sentence" }, c.sentence),
      c.quote ? el("blockquote", { className: "quote" }, c.quote) : el("p", { className: "quiet" }, "No verbatim quote from the source backs a verdict."),
      reason ? el("p", { className: "p-reason" }, reason) : null,
      note ? el("p", { className: "ref-note" }, note) : null,
      el("p", { className: "provenance" }, `${c.citation_id} → ref ${c.ref_id}`, kind ? ` · ${kind}` : "",
        refShort(c.ref_id) ? ` · ${refShort(c.ref_id)}` : "", c.source_url ? [" · ", src(c.source_url)] : null));
  };
  const byVerdict = (xs) => order(xs, VERDICT, (c) => c.verdict, false);
  const rest = r.claims.filter((c) => !claimAttention(c));
  const groups = [["Needs your attention", byVerdict(r.claims.filter(claimAttention))],
    ...CLAIM_GROUPS.map(([, word, , test]) => [word, byVerdict(rest.filter(test))])];
  const claims = el("section", { className: "p-claims" }, el("h2", {}, `Claims · ${r.claims.length}`),
    r.claims.length ? groups.filter(([, xs]) => xs.length).map(([word, xs]) => [el("h3", {}, `${word} · ${xs.length}`),
      el("ol", { className: "p-list" }, xs.map(claim))]) : el("p", {}, "No citing sentences were found or checked."));
  const refRowP = (a) => {
    const failed = a.providers.some((p) => !p.ok);
    const reason = refReason(a);
    return el("tr", {},
      el("td", {}, mark(a.status, STATUS, a.status === "unchecked" && failed ? "Could not check (lookup failed)" : null)),
      el("td", { className: "id" }, a.reference.id),
      el("td", {}, a.reference.raw),
      el("td", {}, foundLine(a), a.mismatches.map((x) => el("p", { className: "p-diff" }, `${x.field}: ${x.cited} → ${x.found}`))),
      el("td", {}, reason || a.note || ""));
  };
  const refs = el("section", { className: "p-refs" }, el("h2", {}, `References · ${r.references.length}`),
    r.references.length ? el("table", {},
      el("thead", {}, el("tr", {}, ["Status", "Ref", "Cited", "Found / differences", "Note"].map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {}, order(r.references.slice(), STATUS, (a) => a.status, false).map(refRowP)))
      : el("p", {}, "No references were found in this manuscript."));
  const day = new Date(r.created_at || Date.now()).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
  return [
    el("h1", {}, r.title || "Report"),
    el("p", { className: "headline" }, headline(r, r.complete === false)),
    oneModelNote(r) ? el("p", { className: "p-notice" }, oneModelNote(r)) : null,
    // same counting as the on-screen counters: a claim citing a missing reference is never under its verdict
    el("p", {}, "Claims: ", [tally(r.claims, claimKey, VERDICT, " · "),
      ...claimExtras(r.claims).map(([, n, word]) => `${n} ${word}`)].filter(Boolean).join(" · ") || "none"),
    el("p", {}, "References: ", tally(r.references, (a) => a.status, STATUS, " · ") || "none"),
    el("p", { className: "p-meta" }, opts.saved ? `Saved example, computed on ${day}; results may differ in a live run.`
      : [`Audited on ${day} · Shareable link: `, el("span", { className: "mono" }, `${location.origin}/r/${encodeURIComponent(jobId)}`)]),
    refsFirst ? [refs, claims] : [claims, refs],
  ].flat().filter(Boolean);
}

function rerender(focusKey) {
  renderReport(...lastRender);
  const again = focusKey && document.querySelector(`[data-fk="${CSS.escape(focusKey)}"]`);
  if (again) again.focus({ preventScroll: true });
}

// Expand all: every group and every row (not the inner "Details"); collapse all: everything closed.
function expandAll(on) {
  openKeys.clear();
  autoOpened.add("first"); // the reader chose: no default-open row comes back
  if (!on) return;
  const r = lastRender[0];
  for (const x of [...r.references, ...r.claims]) openKeys.add(`row-${itemKey(x)}`);
  for (const k of ["fine-references", "all-attention", ...CLAIM_GROUPS.map(([g]) => `grp-${g}`)]) openKeys.add(k);
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
  // rows, "Show full quote", "Show all N": toggle their key and re-render (the key survives streaming)
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-toggle]");
    if (!b || !lastRender) return;
    const k = b.getAttribute("data-toggle");
    openKeys.has(k) ? openKeys.delete(k) : openKeys.add(k);
    rerender(b.getAttribute("data-fk"));
  });
  $("method-btn").addEventListener("click", () => {
    const on = $("method").hidden;
    $("method").hidden = !on;
    $("method-btn").setAttribute("aria-expanded", String(on));
  });
  $("expand-all").addEventListener("click", () => { expandAll(true); rerender("expand-all"); });
  $("collapse-all").addEventListener("click", () => { expandAll(false); rerender("collapse-all"); });
  $("retry").addEventListener("click", resumePolling);
  $("print-pdf").addEventListener("click", () => window.print());
  // the method note, printed once at the end of the print copy (static text, no report data)
  $("print-method").append($("method").firstElementChild.cloneNode(true));
  const m = location.pathname.match(/^\/r\/([\w-]+)$/);
  if (m) startPolling(m[1]);
});
