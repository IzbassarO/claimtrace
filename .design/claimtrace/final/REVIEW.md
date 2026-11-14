# Direction B "Bench" in the product: self-review

Scope: `claimtrace/web/static/{index.html,style.css,app.js}`. Screenshots in this folder come from the
real app served by `scripts/replay_server.py` (no LLM calls) and were taken with headless Chrome by
`shoot.sh`. Every report screen is rendered from report JSON: the saved sample, the large-PDF run
(`.cache/live/final-run.json`, 40 refs and 58 claims), an empty report, and two synthetic reports
(`long`, `refs200`) that `replay_server.py` generates.

| File | What it shows |
|---|---|
| `home-light.png`, `home-dark.png`, `home-mobile.png` | Home with the upload drop zone, the "Paste text" tab and "Try a sample" |
| `report-sample-light.png` | Saved sample, with the first Details opened (`open=1`) |
| `report-final-light.png`, `report-final-dark.png` | Large PDF, desktop, with the first Details opened |
| `report-mobile-390.png` | Large PDF at 390 px (rows become cards; shot through an iframe because headless Chrome on macOS will not lay out narrower than about 500 px) |
| `report-empty.png` | Empty report screen |
| `long-cases.png`, `long-cases-mobile.png` | A 5-line sentence, an 8-line quote, a long title, an unbroken URL and DOI, needs review |
| `refs-200.png` | 200 references and 28 claims |
| `stream-5s.png`, `stream-30s.png`, `stream-120s.png`, `stream-finished.png` | One replayed job (about 150 s) in light theme, polled in a fresh browser at 5, 30 and 120 s and after it ends (158 s) |

## After the fresh-eyes review of PR #105

1. **Missing or retracted reference ⇒ attention.** A claim that cites a `not_found` or `retracted`
   reference is now always in "Needs your attention". Its verdict mark is drawn neutral (dashed
   circle), never the green filled one, whatever the source text said. Claims citing
   `metadata_mismatch` or `unchecked` references stay in the middle group with a note. In the
   sample, c3 (the fabricated reference) is now in attention; in `refs-200`, c7 → ref 49 is no
   longer green.
2. **Same order live and on the permalink.** Arrival order is kept only while the run is going. On
   the final render, rows are sorted once, worst first: verdict or status, then needs review, then
   the cited reference's status, with arrival order breaking ties. The permalink uses the same sort.
3. **Headline in plain words.** A primary-ink headline names the worst counts, for example
   "1 reference not found · 7 claims not supported · 21 claims need review (5 claims are in both
   counts)." When claim counts overlap, the headline says so. The old "partly supported, not
   checkable, or citing a doubtful reference" phrase is gone from the summary. When any reference is
   not found or retracted, the References section moves above Claims (a DOM move, so focus order
   follows).
4. **Filters scroll.** Clicking a counter scrolls the filtered section into view (smooth unless
   reduced motion is on) and keeps focus on the pressed counter (`preventScroll`).
5. **Stream shots retaken in light theme** at 5, 30 and 120 s, plus a finished shot. The replay
   page polls a real job through `/dev/show/home?theme=light&job=<id>`.
6. **Mismatch wording.** The note now reads "Details differ from the found record (year)." on both
   the reference row and the claim citing it, matching the "Details differ" label.
7. **Theme button.** It names the theme it switches to ("Light theme" while dark is on). I dropped
   `aria-pressed` on purpose: a toggle button whose label changes with its state would be read as
   "Light theme, pressed" while dark is on, which contradicts itself (WAI-ARIA APG: a toggle keeps
   a constant label). The label alone carries the state.
8. **Nothing hard-coded.**
   - The retention sentence comes from a new `GET /api/info` → `{"max_reports": N}`. If that
     request fails, the sentence falls back to "kept for a limited number of recent reports".
   - The "Try a sample" description is counted from `sample-report.json` itself.
9. **Ticks and running layout.**
   - Done ticks are solid primary ink and pending ticks are hollow, outlined in the input-border
     colour. They differ by shape and by contrast: 4.9:1 apart in light, 3.7:1 in dark.
   - While running, the exports band is collapsed (`display: none`) instead of leaving an
     invisible gap.
   - The duplicated progress bar on the left is removed; the rulers are the only progress
     display.

