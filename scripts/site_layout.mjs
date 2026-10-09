#!/usr/bin/env node
// Browser checks for the rendered website (_site/): what scripts/check_site.py cannot see in the
// HTML. It reads the pages to visit as JSON on stdin (`check_site.py --layout-pages` prints them),
// serves _site over HTTP, and opens every page at desktop (1280x800) and phone (390x844) widths in
// light and dark, checking that:
//   * nothing sticks out past the right edge of the viewport (content wider than the page that
//     is not inside its own horizontal scroll container, such as a code block or a wrapped table);
//   * no text is cut off: a line partly hidden by a box that clips its overflow (an ellipsis is
//     fine, and wholly hidden text such as a closed menu is not counted);
//   * every Mermaid diagram was drawn (each pre.mermaid holds an <svg>);
//   * the page took the colour scheme the browser asked for (body.quarto-dark / quarto-light);
//   * the page threw no uncaught errors.
// It saves a screenshot of the top of every view to the output directory for a person to review;
// pages.yml uploads them on pull requests. It needs Chrome or Chromium (CHROME_PATH, or the usual
// install locations) and the network, because the diagrams load Mermaid from jsDelivr, so it is
// not part of the offline `make ci`.
// Usage: uv run python scripts/check_site.py --layout-pages | node scripts/site_layout.mjs [--site _site] [--out _site_checks]

import { existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve, sep } from "node:path";
import { parseArgs } from "node:util";
import puppeteer from "puppeteer-core";

const { values: args } = parseArgs({
  options: {
    site: { type: "string", default: "_site" },
    out: { type: "string", default: "_site_checks" },
  },
});
const SITE = resolve(args.site);
const OUT = resolve(args.out);
const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "phone", width: 390, height: 844, isMobile: true, hasTouch: true },
];
const SCHEMES = ["light", "dark"];
const DIAGRAM_TIMEOUT_MS = 20000;
const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
];
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".yml": "text/plain; charset=utf-8",
};

function fail(message) {
  console.error(`site_layout: ${message}`);
  process.exit(2);
}

function serve(root) {
  const server = createServer((req, res) => {
    let path;
    try {
      path = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname));
    } catch {
      res.writeHead(400).end(); // a malformed %-escape must not crash the check
      return;
    }
    let file = join(root, path);
    if (!file.startsWith(root + sep) && file !== root) {
      res.writeHead(403).end();
      return;
    }
    if (existsSync(file) && statSync(file).isDirectory()) file = join(file, "index.html");
    if (!existsSync(file)) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream" });
    res.end(readFileSync(file));
  });
  return new Promise((ok) => server.listen(0, "127.0.0.1", () => ok(server)));
}

// Runs in the page: elements that reach past the viewport's right edge and are not clipped by a
// scroll container that itself fits. Only the outermost offender of each subtree is reported.
function overflowingElements() {
  // The layout width, not innerWidth: on a phone innerWidth grows to fit content that is too wide.
  const limit = document.documentElement.clientWidth + 1;
  const describe = (el) =>
    el.tagName.toLowerCase() +
    (el.id ? `#${el.id}` : "") +
    [...el.classList].slice(0, 2).map((c) => `.${c}`).join("");
  const found = [];
  for (const el of document.body.querySelectorAll("*")) {
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0 || box.right <= limit) continue;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.position === "fixed") continue;
    let contained = false;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const ps = getComputedStyle(p);
      if (ps.position === "fixed" || ps.display === "none") {
        contained = true; // off-canvas menus and the like
        break;
      }
      if (ps.overflowX !== "visible") {
        contained = p.getBoundingClientRect().right <= limit;
        break;
      }
    }
    if (contained || found.some((f) => f.contains(el))) continue;
    found.push(el);
  }
  return found.slice(0, 5).map(
    (el) => `${describe(el)} reaches ${Math.round(el.getBoundingClientRect().right)}px`,
  );
}

// Runs in the page: text that a clipping box (overflow hidden or clip, no ellipsis) cuts through,
// so a reader sees part of a line. Wholly hidden text (closed menus) does not count.
function clippedText() {
  const found = [];
  for (const box of document.body.querySelectorAll("*")) {
    const style = getComputedStyle(box);
    if (!["hidden", "clip"].includes(style.overflowX) || style.textOverflow === "ellipsis") continue;
    const edge = box.getBoundingClientRect();
    if (edge.width === 0 || edge.height === 0) continue;
    const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent.trim()) continue;
      range.selectNodeContents(node);
      const cut = [...range.getClientRects()].some(
        (r) => r.width > 0 && r.left < edge.right - 1 && r.right > edge.right + 1,
      );
      if (cut) {
        const label = box.tagName.toLowerCase() + [...box.classList].slice(0, 2).map((c) => `.${c}`).join("");
        found.push(`${label} cuts off "${node.textContent.trim().slice(0, 40)}"`);
        break;
      }
    }
    if (found.length >= 5) break;
  }
  return found;
}

