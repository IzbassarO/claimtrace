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
// value -> [label, level]; level picks the shape (ok, warn, bad, unknown), order = severity
const STATUS = {
  not_found: ["Not found", "bad"],
  retracted: ["Retracted", "bad"],
  metadata_mismatch: ["Metadata mismatch", "warn"],
  unchecked: ["Unchecked", "unknown"],
  verified: ["Verified", "ok"],
};
const VERDICT = {
  unsupported: ["Unsupported", "bad"],
  partly_supported: ["Partly supported", "warn"],
  source_not_found: ["Source not found", "unknown"],
  insufficient_evidence: ["Insufficient evidence", "unknown"],
  supported: ["Supported", "ok"],
};

let jobId = null;
let chatHistory = [];
const BACKOFF_S = [2, 4, 8, 16, 30]; // ~1 min of retries before showing the Retry button
let failures = 0;

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

// Status label with a CSS shape, so it never depends on colour alone.
function mark(value, labels, label) {
  const [text, level] = labels[value] || [value, "unknown"];
  return el("span", { className: `mark m-${level}` }, label || text);
}

const rank = (labels, value) => { const i = Object.keys(labels).indexOf(value); return i < 0 ? 0 : i; };

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
      formMessage(`${await apiError(resp)} Your text is still here; submit again when ready.`, "notice");
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

// Same flow as pasted text: fill the textarea (so the user sees what was sent) and submit.
async function trySample() {
  formMessage("");
  $("sample").disabled = true;
  try {
    const resp = await fetch("/static/sample.txt");
    if (!resp.ok) throw new Error(`Could not load the sample (HTTP ${resp.status}).`);
    $("text").value = await resp.text();
    $("files").value = "";
    $("job-form").requestSubmit();
  } catch (e) {
    formMessage(e.message);
  } finally {
    $("sample").disabled = false;
  }
}

