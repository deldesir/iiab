#!/usr/bin/env node
/*
 * organized-sources-fetch.js  (IIAB roles/organized)
 *
 * OPTIONAL online step for the source-materials mirror. When the box has
 * internet, this refreshes the local publication cache by downloading the
 * current/upcoming Meeting Workbook (mwb) and Watchtower study (w) EPUBs from
 * jw.org's PUBLIC pub-media API into:
 *     $INPUT_DIR/<JWLANG>/
 * organized-sources-generate.js then parses them into the offline API.
 *
 * It is best-effort: missing issues/languages (HTTP 404) and any network
 * failure are skipped, so an offline box simply keeps serving its last cache.
 * Nothing here is required at run time once the EPUBs are cached.
 *
 * Config (environment variables, all optional):
 *   INPUT_DIR                   default /library/www/html/organized-sources/input
 *   ORGANIZED_SOURCE_LANGUAGES  comma/space list of JW lang codes, default "E"
 *   MWB_LOOKBACK / MWB_LOOKAHEAD   bimonths back/forward to try   (default 1 / 2)
 *   W_LOOKBACK   / W_LOOKAHEAD     months  back/forward to try     (default 3 / 1)
 *   FETCH_TIMEOUT_MS            per-request timeout, default 30000
 *
 * Requires Node >= 18 (uses the built-in global fetch). No external deps.
 */

'use strict';

const fs = require('fs');
const path = require('path');

const API = 'https://app.jw-cdn.org/apis/pub-media/GETPUBMEDIALINKS';
const INPUT_DIR =
  process.env.INPUT_DIR || '/library/www/html/organized-sources/input';
const LANGS = (process.env.ORGANIZED_SOURCE_LANGUAGES || 'E')
  .split(/[,\s]+/)
  .map((s) => s.trim())
  .filter(Boolean);
const MWB_BACK = parseInt(process.env.MWB_LOOKBACK || '1', 10); // bimonths
const MWB_FWD = parseInt(process.env.MWB_LOOKAHEAD || '2', 10);
const W_BACK = parseInt(process.env.W_LOOKBACK || '3', 10); // months
const W_FWD = parseInt(process.env.W_LOOKAHEAD || '1', 10);
const TIMEOUT = parseInt(process.env.FETCH_TIMEOUT_MS || '30000', 10);

function log(msg) {
  console.log(`[organized-sources-fetch] ${msg}`);
}

const ym = (year, month) => year * 100 + month; // 1-12 -> YYYYMM
function shift(year, month, deltaMonths) {
  const idx = year * 12 + (month - 1) + deltaMonths;
  return { year: Math.floor(idx / 12), month: (idx % 12) + 1 };
}

function mwbIssues(now) {
  const y = now.getFullYear();
  const m = now.getMonth() + 1;
  const start = m % 2 === 1 ? m : m - 1; // odd month = bimonth start
  const set = new Set();
  for (let k = -MWB_BACK; k <= MWB_FWD; k++) {
    const d = shift(y, start, k * 2);
    set.add(ym(d.year, d.month));
  }
  return [...set];
}

function wIssues(now) {
  const y = now.getFullYear();
  const m = now.getMonth() + 1;
  const set = new Set();
  for (let k = -W_BACK; k <= W_FWD; k++) {
    const d = shift(y, m, k);
    set.add(ym(d.year, d.month));
  }
  return [...set];
}

async function getJson(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

// Success responses are objects with .files; 404 responses are arrays.
function epubEntry(data, lang) {
  if (!data || Array.isArray(data) || !data.files) return null;
  const byLang = data.files[lang];
  if (!byLang || !Array.isArray(byLang.EPUB) || byLang.EPUB.length === 0) {
    return null;
  }
  return byLang.EPUB[0];
}

async function download(url, dest, expectedSize) {
  if (
    fs.existsSync(dest) &&
    expectedSize &&
    fs.statSync(dest).size === expectedSize
  ) {
    return 'cached';
  }
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT * 4);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) return `http ${res.status}`;
    const buf = Buffer.from(await res.arrayBuffer());
    const tmp = `${dest}.tmp`;
    fs.writeFileSync(tmp, buf);
    fs.renameSync(tmp, dest); // atomic
    return `downloaded (${buf.length} bytes)`;
  } catch (e) {
    return `error: ${e.message}`;
  } finally {
    clearTimeout(t);
  }
}

async function main() {
  if (typeof fetch !== 'function') {
    log('global fetch unavailable (need Node >= 18); skipping online refresh');
    return;
  }
  const now = new Date();
  const plan = [
    ...mwbIssues(now).map((issue) => ({ pub: 'mwb', issue })),
    ...wIssues(now).map((issue) => ({ pub: 'w', issue })),
  ];
  log(`languages: ${LANGS.join(', ')}; ${plan.length} issue(s) per language`);

  for (const lang of LANGS) {
    const langDir = path.join(INPUT_DIR, lang);
    fs.mkdirSync(langDir, { recursive: true });
    for (const { pub, issue } of plan) {
      const url =
        `${API}?langwritten=${encodeURIComponent(lang)}` +
        `&pub=${pub}&issue=${issue}&fileformat=EPUB`;
      const entry = epubEntry(await getJson(url), lang);
      if (!entry || !entry.file || !entry.file.url) {
        log(`${lang} ${pub} ${issue}: not available`);
        continue;
      }
      const dest = path.join(
        langDir,
        path.basename(new URL(entry.file.url).pathname)
      );
      const status = await download(entry.file.url, dest, entry.filesize);
      log(`${lang} ${pub} ${issue}: ${status} -> ${path.basename(dest)}`);
    }
  }
  log('done');
}

main().catch((e) => {
  console.error(
    `[organized-sources-fetch] fatal: ${e && e.stack ? e.stack : e}`
  );
  process.exit(1);
});
