#!/usr/bin/env node
// Browser smoke test for the AURA web app: loads it in headless Chromium at a desktop and a phone
// width and fails on anything that would break it for a visitor:
//   - the splash never goes away (Flutter did not paint),
//   - a Content-Security-Policy violation, a failed request, a >=400 response, or a console error,
//   - an invite link that leaves its single-use token in the address bar.
//
//   node scripts/web-app-smoke.js <base-url> [screenshot-dir]
//   node scripts/web-app-smoke.js https://web.aureon-ai.tech
//   node scripts/web-app-smoke.js http://127.0.0.1:8080          (docker run of aura_web)
//
// Needs the `playwright` package and a Chromium (preinstalled in the cloud session image;
// elsewhere `npm i playwright && npx playwright install chromium`). It cannot prove a real
// sign-in: that needs the owner's Firebase project and a real account (docs/deployment/web-app.md).
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const base = (process.argv[2] || '').replace(/\/+$/, '');
const shots = process.argv[3];
if (!base) {
  console.error('usage: node scripts/web-app-smoke.js <base-url> [screenshot-dir]');
  process.exit(2);
}
if (shots) fs.mkdirSync(shots, { recursive: true });

async function visit(browser, name, viewport, url, wait) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const problems = [];
  const own = new URL(base).origin;
  page.on('console', (m) => {
    const t = m.text();
    if (/Content Security Policy|Refused to/i.test(t)) problems.push(`CSP: ${t.slice(0, 200)}`);
    else if (m.type() === 'error') problems.push(`console error: ${t.slice(0, 200)}`);
  });
  page.on('pageerror', (e) => problems.push(`page error: ${String(e).slice(0, 200)}`));
  page.on('requestfailed', (r) => problems.push(`request failed: ${r.url().slice(0, 120)}`));
  page.on('response', (r) => {
    if (r.status() >= 400) problems.push(`HTTP ${r.status()}: ${r.url().slice(0, 120)}`);
  });
  await page.goto(url, { waitUntil: 'load' });
  try {
    await page.waitForFunction(() => !document.getElementById('aura-splash'), null, { timeout: 60000 });
  } catch (e) {
    problems.push('the loading splash never went away: the app did not paint');
  }
  await page.waitForTimeout(wait || 3000);
  const painted = await page.evaluate(() => !!document.querySelector('flutter-view, flt-glass-pane'));
  if (!painted) problems.push('no Flutter view in the page');
  if (shots) await page.screenshot({ path: path.join(shots, `${name}.png`) });
  const finalUrl = page.url();
  await ctx.close();
  return { name, finalUrl, problems, own };
}

(async () => {
  const browser = await chromium.launch();
  const results = [];
  results.push(await visit(browser, 'desktop-1280', { width: 1280, height: 800 }, `${base}/app/`));
  results.push(await visit(browser, 'phone-390', { width: 390, height: 800 }, `${base}/app/`));
  const invite = await visit(browser, 'invite-link', { width: 1280, height: 800 }, `${base}/app/invite?token=SMOKE-TOKEN`);
  if (invite.finalUrl.includes('SMOKE-TOKEN')) invite.problems.push('the single-use invite token is still in the address bar');
  results.push(invite);
  await browser.close();

  let failed = false;
  for (const r of results) {
    console.log(`${r.problems.length ? 'FAIL' : 'ok  '} ${r.name} -> ${r.finalUrl}`);
    for (const p of r.problems) console.log(`       ${p}`);
    if (r.problems.length) failed = true;
  }
  process.exit(failed ? 1 : 0);
})();