function startPolling(id) {
  jobId = id;
  chatHistory = [];
  $("chat-log").replaceChildren();
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

// Stage checklist: done / current / to do, plus the live status line and progress bar.
function renderStages(job) {
  const cur = job.status === "queued" ? -1 : job.stage === "done" ? STAGES.length : STAGES.findIndex(([k]) => k === job.stage);
  $("stages").replaceChildren(...STAGES.map(([, label], i) => {
    const state = i < cur ? "done" : i === cur ? "current" : "todo";
    const count = state === "current" && job.total ? ` ${job.done} of ${job.total}` : "";
    return el("li", { className: `st-${state}`, ...(state === "current" ? { "aria-current": "step" } : {}) },
      label, count ? el("span", { className: "count" }, count) : null,
      el("span", { className: "sr-only" }, { done: " (done)", current: " (in progress)", todo: "" }[state]));
  }));
  const label = job.status === "queued" ? "Queued: waiting for a free worker"
    : cur >= STAGES.length ? "Finishing" : (STAGES[cur] || [, "Working"])[1];
  $("stage").textContent = job.total ? `${label}: ${job.done} of ${job.total}` : label;
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
  setTimeout(poll, 1000);
}

// ref id -> audit status, set by renderReport: a claim is only as good as the reference it cites
let refStatus = new Map();
const REF_NOTE = {
  retracted: "The cited source is retracted.",
  not_found: "The cited reference was not found.",
  metadata_mismatch: "Reference metadata does not match the cited work.",
};
const refNote = (c) => REF_NOTE[refStatus.get(c.ref_id)] || null;
const refAttention = (a) => a.status !== "verified";
const claimAttention = (c) => c.verdict === "unsupported" || c.needs_review || refStatus.get(c.ref_id) === "retracted";
// partly supported, not checkable, or citing a doubtful reference: shown open, below the claims that need attention
const claimMiddle = (c) => !claimAttention(c) && (c.verdict !== "supported" || refNote(c) !== null);
const MIDDLE = "Partly supported, not checkable, or citing a doubtful reference";
const need = (n) => (n === 1 ? "needs" : "need");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function refItem(a) {
  const ref = a.reference;
  const m = a.matched;
  const failed = a.providers.some((p) => !p.ok);
  // unchecked although every provider answered: the audit note says why (possible match, nothing to compare)
  const explained = a.status === "unchecked" && !failed && a.note;
  const found = m
    ? el("p", { className: "found" }, el("span", { className: "label" }, "Found: "),
      m.title || "(untitled)", m.year ? ` (${m.year})` : "", ` · ${m.provider}`,
      m.doi ? [" · ", link(`https://doi.org/${m.doi}`, `doi:${m.doi}`)] : m.url ? [" · ", link(m.url, "record")] : null)
    : el("p", { className: "found quiet" }, a.status !== "unchecked" ? "No matching record was found."
      : failed ? "The lookup failed, so this reference could not be checked."
        : explained || "The providers answered, but this reference could not be confirmed or ruled out.");
  const issues = [
    ...a.mismatches.map((x) => el("li", {}, `${x.field}: cited "${x.cited}", found "${x.found}"`)),
    ...a.providers.filter((p) => !p.ok).map((p) => el("li", {}, `${p.provider} failed${p.error ? `: ${p.error}` : ""}`)),
    a.note && !explained ? el("li", {}, a.note) : null,
  ].filter(Boolean);
  return el("li", { className: "item" },
    el("p", { className: "item-head" }, mark(a.status, STATUS, a.status === "unchecked" && failed ? "Unchecked (lookup failed)" : null), el("span", { className: "id" }, `ref ${ref.id}`)),
    el("p", { className: "cited" }, ref.raw),
    found,
    issues.length ? el("ul", { className: "issues" }, issues) : null);
}

function opinion(role, o) {
  if (!o) return el("p", {}, `${role}: no answer (model call failed).`);
  return el("div", { className: "opinion" },
    el("p", {}, el("strong", {}, `${role}: `), (VERDICT[o.verdict] || [o.verdict])[0]),
    o.rationale ? el("p", {}, o.rationale) : null,
    o.quote ? el("p", { className: "quiet" }, `Quoted (not necessarily verified): "${o.quote}"`) : null);
}

function claimCard(c) {
  const kind = { full_text: "Checked against the full text", abstract: "Checked against the abstract only" }[c.evidence_kind];
  return el("li", { className: "item claim" },
    el("article", { "aria-label": `Claim ${c.citation_id} citing reference ${c.ref_id}` },
      el("p", { className: "item-head" },
        mark(c.verdict, VERDICT),
        c.needs_review ? el("span", { className: "mark m-review" }, "Needs review") : null,
        el("span", { className: "id" }, `${c.citation_id} → ref ${c.ref_id}`)),
      refNote(c) ? el("p", { className: "notice" }, refNote(c)) : null,
      el("p", { className: "label" }, "The manuscript says"),
      el("p", { className: "sentence" }, c.sentence),
      c.quote
        ? [el("p", { className: "label" }, "The source says"), el("blockquote", { className: "quote" }, c.quote)]
        : el("p", { className: "quiet" }, "No verbatim quote from the source backs a verdict."),
      el("p", { className: "provenance" }, kind || null, kind && c.source_url ? " · " : "",
        c.source_url ? link(c.source_url, "source") : null),
      c.note ? el("p", { className: "quiet" }, c.note) : null,
      c.judge || c.skeptic ? el("details", {}, el("summary", {}, "Model rationales"), opinion("Judge", c.judge), opinion("Skeptic", c.skeptic)) : null));
}

// Problems first (worst first), everything fine collapsed in a <details>.
function group(items, isProblem, labels, key, render, noun, fineNoun, isMiddle = () => false) {
  const worst = (xs) => xs.sort((a, b) => rank(labels, key(a)) - rank(labels, key(b)));
  const bad = worst(items.filter(isProblem));
  const mid = worst(items.filter((x) => !isProblem(x) && isMiddle(x)));
  const fine = items.filter((x) => !isProblem(x) && !isMiddle(x));
  if (!items.length) return [el("p", { className: "quiet" }, `No ${noun} were found in this manuscript.`)];
  return [
    bad.length ? el("ol", { className: "items" }, bad.map(render)) : el("p", { className: "quiet" }, `No ${noun} need attention.`),
    mid.length ? [el("h3", {}, `${MIDDLE} (${mid.length})`), el("ol", { className: "items" }, mid.map(render))] : null,
    fine.length ? el("details", { className: "fine" }, el("summary", {}, plural(fine.length, fineNoun)),
      el("ol", { className: "items" }, fine.map(render))) : null,
  ].flat().filter(Boolean);
}

function renderReport(r) {
  $("results-h").textContent = r.title || "Report";
  $("export-json").href = `/api/jobs/${encodeURIComponent(jobId)}/export.json`;
  $("export-md").href = `/api/jobs/${encodeURIComponent(jobId)}/export.md`;
  refStatus = new Map(r.references.map((a) => [a.reference.id, a.status]));
  const nr = r.references.filter(refAttention).length;
  const nc = r.claims.filter(claimAttention).length;
  const nm = r.claims.filter(claimMiddle).length;
  $("summary").textContent = !r.references.length && !r.claims.length
    ? "No references or citing sentences were found. Check that the text includes its reference list."
    : [r.references.length ? `${nr} of ${plural(r.references.length, "reference")} ${need(nr)} attention` : "No references were found",
      r.claims.length ? `${nc} of ${plural(r.claims.length, "claim")} ${need(nc)} attention` : "No citing sentences were found or checked",
      nm ? `${nm} ${MIDDLE.toLowerCase()}` : ""].filter(Boolean).join(" · ");
  $("warnings").replaceChildren(...r.warnings.map((w) => el("li", {}, w)));
  $("refs").replaceChildren(...group(r.references, refAttention, STATUS, (a) => a.status, refItem,
    "references", "verified reference"));
  $("claims").replaceChildren(...group(r.claims, claimAttention, VERDICT, (c) => c.verdict, claimCard,
    "citing sentences", "supported claim", claimMiddle));
  showPermalink();
  show("results");
  $("results-h").setAttribute("tabindex", "-1");
  $("results-h").focus();
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
  history.replaceState(null, "", "/");
  show("input");
  $("text").focus();
}

document.addEventListener("DOMContentLoaded", () => {
  $("job-form").addEventListener("submit", submitJob);
  $("chat-form").addEventListener("submit", ask);
  $("new-job").addEventListener("click", reset);
  $("copy-link").addEventListener("click", copyLink);
  $("sample").addEventListener("click", trySample);
  $("retry").addEventListener("click", resumePolling);
  const m = location.pathname.match(/^\/r\/([\w-]+)$/);
  if (m) startPolling(m[1]);
});