// One view; a navigation timeout or a crashed page becomes a problem for this view only, so
// every other page, width and scheme is still checked and reported.
async function checkView(browser, origin, page, viewport, scheme) {
  const context = await browser.createBrowserContext(); // fresh storage: a first visit each time
  const problems = [];
  try {
    await inspectView(await context.newPage(), origin, page, viewport, scheme, problems);
  } catch (err) {
    problems.push(`could not check: ${String(err.message ?? err).split("\n")[0]}`);
  } finally {
    await context.close();
  }
  return problems;
}

async function inspectView(tab, origin, page, viewport, scheme, problems) {
  tab.on("pageerror", (err) => problems.push(`page error: ${err.message.split("\n")[0]}`));
  await tab.setViewport(viewport);
  await tab.emulateMediaFeatures([{ name: "prefers-color-scheme", value: scheme }]);
  const response = await tab.goto(`${origin}/${page.path}`, { waitUntil: "networkidle0" });
  if (!response || !response.ok()) {
    problems.push(`HTTP ${response ? response.status() : "no response"}`);
  } else {
    const dark = await tab.evaluate(() => document.body.classList.contains("quarto-dark"));
    if (dark !== (scheme === "dark")) problems.push(`asked for ${scheme}, got the other scheme`);
    const diagrams = await tab.$$eval("pre.mermaid", (els) => els.length);
    if (diagrams) {
      try {
        await tab.waitForFunction(
          () => [...document.querySelectorAll("pre.mermaid")].every((p) => p.querySelector("svg")),
          { timeout: DIAGRAM_TIMEOUT_MS },
        );
      } catch {
        const drawn = await tab.$$eval("pre.mermaid svg", (els) => els.length);
        problems.push(`${diagrams - drawn} of ${diagrams} diagram(s) not drawn`);
      }
    }
    const wider = await tab.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    if (wider > 1) problems.push(`the page scrolls sideways: ${wider}px wider than the window`);
    for (const offender of await tab.evaluate(overflowingElements)) {
      problems.push(`wider than the viewport: ${offender}`);
    }
    for (const cut of await tab.evaluate(clippedText)) problems.push(`text cut off: ${cut}`);
    const shot = `${page.path.replace(/[/.]/g, "_")}--${viewport.name}--${scheme}.png`;
    await tab.screenshot({ path: join(OUT, shot) });
  }
}

// Read stdin as a stream: readFileSync(0) throws EAGAIN when the pipe is not ready yet.
async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf-8");
}

async function main() {
  const input = (await readStdin()).trim();
  if (!input) fail("no pages on stdin; pipe `check_site.py --layout-pages` into this script");
  const pages = JSON.parse(input);
  if (!existsSync(join(SITE, "index.html"))) fail(`${SITE} has no index.html; run make site`);
  const executablePath = CHROME_CANDIDATES.find((p) => p && existsSync(p));
  if (!executablePath) fail("no Chrome or Chromium found; set CHROME_PATH");
  mkdirSync(OUT, { recursive: true });

  const server = await serve(SITE);
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await puppeteer.launch({ executablePath, headless: true });
  let problems = 0;
  try {
    for (const page of pages) {
      for (const viewport of VIEWPORTS) {
        for (const scheme of SCHEMES) {
          const found = await checkView(browser, origin, page, viewport, scheme);
          const label = `${page.path} [${viewport.name}, ${scheme}]`;
          console.log(found.length ? `FAIL ${label}` : `ok   ${label}`);
          for (const p of found) console.log(`       ${p}`);
          problems += found.length;
        }
      }
    }
  } finally {
    await browser.close();
    server.close();
  }
  const views = pages.length * VIEWPORTS.length * SCHEMES.length;
  console.log(`site_layout: ${problems} problem(s) across ${views} view(s); screenshots in ${OUT}`);
  process.exit(problems ? 1 : 0);
}

main().catch((err) => fail(err.stack ?? String(err)));
