# Plan

Window: Fri 2026-11-13 12:20 ET → Sat 2026-11-14 23:59 ET. **Code freeze Sat 18:00 ET.**

## Milestones (ET)

| When | Milestone | Needs |
|---|---|---|
| Fri 18:00 | Live end-to-end locally: pasted text → report (abstract-level claim checks) | T1 text path, T2, T3 abstract path, T4, T5 minimal UI, pipeline |
| Fri 22:00 | Docker stack (app + grobid + caddy) built and verified locally; CI green | T7 |
| Sat 12:00 | PDF via real GROBID, full text (Europe PMC / arXiv / Unpaywall), eval numbers, chat | T1 PDF, T3 full text, T6, T5 chat |
| Sat 15:00 | Deployed on Vultr Compute over HTTPS | T7 + lead |
| Sat 18:00 | Freeze. README final (numbers, architecture, limits, Vultr, AI assistance) | lead |

## Process

1. Lead writes foundation (this PR): docs, `core/` contracts, pipeline, module stubs, agents.
2. Developers T1–T7 run in parallel, each in its own worktree/branch, only in their paths,
   finishing with a PR. Lead reviews each PR, reruns ruff + pytest (+ live tests), reports to the
   human which PRs are ready and in what merge order. **Only the human merges into `main`.**
3. After each merge a tester agent hunts for confidently-wrong results and lands them as
   failing tests (`xfail(strict=True)` with the bug described), which the owning developer fixes.
4. Status is logged with real timestamps in `docs/STATUS.md` on its own PR.

## Cut list (in this order, only if a milestone is at risk)

1. Report chat
2. Export (keep JSON download; drop Markdown)
3. LaTeX `\cite{}` support
4. Semantic Scholar provider (keep Crossref + OpenAlex + arXiv)
5. Hybrid retrieval → BM25 only

**Never cut:** reference audit, verbatim quote gate, deployment, evaluation numbers.
