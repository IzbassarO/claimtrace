# Direction A — Journal

**Idea.** The report is a well-set journal page, read top to bottom like a referee's letter. Each
claim is a short typeset passage: the author's sentence in a quieter ink with the citation marker
highlighted, then the cited source's own words set large as a pull quote — the largest text on the
card — and only then, as a caption, the verdict. Problems come first under plain headings ("Claims
that need you", "Still checking", "Claims that hold up"), and the summary is a sentence, not a
dashboard. Authority comes from typography and restraint, the way a journal earns trust.

Files: `tokens.css` (all values), `style.css` (components, tokens only), `home.html`,
`report.html`, screenshots `shot-home.png`, `shot-report.png` (1280 px), `shot-report-mobile.png`
(390 px).

## Palette (light; dark in `tokens.css`)

| Token | Value | Use | Contrast on paper |
|---|---|---|---|
| `--color-bg-primary` | `#faf8f3` | warm paper | — |
| `--color-text-primary` | `#1c1a17` | ink | 16.4:1 |
| `--color-text-secondary` | `#57524a` | citing sentence, captions | 7.3:1 |
| `--color-accent-primary` | `#8f2a1c` | oxblood: the one action, focus, needs review | 7.9:1 |
| `--color-status-supported` | `#2d6a3a` | filled circle + "Supported" | 6.1:1 |
| `--color-status-partly` | `#7a5410` | half circle + "Partly supported", details differ | 6.4:1 |
| `--color-status-unsupported` | `#9a2a1d` | crossed circle + "Not supported" | 7.3:1 |
| `--color-status-insufficient` | `#57524a` | dashed circle; also "Could not check" — neutral on purpose | 7.3:1 |
| `--color-mark-highlight` | `#f1e2b8` | background of the `[n]` marker in the citing sentence | ink 13.5:1 |

## Type

System serif stack: Charter → Bitstream Charter → Iowan Old Style → Sitka Text → Cambria → Georgia.
Body 17 px / 1.55, measure 40 rem (~68 ch). Source quote 22 px / 1.45 with hanging punctuation.
Labels in true small caps (`all-small-caps`, +0.06 em). Mono (`ui-monospace`) only for arXiv ids
and DOIs. Optional upgrade: **Source Serif 4** (SIL Open Font License 1.1), self-hosted as WOFF2.

## Spacing

8 px base, editorial multipliers: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96 px. Cards are separated
by a 1 px hairline and 32 px of air, never by boxes. More space above a heading than below it.

## Motion

- Almost none. Results that stream in appear without entrance animation (they arrive tens of
  times per report; animating them would read as noise).
- The one moving thing: the 2 px progress rule grows with `transform: scaleX()` in 280 ms,
  `cubic-bezier(0.23, 1, 0.32, 1)`; disabled under `prefers-reduced-motion`.
- Buttons: `scale(0.97)` on press, 120 ms ease-out. `details` open/close is instant.

## What makes it distinct

It has no cards, chips or panels at all — only type, hairlines and one highlight. The quote is
literally the biggest thing on the screen, so a verdict can never look more important than its
evidence. It is also the closest to the current product UI (paper / ink / one accent, Charter), so
it is the cheapest to adopt.

## Known limits

- Long reports (58 claims) become a long scroll; it relies on "problems first" ordering and a
  collapsed "hold up" section rather than filters.
- Small caps fall back to faux small caps on fonts without `smcp`.