## Against the brief and B's README

Done:
- **Ledger, aligned columns.** Rows run *citation, citing sentence, source says, verdict*. The quote
  is the largest and darkest text in the row and the sentence is secondary ink. References use
  *reference, cited as, found, status*, and the Details panel shows a field / cited / found table.
- **Readout instead of tiles.** A plain-words headline, then two tick rulers (claims, references)
  with `n / N checked`.
  The per-status counters appear only once that section is final; while it runs, the meter says
  "Counts by verdict appear when every claim is checked." The brief's rule against showing counts
  before the run is complete is kept.
- **Problems first.** "Needs your attention" is always open. Then comes the middle group (partly
  supported, not checkable, or citing a doubtful reference), then the fine items in a collapsed
  "Show N supported claims" / "Show N verified references" disclosure.
- **Status = word + shape + colour.** Each status has a CSS-drawn shape: filled circle, half circle,
  crossed circle, dashed circle, two offset bars, flag, and a pending tick. From C, verdict words
  also carry an underline style: supported solid, partly dashed, not supported wavy, not enough
  evidence dotted. This is a fourth, non-colour channel.
- **Vocabulary from the brief.** Not supported, Not enough evidence, Details differ, Could not check,
  Needs review. A lookup failure reads "Could not check (lookup failed)" with "… did not answer;
  this is not a finding against the reference", never as a negative finding.
- **Progressive disclosure.** By default a row shows sentence, quote, verdict and one reason line
  (the first sentence, clamped to one line on desktop and two in cards). "Details" holds the judge
  and skeptic rationales, other excerpts the models quoted, the source (kind, section, URL, note),
  and the reference (raw, status, mismatch table, lookups per provider). Warnings and per-stage
  timings (`report.timings`) live in "Technical details". Opened disclosures are tracked by key and
  survive every stream re-render.
- **Counters are filters.** Each counter is a `<button aria-pressed>`. A click shows only matching
  rows, as one ledger without groups. A `role=status` line says "Showing only claims: supported."
  and offers "Show all". Focus stays on the pressed counter across the re-render.
- **Themes.** Light by default, with `prefers-color-scheme` and a manual toggle (its label names the
  target theme) stored in `localStorage` (inside try/catch). An inline head script applies the stored choice
  before first paint.
- **Tokens.** Every colour, size, space, radius, duration and column width is a custom property on
  `:root`. Component rules use only `var(...)`; the remaining literals are `0`, `1fr`, gradient
  stop percentages inside the shape definitions, and `!important` on `[hidden]`.
- **Arrival highlight.** Only rows that are new in this render get `.new`, which fades from the
  accent tint in 260 ms via `@starting-style`. No spinner and no entrance animation.

Gaps against the brief / README:
- **Shapes are CSS, not inline SVG.** The brief asks for authored SVG. The CSS shapes kept the fake
  DOM tests simple, but in Windows forced-colours mode the filled, half and crossed shapes lose
  their backgrounds; the word and underline style remain.
- **No "Still checking" rows.** The mockup lists the claims in flight. The partial report contains
  only finished items, so the UI shows pending ticks and "Still checking…" next to the heading
  instead. Showing in-flight rows would need a contract change (the pipeline would have to expose
  pending pairs).
- **Pending ticks only for the current stage.** The job API gives `done/total` for that stage only.
  For the other section the meter says "n checked so far".
- **Home placement of "Try a sample".** It sits at the right of the tab row. At 390 px it wraps onto
  its own line inside the tab strip, which is a little awkward.
- **No optional webfont.** IBM Plex is not shipped; the system stack is used.
- **Whole-section re-render on every poll** (as before). It is cheap at 58 or 200 rows, but a focused
  link inside a row (for example "source") loses focus when a new snapshot arrives. Counters and
  Details summaries are restored.

## Accessibility audit

**Keyboard path, home.** Skip link → brand → theme toggle → "Upload files" tab (roving tabindex;
←/→/Home/End switch tabs) → "Try a sample" → file input (visually hidden inside the drop zone, which
shows a focus ring via `:focus-within`; Enter/Space opens the picker) → "Audit citations". Drag and
drop is an extra; the input is the real control. After "Paste text" the textarea follows the tabs.

