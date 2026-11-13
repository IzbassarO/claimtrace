# Direction C — Margin

**The manuscript itself, with an editor's notes in the margin.**

## Idea

The report doesn't sit in a separate list of cards. It shows the author's own sentences as running
text, with each citation underlined, and the evidence written in the margin next to it, the way a
careful editor annotates a draft. The underline's *style* gives the verdict before you read
anything: solid means supported, dashed partly supported, wavy not supported, dotted no verdict (not enough
evidence, or not checked yet). The margin note opens with the source's own words, set in a serif so it reads as a
different voice from the manuscript. The verdict word and its shape sit above the quote as a
caption. Reviewers who work sentence by sentence never have to match a card back to a line,
because the claim and its evidence share a row. On phones the margin folds in, and each note
appears indented directly under the sentence it checks.

## Tokens (`tokens.css`)

- **Palette:** white page `#ffffff`; ink `#1b1d21`; pencil grey `#555a63` for apparatus; one
  blue-black "pen" `#1d3a6e` for links, actions, the needs-review flag and the claim highlight
  wash `#e8edf6`. Status colours: supported `#1f6b3a`, partly `#7a5600`, not supported `#a3221b`,
  not enough evidence / could not check `#555a63` (neutral, never alarming). Every text colour is
  ≥ 4.5:1 on white (measured: 6.5–16.9:1), and the input border is 3.25:1. The dark palette
  (`#16181b` ground) is a separate set of values, not an inversion; its text measures 5.6–14.1:1.
- **Type (system stacks only, no CDN):** humanist sans for the manuscript and UI (`Seravek`,
  Gill Sans Nova, Ubuntu, Calibri…), old-style serif for the quoted source (`Iowan Old Style`,
  Palatino…), mono only for identifiers (DOIs, arXiv ids, `c5`, `[27]`). Manuscript 17px at
  line-height 1.75, which leaves room for the underline marks. Quote 19px. Margin apparatus 15px.
- **Spacing:** 4px base (`--space-1` … `--space-10`). The text column is 38rem (~70ch), the
  margin column is 21rem, the page is 66rem, and a single 1px hairline separates them.
- **Radius:** 2–4px; nothing is rounded into a "card".

Optional upgrade (self-host, no CDN): **Source Serif 4** for quotes and **Atkinson Hyperlegible**
for body text. Both are under the SIL Open Font License 1.1, so the font files can be committed
next to the CSS.

## Motion

- When a note arrives during streaming, it fades in with a 4px rise over 200ms on
  `cubic-bezier(0.23, 1, 0.32, 1)`. Nothing else in the report moves.
- A pending note has a static open-circle mark with a slow 1.6s opacity pulse. There's no spinner.
- Buttons press to `scale(0.97)` over 140ms. Hover changes colour only.
- `prefers-reduced-motion: reduce` turns all three off, because the token durations drop to 0ms.

## What makes it distinct

- **The evidence sits next to the claim, not below it.** It's the only direction where you read
  a citing sentence and its quote side by side, in the manuscript's own order.
- **Status is carried by typography.** The underline style is the first channel; the word and the
  authored SVG shape repeat it, and colour comes last. You can scan the text for wavy lines
  without reading a single badge.
- **There's no container chrome.** Rows are separated by hairlines. The only furniture is a margin
  rule and the column grid.
- **The home page explains the method.** Its margin column holds a legend of the four underline
  styles, so the first screen already teaches you how to read the report.

## Files

`home.html`, `report.html` (static; real content from the live "Attention Is All You Need" run),
`tokens.css`, `shot-home.png` (1280), `shot-report.png` (1280), `shot-report-mobile.png` (390,
through an iframe harness because headless Chrome on macOS has a ~500px minimum viewport). The
screenshots use the light palette (`--blink-settings=preferredColorScheme=1`).

Trade-offs: the margin layout leaves white space under short sentences that have long notes, and
the report is less compact than direction B for triaging 60 items. A "problems only" filter would
help with that; it is left out of this mock.
