"use strict";
// All report data is untrusted (manuscript, provider records, model output):
// it is only ever written with text nodes / textContent / setAttribute, never parsed as HTML.

const $ = (id) => document.getElementById(id);

const STAGES = {
  ingest: "Reading the manuscript",
  references: "Checking references",
  sources: "Fetching cited sources",
  claims: "Checking claims against sources",
  done: "Finishing",
};
const STATUS = {
  verified: "Verified",
  metadata_mismatch: "Metadata mismatch",
  retracted: "Retracted",
  not_found: "Not found",
  unchecked: "Unchecked (lookup failed)",
};
const VERDICT = {
  supported: "Supported",
  partly_supported: "Partly supported",
  unsupported: "Unsupported",
  insufficient_evidence: "Insufficient evidence",
  source_not_found: "Source not found",
};

let jobId = null;
let chatHistory = [];

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

const badge = (value, labels) => el("span", { className: `badge b-${value}` }, labels[value] || value);

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

async function submitJob(ev) {
  ev.preventDefault();
  $("form-error").textContent = "";
  const form = new FormData($("job-form"));
  if (!form.get("text").trim() && !$("files").files.length) {
    $("form-error").textContent = "Paste manuscript text or choose at least one file.";
    return;
  }
  $("submit").disabled = true;
  try {
    const resp = await fetch("/api/jobs", { method: "POST", body: form });
    if (!resp.ok) throw new Error(await apiError(resp));
    const { id } = await resp.json();
    history.replaceState(null, "", `/r/${encodeURIComponent(id)}`);
    startPolling(id);
  } catch (e) {
    $("form-error").textContent = e.message;
  } finally {
    $("submit").disabled = false;
  }
}

function startPolling(id) {
  jobId = id;
  chatHistory = [];
  $("chat-log").replaceChildren();
  show("progress");
  $("stage").textContent = "Queued";
  $("bar").removeAttribute("value");
  poll();
}

async function poll() {
  const id = jobId;
  let job;
  try {
    const resp = await fetch(`/api/jobs/${encodeURIComponent(id)}`);
    if (!resp.ok) throw new Error(await apiError(resp));
    job = await resp.json();
  } catch (e) {
    if (id !== jobId) return;
    show("input");
    $("form-error").textContent = e.message;
    return;
  }
  if (id !== jobId) return;
  if (job.status === "done") return renderReport(job.report);
  if (job.status === "error") {
    show("input");
    $("form-error").textContent = `The audit failed: ${job.error || "unknown error"}`;
    return;
  }
  const label = job.status === "queued" ? "Queued: waiting for a free worker" : STAGES[job.stage] || "Working";
  $("stage").textContent = job.total ? `${label}: ${job.done} of ${job.total}` : label;
  if (job.total) { $("bar").max = job.total; $("bar").value = job.done; } else $("bar").removeAttribute("value");
  setTimeout(poll, 1000);
}

function countsList(title, counts, labels) {
  const items = Object.entries(counts || {});
  return el("div", {}, el("p", { className: "sum-title" }, title),
    items.length ? el("dl", {}, items.map(([k, n]) => [el("dt", {}, badge(k, labels)), el("dd", {}, n)]))
      : el("p", { className: "muted" }, "none"));
}

function refRow(a) {
  const ref = a.reference;
  const m = a.matched;
  const cited = el("td", {}, ref.title || "", el("div", { className: "raw" }, ref.raw));
  const found = el("td", {});
  if (m) {
    found.append(el("div", {}, m.title || "(untitled)", m.year ? ` (${m.year})` : ""));
    const meta = el("div", { className: "muted" }, m.provider);
    if (m.doi) meta.append(" · ", link(`https://doi.org/${m.doi}`, `doi:${m.doi}`));
    else if (m.url) meta.append(" · ", link(m.url));
    found.append(meta);
  } else found.append(el("span", { className: "muted" }, "no record matched"));
  const issues = [
    ...a.mismatches.map((x) => el("li", {}, `${x.field}: cited "${x.cited}" → found "${x.found}"`)),
    ...a.providers.filter((p) => !p.ok).map((p) => el("li", {}, `${p.provider} failed${p.error ? `: ${p.error}` : ""}`)),
    a.note ? el("li", {}, a.note) : null,
  ].filter(Boolean);
  return el("tr", {},
    el("th", { scope: "row" }, ref.id),
    el("td", {}, badge(a.status, STATUS)),
    cited, found,
    el("td", {}, issues.length ? el("ul", {}, issues) : el("span", { className: "muted" }, "none")));
}

function opinion(role, o) {
  if (!o) return el("p", {}, `${role}: no answer (model call failed).`);
  return el("div", {},
    el("p", {}, el("strong", {}, `${role}: `), VERDICT[o.verdict] || o.verdict),
    o.rationale ? el("p", {}, o.rationale) : null,
    o.quote ? el("p", { className: "muted" }, `Quoted (not necessarily verified): "${o.quote}"`) : null);
}

function claimCard(c) {
  const kind = { full_text: "Checked against full text", abstract: "Checked against abstract only" }[c.evidence_kind];
  return el("article", { className: "card", "aria-label": `Claim ${c.citation_id} citing reference ${c.ref_id}` },
    el("header", {},
      el("span", { className: "ids" }, `${c.citation_id} → ref ${c.ref_id}`),
      badge(c.verdict, VERDICT),
      c.needs_review ? el("span", { className: "badge b-review" }, "Needs review") : null),
    el("blockquote", {}, c.sentence),
    c.quote ? [el("p", {}, el("strong", {}, "Verbatim quote from the source:")), el("blockquote", { className: "quote" }, c.quote)] : null,
    kind ? el("p", { className: "muted" }, kind) : null,
    c.source_url ? el("p", {}, "Source: ", link(c.source_url)) : null,
    c.note ? el("p", { className: "muted" }, c.note) : null,
    c.judge || c.skeptic ? el("details", {}, el("summary", {}, "Model rationales"), opinion("Judge", c.judge), opinion("Skeptic", c.skeptic)) : null);
}

function renderReport(r) {
  $("results-h").textContent = r.title ? `Report: ${r.title}` : "Report";
  $("export-json").href = `/api/jobs/${encodeURIComponent(jobId)}/export.json`;
  $("export-md").href = `/api/jobs/${encodeURIComponent(jobId)}/export.md`;
  $("warnings").replaceChildren(...r.warnings.map((w) => el("li", {}, w)));
  $("summary").replaceChildren(
    countsList("References", r.summary.references, STATUS),
    countsList("Claims", r.summary.claims, VERDICT));
  $("refs").tBodies[0].replaceChildren(...r.references.map(refRow));
  $("claims").replaceChildren(...(r.claims.length ? r.claims.map(claimCard) : [el("p", { className: "muted" }, "No citing sentences were checked.")]));
  showPermalink();
  show("results");
  $("results-h").setAttribute("tabindex", "-1");
  $("results-h").focus();
}

// Permanent link to this report; anyone holding it can read the report.
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
  const m = location.pathname.match(/^\/r\/([\w-]+)$/);
  if (m) startPolling(m[1]);
});
