#!/usr/bin/env node
// Rewrites root-absolute URLs in a `mint export` build so it works under a path prefix.
//
// GitHub Pages serves a project site at /<repo>/, but the export assumes it is served at /.
// This prefixes asset URLs (/_next/...) in every HTML, JS, and CSS file, and page and file
// links (href="/...", src="/...", and hrefs inside the page data) in every HTML file.
// With an empty base (a custom domain), it changes nothing.
//
//   node scripts/pages-base-path.mjs <export dir> <base, e.g. /team-kit>

import fs from "node:fs";
import path from "node:path";

const [dir, rawBase = ""] = process.argv.slice(2);
if (!dir) {
  console.error("usage: pages-base-path.mjs <export dir> <base>");
  process.exit(2);
}
const base = rawBase.replace(/\/+$/, "");
if (!base) {
  console.log("base path is empty; nothing to rewrite");
  process.exit(0);
}
if (!/^\/[A-Za-z0-9._-]+(\/[A-Za-z0-9._-]+)*$/.test(base)) {
  console.error(`base must look like /name, got ${JSON.stringify(rawBase)}`);
  process.exit(2);
}

const notBase = `(?!${base.slice(1).replace(/[.]/g, "\\.")}/)`;
const assetRe = /(["'`(]|\\")\/_next\//g;
const attrRe = new RegExp(`\\b(href|src|action|poster)=(["'])/(?!/)${notBase}`, "g");
const srcsetRe = /\bsrcset=(["'])([^"']*)\1/g;
const dataHrefRe = new RegExp(`(\\\\"href\\\\":\\\\")/(?!/)${notBase}`, "g");

let files = 0;
(function walk(d) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const full = path.join(d, e.name);
    if (e.isDirectory()) walk(full);
    else if (/\.(html|js|css)$/.test(e.name)) rewrite(full);
  }
})(dir);

function rewrite(file) {
  const before = fs.readFileSync(file, "utf8");
  let text = before.replace(assetRe, `$1${base}/_next/`);
  if (file.endsWith(".html")) {
    text = text
      .replace(attrRe, `$1=$2${base}/`)
      .replace(srcsetRe, (_, q, list) => `srcset=${q}${list.replace(new RegExp(`(^|,\\s*)/(?!/)${notBase}`, "g"), `$1${base}/`)}${q}`)
      .replace(dataHrefRe, `$1${base}/`);
  }
  if (text !== before) {
    fs.writeFileSync(file, text);
    files++;
  }
}
console.log(`rewrote ${files} files for base ${base}`);
