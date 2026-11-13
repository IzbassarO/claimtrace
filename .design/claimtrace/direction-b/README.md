# Direction B — Bench

**Idea.** ClaimTrace as a bench instrument and its notebook. The report is a ledger in which every
claim is one row with aligned columns — *citation ‖ citing sentence ‖ source says ‖ verdict* — so
an editor can run an eye down the "source says" column and compare the paper's own words against
the manuscript's sentence at the same height, item after item. The header is a readout, not a
dashboard: two tick rulers (one tick per reference, one per claim) show what has been measured and
what is still pending, with the counts written next to them. Problems are grouped first ("Needs
your attention"), then supported, then "Still checking". Nothing is decorative: monospace appears
only where the content is an identifier or a count (citation ids, DOIs, arXiv ids, years, n / N).

Files: `tokens.css`, `home.html`, `report.html`, `shot-home.png`, `shot-report.png`,
`shot-report-mobile.png` (390 px, shot through an iframe harness because headless Chrome on macOS
will not lay out narrower than ~500 px).

## Palette (light is primary; dark in `tokens.css`)

| Token | Light | Use | Contrast on #fff |
|---|---|---|---|
| `--color-bg-primary` | `#f4f6f6` | page | — |
| `--color-bg-secondary` | `#ffffff` | ledger rows, form | — |
| `--color-bg-tertiary` | `#eaeeef` | column header, wells, model notes | — |
| `--color-text-primary` | `#14191c` | graphite ink, the quote | 17.7:1 |
| `--color-text-secondary` | `#4a555b` | citing sentence, captions | 7.7:1 |
| `--color-accent-primary` | `#0b5d57` | deep teal: links, primary button | 7.7:1 |
| `--color-status-supported` | `#1d6b3a` | filled circle | 6.5:1 |
| `--color-status-partly` / `-mismatch` | `#7a5600` | half circle / two offset bars | 6.6:1 |
| `--color-status-unsupported` | `#a3261b` | crossed circle | 7.4:1 |
| `--color-status-neutral` | `#56626a` | dashed circle (not enough evidence, could not check), pending | 6.3:1 |
| `--color-status-review` | `#8c2f6b` | flag | 7.7:1 |

Every text colour was checked against all four light backgrounds (lowest: tertiary text on
`#e3f0ee`, 4.7:1) and all dark backgrounds (lowest 5.4:1). Status = word + SVG shape + colour.

## Type

- Prose and UI: system grotesque — `ui-sans-serif, -apple-system, "Segoe UI", "Helvetica Neue", Arial`.
- Data only: `ui-monospace, "SF Mono", Menlo, Consolas`. Tabular numerals everywhere.
- Ramp (rem): 0.75 (column labels, mono) · 0.875 · **1 body** · **1.125 the source quote, weight 500** ·
  1.25 section · 1.875 / 2.25 page title.
- The quote is the largest and darkest text in a row; the citing sentence is secondary ink. That is
  the hierarchy: the evidence speaks, the manuscript is the question.
- Optional self-hosted upgrade: **IBM Plex Sans + IBM Plex Mono** (SIL Open Font License 1.1, files
  can be served from `/static/fonts/`, no third-party CDN). The system stack is the default.

## Spacing and shape

4 px base, dense: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96. Radius 2–4 px (instrument, not app).
Rows separated by 1 px rules inside one bordered ledger; no cards, no shadows on content.
Desktop row grid: `6.5rem | 1fr | 1.35fr | 12rem`; below 48 rem each row stacks and the hidden
column header is replaced by a small label above each cell.

## Motion

- No entrance animation on load; the readout is static.
- When a streamed row arrives, its background fades from `--color-accent-tint` to white in 260 ms
  (`@starting-style`, `cubic-bezier(0.23, 1, 0.32, 1)`), so the eye finds what is new. Disabled
  under `prefers-reduced-motion`.
- The tick ruler fills without animation; the pending mark is static (no spinner).
- Buttons: 120 ms colour, `scale(0.97)` on press.
- `details` for judge / skeptic opens instantly (used often, so no animation).

## What makes it distinct

- **Alignment is the argument**: sentence and source side by side at the same height, across rows —
  the only direction built for scanning 58 claims fast.
- **Readout header** (tick rulers + counts) instead of summary tiles; it is honest about partial
  progress and refuses to show per-verdict counts before the run is complete.
- References show a *field / cited / found* table, the way an instrument shows expected vs measured.
- Home is a form beside a datasheet ("What is measured" + readout key): the method is stated before
  you run it.

Trade-off: the densest of the three; on a phone it becomes a long stacked list and loses its main
advantage (side-by-side comparison).
