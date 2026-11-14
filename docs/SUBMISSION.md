# Submission — Vultr Community

The project was submitted on Vultr Community (Project Showcase → ClaimTrace) on 14 November 2026
at 09:42 ET, inside the US East build window, for the event HackGlobal 2026. This file records the
form fields as submitted. Every number below is measured and appears in `README.md`,
`docs/DECISIONS.md` or `eval/results/`.

| Field | Value |
|---|---|
| Project name | ClaimTrace |
| Industry / Domain | Education |
| Tags | Hackathons, Cloud Compute, Claude, Network |
| Tech stack | FastAPI, Docker, RAG & Vector Search, Python, AI Agents |
| Repository link | https://github.com/IzbassarO/claimtrace |
| Live demo | https://66-135-11-202.sslip.io |
| Build for an event | HackGlobal 2026 |
| Team | Izbassar Orynbassar (Syracuse University), Madina Sissenbay (Atyrau Oil and Gas University) |

Images, in order: the architecture diagram (`docs/architecture.png`), then `02-home.png`,
`03-report-sample.png`, `04-row-expanded.png` and `05-phone.png` from
`.design/claimtrace/submission/`.

## Short description

ClaimTrace audits the citations in a scientific manuscript: it checks that every reference exists and is described correctly, and that each cited source actually says what the citing sentence claims, with a verbatim quote as evidence. On 1,728 reference test cases it never marked a fabricated or altered reference as verified.

## Overview description

### Problem

Scientific writing now contains references that look real but do not exist, and real papers cited for things they never said. AI writing tools have made both more common, and reviewers have no fast way to check a reference list against the sources.

### Who it is for

Authors before submission, reviewers and editors, and anyone reading AI-assisted writing.

### How it works

Paste a manuscript or upload a PDF.

1. References. Each reference is looked up in Crossref, OpenAlex, arXiv, Semantic Scholar and doi.org. Title, authors, year and DOI are compared by deterministic code, not by a language model. The result is one of: verified, details differ, retracted, not found, could not check.
2. Claims. For each citing sentence ClaimTrace fetches the cited source (open full text where available, otherwise the abstract, and says which), retrieves the most relevant passages, and asks two models from different providers, a judge (Claude) and a skeptic (Kimi), whether the source supports the sentence.
3. Evidence. A verdict is shown only with a quote of at least five words found verbatim in the source. If the two models disagree, the claim is flagged for human review. A provider failure is reported as "could not check", never as a negative finding.
The report streams in as checks finish, puts problems first, and can be shared by link or exported.

### What we measured

- References: 1,728 test cases built from 399 real references (unchanged, wrong year, negated title, swapped first author, fabricated, retracted). Accuracy 0.956, with 0 false "verified" and 0 real papers called non-existent.
- Claims: SciFact dev set, n = 100. Judge + skeptic reached 0.90 accuracy with a 0.10 false-supported rate (judge alone: 0.87 and 0.167).
- Prompt injection: 10 kinds of injected instructions planted in source texts, 50 live checks. No contradicted claim was shown as supported, and no displayed quote contained injected text.
- Speed on the Vultr instance: a 40-reference PDF in 191 s, first result after 19 s (one run).

### Limitations

- A 40-reference paper takes about 3–5 minutes, depending on the LLM providers. Our 3-minute target was not reached reliably.
- Only open-access full text is used; otherwise the claim is checked against the abstract and labelled so.
- Citations of methods or datasets often come out "partly supported".
- If the skeptic's provider is unavailable, claims are checked by one model and labelled "judge only".
- In the 1,728-case run, 67 of 150 fabricated references came back "could not check" because an index failed under load. None came back "verified".
- Sentences that cite a work as an example or for comparison, not as evidence, can come out "not supported" even though nothing is wrong with the citation.

### Try it

Open https://66-135-11-202.sslip.io and press "Try a sample". A finished audit of "Attention Is All You Need" (40 references, 58 claims): https://66-135-11-202.sslip.io/r/Bmu5Xq9Q1H9aZ5TWWkzpLw

Built from scratch during HackGlobal 2026 by Izbassar Orynbassar (Syracuse University) and Madina Sissenbay (Atyrau Oil and Gas University).

## How did you use Vultr?

ClaimTrace runs entirely on one Vultr Cloud Compute instance, live at https://66-135-11-202.sslip.io. No managed databases or object storage; the instance is paid for with the hackathon credit.

Region: New Jersey (shown as "New York, US" in the deploy wizard), the closest to our team in Syracuse, NY and to users on the US East Coast.

Plan: Dedicated CPU vx1-g-4c-16g-240s (4 dedicated vCPU, 16 GB RAM, 240 GB local NVMe, Ubuntu 24.04). Dedicated cores because evidence retrieval (embeddings and a reranker on ONNX) is CPU-bound. 16 GB because GROBID peaks at 5.3 GB and the app at up to 1.2 GB with three jobs running. Local NVMe for Docker images and the source cache.

How it is wired: Docker Compose runs three containers: the app (FastAPI and the audit pipeline), GROBID (PDF parsing) and Caddy (automatic HTTPS through sslip.io, so no DNS setup). Reports are stored as files on the instance.

Hardening: SSH keys only, ufw open on 22/80/443, fail2ban, unattended upgrades, 4 GB swap. All containers restart on boot (verified with a reboot).

Sized from measurements, kept in a non-secret server.env: 3 concurrent audits with a visible queue, retrieval 2 threads × 2 slots, 8 skeptic calls in flight shared by all jobs, memory caps of 3 GB (app) and 6 GB (GROBID).

Measured on this instance: a 40-reference PDF audited in 191 s with the first result after 19 s; five PDFs submitted at once finished in 144–322 s with no restarts.

Guards for a public demo: a daily cap of 300 audits, a budget on paid scholarly-search calls, and usage counted per day and country only, with no IP addresses stored. Deploys are smoke-tested and roll back to the previous image on failure.
