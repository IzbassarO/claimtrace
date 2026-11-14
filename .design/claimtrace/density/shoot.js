// Playwright snippet (run through the Playwright MCP `browser_run_code` tool, or `page` from any
// Playwright script) against scripts/replay_server.py on port 8795. Saves the density screenshots
// next to this file and returns the measured heights.
async (page) => {
  const B = "http://127.0.0.1:8795";
  const OUT = "/Users/izbassar/Documents/Projects/claimtrace-hg/.worktrees/feat/report-density/.design/claimtrace/density";
  const measure = () => page.evaluate(() => {
    const rows = [...document.querySelectorAll(".row-head")].filter((b) => b.offsetParent && b.getAttribute("aria-expanded") === "false");
    const att = document.querySelector("#claims h3 + .ledger");
    const attRows = att ? [...att.children] : [];
    return {
      doc: document.documentElement.scrollHeight, // never below the viewport height
      content: Math.ceil(document.body.getBoundingClientRect().height),
      maxCollapsedRow: Math.max(0, ...rows.map((r) => Math.round(r.closest(".row").getBoundingClientRect().height))),
      problemRowsFullyOnFirstScreen: attRows.filter((r) => r.getBoundingClientRect().bottom <= innerHeight).length,
      problemRowsStartingOnFirstScreen: attRows.filter((r) => r.getBoundingClientRect().top < innerHeight).length,
    };
  });
  const open = async (name, theme, w, h) => {
    await page.setViewportSize({ width: w, height: h });
    await page.goto(`${B}/dev/show/${name}?theme=${theme}`);
    await page.waitForSelector("#claims .row-head");
    await page.waitForTimeout(300);
  };
  const out = {};
  for (const theme of ["light", "dark"]) {
    const sfx = theme === "dark" ? "-dark" : "";
    await open("sample", theme, 1280, 800);
    out[`sample-1280${sfx}`] = await measure();
    await page.screenshot({ path: `${OUT}/sample-1280${sfx}.png` });
    await open("final", theme, 1280, 800);
    out[`large-1280${sfx}`] = await measure();
    await page.screenshot({ path: `${OUT}/large-collapsed-1280${sfx}.png`, fullPage: true });
    // one row expanded: the second attention row, clicked (the first one is open by default; close it)
    await page.locator("#claims .row-head").first().click();
    await page.locator("#claims .row-head").nth(1).click();
    await page.locator("#claims .row-head").nth(1).evaluate((b) => b.scrollIntoView({ block: "center" }));
    await page.waitForTimeout(200);
    await page.screenshot({ path: `${OUT}/row-expanded-1280${sfx}.png` });
    await open("final", theme, 390, 844);
    out[`large-390${sfx}`] = await measure();
    await page.screenshot({ path: `${OUT}/phone-390${sfx}.png` });
  }
  return out;
}
