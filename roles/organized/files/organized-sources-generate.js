#!/usr/bin/env node
/*
 * organized-sources-generate.js  (IIAB roles/organized)
 *
 * Builds the LOCAL "source materials" mirror that organized-app's auto-import
 * consumes (VITE_SOURCE_MATERIALS_API=/organized-sources). Fully offline.
 *
 * It parses publication files (.epub / .jwpub) that an admin has dropped under
 *     $INPUT_DIR/<JWLANG>/        e.g.  .../input/E/mwb_E_202601.epub
 * using meeting-schedules-parser — the same parser organized-app uses for its
 * in-browser EPUB import — and writes the exact array the frontend expects:
 *     $OUTPUT_DIR/<JWLANG>        served as  GET /organized-sources/api/<JWLANG>
 *
 * The folder name IS the JW language code the app requests (E, F, HT, ...).
 * No language list to configure: every folder found is processed. No network
 * access is required at run time.
 *
 * Config (environment variables, all optional):
 *   INPUT_DIR      default /library/www/html/organized-sources/input
 *   OUTPUT_DIR     default /library/www/html/organized-sources/api
 *   PARSER_MODULE  path to meeting-schedules-parser's Node entry point
 */

'use strict';

const fs = require('fs');
const path = require('path');

const INPUT_DIR =
  process.env.INPUT_DIR || '/library/www/html/organized-sources/input';
const OUTPUT_DIR =
  process.env.OUTPUT_DIR || '/library/www/html/organized-sources/api';
const PARSER_MODULE =
  process.env.PARSER_MODULE ||
  '/opt/iiab/organized-app/node_modules/meeting-schedules-parser/dist/node/index.cjs';

const PUB_EXT = new Set(['.epub', '.jwpub']);

function log(msg) {
  console.log(`[organized-sources] ${msg}`);
}
function warn(msg) {
  console.warn(`[organized-sources] ${msg}`);
}
function err(msg) {
  console.error(`[organized-sources] ${msg}`);
}

async function main() {
  let loadPub;
  try {
    ({ loadPub } = require(PARSER_MODULE));
  } catch (e) {
    err(`cannot load parser at ${PARSER_MODULE}: ${e.message}`);
    err('is organized-app built (npm ci) so node_modules is present?');
    process.exit(1);
  }

  if (!fs.existsSync(INPUT_DIR)) {
    warn(`input dir ${INPUT_DIR} does not exist; nothing to do`);
    return;
  }
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const langs = fs
    .readdirSync(INPUT_DIR, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name);

  if (langs.length === 0) {
    warn(
      `no language folders under ${INPUT_DIR}; drop publication files into ` +
        `${INPUT_DIR}/<JWLANG>/ (e.g. ${INPUT_DIR}/E/, ${INPUT_DIR}/HT/)`
    );
    return;
  }

  let totalWeeks = 0;
  for (const lang of langs) {
    const langDir = path.join(INPUT_DIR, lang);
    const files = fs
      .readdirSync(langDir)
      .filter((f) => PUB_EXT.has(path.extname(f).toLowerCase()))
      .map((f) => path.join(langDir, f))
      .sort();

    if (files.length === 0) {
      warn(`${lang}: no .epub/.jwpub files, skipping`);
      continue;
    }

    const weeks = [];
    for (const file of files) {
      try {
        const parsed = await loadPub(file);
        const n = Array.isArray(parsed) ? parsed.length : 0;
        if (Array.isArray(parsed)) weeks.push(...parsed);
        log(`${lang}: parsed ${path.basename(file)} (${n} weeks)`);
      } catch (e) {
        err(`${lang}: failed to parse ${path.basename(file)}: ${e.message}`);
      }
    }

    // Atomic publish so nginx never serves a half-written file.
    const outFile = path.join(OUTPUT_DIR, lang);
    const tmpFile = `${outFile}.tmp`;
    fs.writeFileSync(tmpFile, JSON.stringify(weeks));
    fs.renameSync(tmpFile, outFile);
    totalWeeks += weeks.length;
    log(`wrote ${outFile} (${weeks.length} weeks)`);
  }

  log(`done: ${langs.length} language(s), ${totalWeeks} week(s) total`);
}

main().catch((e) => {
  err(`fatal: ${e && e.stack ? e.stack : e}`);
  process.exit(1);
});
