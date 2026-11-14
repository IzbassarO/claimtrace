# Submission draft — Vultr Community

*Draft for the HackGlobal 2026 submission form. Every number below is measured and appears in
`README.md` / `docs/DEPLOY.md`; nothing here is projected. Fill in the video link before sending.*

## Name

**ClaimTrace** — does each cited source actually say what the sentence citing it claims?

## Description

ClaimTrace audits the citations in a scientific manuscript. Paste the text or upload a PDF and, for
every reference, it checks against Crossref, OpenAlex, arXiv, Semantic Scholar and doi.org that the
work exists and is described correctly — title, authors, year and DOI are compared by deterministic
code, not by a language model. For every citing sentence it fetches the cited source (open full
text where available, otherwise the abstract, and says which), finds the most relevant passages,
and has two models from different providers — a judge and a skeptic — decide whether the source
supports the sentence. A verdict is shown only together with a quote of at least five words found
verbatim in the source; otherwise the claim is marked "insufficient evidence", never "unsupported".

The report streams in as checks finish, puts what needs attention first, and can be shared by link
or exported. The design rule throughout is that a failure is never a negative finding: a provider
that times out or rate-limits produces "unchecked", not "fake". We measured it: on 1728 reference
cases there were 0 false "verified" and 0 real papers called non-existent (real ML papers that
still came back "not found" after launch were traced and fixed); on SciFact (n = 100)
judge + skeptic reached 0.90 accuracy with a 0.10 false-supported rate; and in a live test with 10
kinds of prompt injection planted in source texts, no contradicted claim was shown as supported and
no displayed quote contained injected text.

## Stack

Python 3.12, FastAPI, asyncio streaming pipeline; GROBID for PDF structure; BM25 + bge-small dense
retrieval + cross-encoder rerank (CPU, fastembed); Claude Haiku (reference-field extraction), Claude
Sonnet (judge) and Kimi
(skeptic) through LangChain with pydantic-validated JSON; plain HTML/CSS/JS front end (untrusted text
via `textContent` only); Docker Compose with Caddy for automatic HTTPS; pytest (offline, adversarial
suites) and a reproducible eval harness; GitHub Actions CI.

## How we used Vultr

ClaimTrace runs on one Vultr Cloud Compute instance at https://66-135-11-202.sslip.io: Dedicated
CPU plan `vx1-g-4c-16g-240s` (4 dedicated vCPU, 16 GB RAM, 240 GB local NVMe, Ubuntu 24.04) in the
New Jersey region ("New York, US" in the wizard), the closest to our team in Syracuse, NY and to
users on the US East Coast. We chose dedicated cores because evidence retrieval (embeddings and a
reranker on ONNX) is CPU-bound, 16 GB because GROBID peaks at 5.3 GB and the app at up to 1.2 GB
with three jobs, and local NVMe for Docker images and the source cache. There are no managed
databases or storage: one instance, paid with the hackathon credit, with Docker Compose running the
app, GROBID and Caddy (Let's Encrypt via sslip.io, so no DNS setup). We hardened it (SSH keys only, ufw, fail2ban,
unattended upgrades, swap) and verified the containers come back after a reboot. We sized it from
measurements, not guesses, in a non-secret `server.env`: 3 concurrent audits with a visible queue,
retrieval 2 threads × 2 slots, memory caps of 3 GB for the app and 6 GB for GROBID after measuring
peaks of 1.2 GB and 5.3 GB with five PDFs at once. On this machine a 40-reference paper is audited
in 191 s with the first result after 19 s. Cost guards keep a public demo safe: a daily cap on audits
with a clear message, a budget on paid scholarly-search calls, and usage counted only per day and
country, with no IP addresses stored. Deploys are smoke-tested with automatic rollback.

## Links

- Live demo: https://66-135-11-202.sslip.io (press "Try a sample")
- Code: https://github.com/IzbassarO/claimtrace
- Architecture: `docs/architecture.png` in the repository
- Demo video: *(add link)*

## Team

- Izbassar Orynbassar — Syracuse University
- Madina Sissenbay — Atyrau Oil and Gas University