**Keyboard path, report.** Focus moves to the report `h1` once, when it first appears, and never
again during streaming. DOM order: saved notice / Run live → summary → claims counters → reference
counters → Download JSON / Markdown → Copy link → Audit another → permalink → "Show all" (when a
filter is on) → claims rows (source link, Details) → "Show N supported claims" → references → Technical
details → chat. When a reference is not found or retracted, the References section comes before Claims in
the DOM as well as on screen. The meters sit to the right of the summary on wide screens and come
straight after it in the DOM.

**ARIA.**
- Tabs: `role=tablist/tab/tabpanel`, `aria-selected`, `aria-controls`, `aria-labelledby`.
- Theme: an action label ("Dark theme" / "Light theme"), no `aria-pressed` (see fix 7).
- Counters: `aria-pressed`, in a list labelled "Filter by status".
- Rulers: `role=img` with an `aria-label` such as "41 of 58 checked".
- Disclosures: native `details/summary`.
- Live regions: stage line (`aria-live=polite`), connection notice and filter line (`role=status`),
  form errors (`role=alert`), chat log (polite).
- Ledger column headers are `aria-hidden`. Each cell instead carries its column name as a visually
  hidden label, which becomes visible above each cell in card mode.
- Mark shapes are pseudo-elements, so only the word is read.

**Contrast** (WCAG 2.x ratios, computed from the tokens in `style.css`; text needs 4.5:1).

| Text token | Light on page / row / well / tint | Dark on page / row / well / tint |
|---|---|---|
| text-primary | 16.3 / 17.7 / 15.2 / 15.2 | 14.9 / 13.9 / 12.3 / 11.5 |
| text-secondary | 7.1 / 7.7 / 6.6 / 6.6 | 8.7 / 8.1 / 7.2 / 6.7 |
| text-tertiary | 5.1 / 5.6 / 4.8 / 4.7 | 6.9 / 6.5 / 5.7 / 5.4 |
| link / accent | 7.1 / 7.7 / 6.6 / 6.6 | 9.6 / 9.0 / 7.9 / 7.4 |
| status-supported | 6.0 / 6.5 / 5.6 / 5.6 | 9.1 / 8.5 / 7.5 / 7.1 |
| status-partly / mismatch | 6.1 / 6.6 / 5.7 / 5.7 | 9.5 / 8.9 / 7.9 / 7.4 |
| status-unsupported | 6.8 / 7.4 / 6.3 / 6.3 | 7.6 / 7.1 / 6.2 / 5.9 |
| status-neutral | 5.8 / 6.3 / 5.4 / 5.4 | 7.4 / 6.9 / 6.1 / 5.7 |
| status-review | 7.1 / 7.7 / 6.6 / 6.6 | 7.6 / 7.1 / 6.3 / 5.9 |
| button text on accent | 7.7 | 9.9 |

Non-text UI needs 3:1:

| Element | Light | Dark |
|---|---|---|
| Field, drop zone and pending-tick borders (`--color-border-input`) on row / page / well | 3.6 / 3.3 / 3.1 | 3.8 / 4.0 / 3.3 |
| Done ticks on the page | 16.3 | 14.9 |
| Done vs pending tick | 4.9 | 3.7 |
| Focus ring | 7.1 | 9.6 |

The ledger rules (`--color-border-primary`, 1.6:1 light) are decorative separators, not controls.

**Reduced motion.** Under `prefers-reduced-motion: reduce`, all transitions are off: no arrival fade
and no press scale. Nothing else moves.

**200% zoom.** At 1280 px wide and 200% zoom the viewport is 640 CSS px. That is below the 48rem
breakpoint, so the ledger becomes single-column cards (the same layout as `report-mobile-390.png`)
with no horizontal scroll. Long URLs, DOIs and titles wrap (`overflow-wrap: anywhere`). I did not
take a separate screenshot at 200% zoom.

**Still missing.**
- No automated axe run.
- Not tested with VoiceOver or NVDA.
- The forced-colours mode issue described above.
- Focus is lost on in-row links during streaming.
- Tap targets: buttons, summaries and counters are at least 44 px. Inline links inside the
  provenance text are smaller.
