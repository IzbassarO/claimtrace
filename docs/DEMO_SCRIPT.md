# Demo video script (2:00)

Screen recording at 1280×800, browser zoom 100 %, light theme. Two tabs open before recording:

- **Tab A:** https://66-135-11-202.sslip.io (home page)
- **Tab B:** https://66-135-11-202.sslip.io/r/Bmu5Xq9Q1H9aZ5TWWkzpLw (a finished live audit of
  "Attention Is All You Need": 40 references, 58 claims, run on the Vultr server; done in 191 s)

Have `attention.pdf` (arXiv 1706.03762) on the desktop for the upload. Speak calmly; the timings
leave a little slack.

| time | on screen (what to click) | what to say |
|---|---|---|
| 0:00–0:12 | Tab A, home page, no clicks | "A citation is a promise: the paper you cite says what you claim it says. Nobody checks that promise at scale. ClaimTrace does." |
| 0:12–0:22 | Click **Try a sample** | "Here is a short manuscript. In one screen: one reference does not exist, one claim is not supported, one is fine." |
| 0:22–0:40 | Click the row **Not supported — "Ioannidis proved that all published research findings are false"** | "The sentence says Ioannidis *proved* that *all* findings are false. ClaimTrace fetched the cited paper and shows the exact words: 'most claimed research findings are false'. Most, not all. Every verdict comes with a quote found word for word in the source — no quote, no verdict." |
| 0:40–0:52 | Click the row **Source not found** tagged *reference not found* (songbirds, Varkonyi 2019) | "This reference was invented. Crossref, OpenAlex, arXiv and Semantic Scholar have no such paper. Existence is decided by code, not by a language model, and if a lookup fails we say 'unchecked' — never 'fake'." |
| 0:52–1:10 | Click **Audit another manuscript** → **Upload files** → choose `attention.pdf` → **Audit citations**. Wait until the first references appear (~20 s; if the queue shows a position, say so) | "Now a real paper, 40 references. Results stream in as each check finishes — the first ones in about twenty seconds. The whole audit takes about three to five minutes, so here is one we ran earlier." |
| 1:10–1:35 | Switch to **Tab B**. Point at the headline and the summary band, then at a **Needs review** row with its "Judge: … · Skeptic: …" line; click it and open **Details** | "Forty references, fifty-eight claims, problems first. Two independent models from different providers check each claim — a judge and a skeptic. 'Not supported' means both agree, with a quote. When they disagree, we don't pick a side: the claim says 'Needs review' and shows both verdicts. Details show what each one said." |
| 1:35–1:48 | Scroll once to the collapsed **Supported** and **References** headers, then back up; click **Copy link** | "Everything else is one line, collapsed. The report is a link you can send to a co-author, or download." |
| 1:48–2:00 | Back to Tab A header (or the README "How we used Vultr") | "It runs on Vultr Cloud Compute — four vCPUs, sixteen gigabytes, Docker, automatic HTTPS — with a daily cap and no IP addresses stored. ClaimTrace: check every citation against its source." |

## Fallbacks

- **Upload is slow or the queue is long:** skip waiting at 0:52 — say "results stream in as they
  are checked" while the progress shows, then go to Tab B.
- **Server unreachable:** record with the local Docker stack (`DOMAIN=localhost docker compose up
  -d`) and say "the same app that runs on Vultr".
- **A different row order in Tab B:** any row marked *Not supported* or *needs review* works for
  1:10–1:35; avoid claiming anything about a row you have not opened.

## Do not say

- That it is fast enough for every paper (a 40-reference paper takes about 3–5 minutes).
- That it detects every fabricated reference (some come back `unchecked` when an index fails).
- Numbers that are not in the README.
