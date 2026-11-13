# Design Brief: ClaimTrace — report and home

Answered from `docs/PLAN.md`, `README.md`, `AGENTS.md` and the owner's request; no interview.
Static mockups only — the product UI (`claimtrace/web/static/`) is not changed by this work.

## Problem

A scientist about to submit, or an editor about to accept, has a manuscript with 40 references and
~60 citing sentences. They cannot re-read every cited paper. What they fear is not a typo in a
year: it is a sentence that says a source shows something the source does not show, or a
reference that does not exist — and finding out from a reviewer, or after publication.

Automated checkers make this worse when they are confidently wrong. A tool that calls a real
paper "fake" because an API timed out, or calls a claim "supported" with no evidence, is worse
than no tool. So the reader's real question on every line of the report is: **"why should I
believe this?"**

## Users and jobs

| User | Job to be done | Success |
|---|---|---|
| Author, before submission | "Show me the few citations I must fix, and prove the rest are fine." | Fixes 3–5 items in 10 minutes, trusts the green ones without opening the PDFs. |
| Journal editor / reviewer | "Is this manuscript's citation practice sound, and where exactly is it not?" | Can point to a quote and a sentence in a review letter. |
| Research-integrity officer | "Which references are invented, retracted or misdescribed?" | Distinguishes *not found* from *could not check*. |

Context of use: desk, long sessions, often a second monitor next to the manuscript; sometimes a
phone, reading a shared link between meetings. Light ambient office light → light theme is the
default; dark mode is supported, not the identity.

## Solution (as an experience)

Paste or upload a manuscript; the report starts filling in at once and keeps streaming. Each claim
reads as a small piece of evidence: **the citing sentence, then the source's own words, verbatim**,
then a verdict that is visibly derived from that quote. Problems float to the top; the many fine
items are calm and collapsible. Whatever could not be checked says so in plain words and is never
dressed as a failure of the manuscript.

## Experience principles

1. **Evidence over verdict.** The verbatim quote is the hero of every claim card; the verdict
   label is its caption. No quote ⇒ no verdict shown (the product rule), and the design never
   lets a label stand alone big and coloured without its evidence.
2. **Calm over alarm.** Most items are fine. Severity is carried by order, words and shape, not by
   red everywhere. "Could not check" looks neutral, never like an error of the author.
3. **Honest progress over fake completeness.** Streaming results are shown as they land, with an
   explicit "still checking" state; nothing pretends to be final before it is.

## What we refuse

- Dashboard tropes: hero-metric tiles, donut charts of verdicts, sparklines, progress rings.
- Status by colour alone; traffic-light rows; coloured `border-left` stripes on cards.
- Icon-plus-heading card grids; eyebrow/kicker labels over headings; numbered "01/02/03" sections.
- Gradient text, glassmorphism, glow, decorative blur, hard offset shadows.
- Emoji or Unicode glyphs as icons (status marks are authored inline SVG).
- Monospace as a "technical" costume — mono only for identifiers and data (DOIs, arXiv ids, years,
  counts).
- Third-party font CDNs. All three directions use system font stacks; a self-hostable OFL face is
  named per direction as an optional upgrade.
- Rendering untrusted text as HTML. Mockups contain static, escaped text; the product uses
  `textContent`.
- Animated entrances on every element; any motion longer than 300 ms in the report.

## Status vocabulary (shared by all directions)

Every status = **word + shape + colour** (colour is the third, redundant channel).

| Status | Word shown | Shape |
|---|---|---|
| supported | Supported | filled circle |
| partly_supported | Partly supported | half-filled circle |
| unsupported | Not supported | circle with a cross |
| insufficient_evidence | Not enough evidence | dashed hollow circle |
| needs_review | Needs review (models disagree) | small flag, in addition to the verdict |
| verified (ref) | Verified | filled circle |
| metadata_mismatch (ref) | Details differ | two offset bars (≠) |
| unchecked (ref) | Could not check | dashed hollow circle |
| pending | Checking… | open circle with a static tick at 12 o'clock (no spinner) |

## Content in the mockups (real, from the live "Attention Is All You Need" run)

- Report: 40 references (38 verified, 1 details differ, 1 could not check); 58 claims.
- Problem reference: b26 Parikh et al., cited title "A decomposable attention model" vs found
  "A Decomposable Attention Model for Natural Language Inference" (Crossref, DOI
  10.18653/v1/d16-1244). Plus b0 Ba et al. "Layer normalization" — could not check (title could
  not be extracted to compare), shown as neutral.
- Supported claim: c6 → ConvS2S [9], full text, both models agree.
- Not supported: c30 → GNMT [38], "input length + 50" vs the source's "twice the source length";
  both models agree.
- Needs review: c5 → Parikh et al. [27]; judge *supported*, skeptic *partly supported* ("all but a
  few cases" vs "predominantly"); shown as the more cautious verdict.
- Streaming state: claims 41 of 58 checked; c32 and c33 still checking. Per-verdict claim counts
  are shown only for the finished report, never invented for a partial one.

## The three directions (and why these three)

The tension to explore is **where the evidence lives**: in a typeset page, in a measurement
ledger, or in the manuscript itself. Each answers "why should I believe this?" differently.

| | A — Journal | B — Bench | C — Margin |
|---|---|---|---|
| Metaphor | A well-set journal page / referee report | A lab instrument readout and notebook ledger | The manuscript with an editor's margin notes |
| Evidence lives | Large set quotation, sentence above it | A two-column row: claim ‖ source, aligned | In the margin, next to the sentence it checks |
| Type | Serif text (Charter / Iowan), small caps labels | Grotesque system sans + mono for data | Humanist sans body, serif for the quoted source |
| Colour | Warm paper, ink, one oxblood | Cool white, graphite, one deep teal | Pure white, pencil grey, one blue-black "pen" |
| Density | Low, one column | High, scannable, table-like | Medium, the text sets the rhythm |
| Best for | Authors reading top-to-bottom | Editors triaging many items | Reviewers working sentence-by-sentence |

Recommendation for the product: **A as the base** (it is closest to the current UI's paper/ink
palette, so lowest-risk), borrowing **C's inline sentence marks** for the claim card and **B's
aligned identifiers** for the reference list. The final choice is the owner's.

## Accessibility (all directions)

- WCAG AA: body text ≥ 4.5:1, large text and UI marks ≥ 3:1, checked for every status colour.
- Status never by colour alone (word + shape always present). Shapes are `aria-hidden`; the word is
  the accessible name.
- Visible focus ring (2–3 px, offset), skip link, landmarks, one `h1` per screen, `details/summary`
  for disclosure (keyboard-native).
- "Still checking" lives in an `aria-live="polite"` region; no spinner animation, static mark with
  `prefers-reduced-motion` respected for the one allowed pulse.
- Touch targets ≥ 44 px; no horizontal scroll at 360 px; text zoom to 200 % keeps one column.

## Responsive behaviour

Mobile-first, one column at < 48 rem. A: unchanged single column, quote stays full width.
B: the claim ‖ source row stacks (claim, then source), identifiers wrap. C: margin notes move
inline directly under the sentence they annotate.

## Out of scope

Implementing any direction in `claimtrace/web/static/`, the chat panel visuals, export formats,
dark-mode screenshots (tokens include dark palettes), logo work.
