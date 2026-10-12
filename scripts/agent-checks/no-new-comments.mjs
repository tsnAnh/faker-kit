#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { globToRegExp } from "./glob.mjs";

const USAGE = `Usage: node scripts/agent-checks/no-new-comments.mjs [--base <ref>] [--config .agents/no-new-comments.json]

Fail when a change adds a comment. Agents copy comments that justify workarounds, so
this repository explains code with names, types, and structure instead.

Allowed: license headers, public API doc comments (///, /**, //!), lint suppressions,
lines linking an issue or URL, shebangs and pragmas, and anything matching the
config's "allow" patterns. Paths matching "exclude" globs are skipped.

Checks source code only. YAML, TOML, and Markdown are skipped: comments are how those
files document themselves. Each changed file is read whole, so comment markers inside
strings, template literals, regular expressions, and heredocs are not comments.

  --base     Compare against this ref (default: merge-base with origin/main or main).

Examples:
  node scripts/agent-checks/no-new-comments.mjs
  node scripts/agent-checks/no-new-comments.mjs --base origin/develop
`;

const SLASH = ["ts", "tsx", "js", "jsx", "mjs", "cjs", "kt", "kts", "swift", "rs", "dart", "java", "go", "c", "cc", "cpp", "h", "hpp", "m", "mm", "scss", "gradle"];
const HASH = ["py", "sh", "bash", "zsh", "rb", "tf", "cmake"];
const DASH = ["sql", "lua"];
const MARKUP = ["xml", "html", "svg"];
const JS = new Set(["ts", "tsx", "js", "jsx", "mjs", "cjs"]);
const BACKTICK_STRINGS = new Set([...JS, "go"]);
const TRIPLE_QUOTED = new Set(["kt", "kts", "swift", "dart", "java", "py"]);
const SHELL_WORD_START = /[\s;|&()]/;
const REGEX_AFTER_WORD = new Set(["return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "yield", "await"]);
const ALWAYS_ALLOWED = [
  /copyright|spdx-license-identifier|licensed under/i,
  /https?:\/\//,
  /(^|\s)#\d+\b|\b[A-Z][A-Z0-9]+-\d+\b/,
  /eslint-disable|eslint-enable|@ts-(ignore|expect-error|nocheck|check)|prettier-ignore|biome-ignore|noqa|type:\s*ignore|pyright:|swiftlint:|swift-format-ignore|ktlint-disable|detekt|ignore(_for_file)?:|NOLINT|istanbul ignore|c8 ignore/,
  /^\s*#!/,
  /^\s*#\s*-\*-/,
  /^\s*\/\/\s*(swift-tools-version|go:|\+build|@flow|@jsx|#region|#endregion)/,
];

function git(repo, args) {
  return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8", maxBuffer: 256 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] });
}

function defaultBase(repo) {
  for (const ref of ["origin/main", "main", "origin/master", "master"]) {
    try {
      return git(repo, ["merge-base", "HEAD", ref]).trim();
    } catch {}
  }
  return "HEAD";
}

function closingQuote(line, from, quote) {
  for (let i = from; i < line.length; i++) {
    if (line[i] === "\\") i++;
    else if (line.startsWith(quote, i)) return i;
  }
  return -1;
}

function regexEnd(line, from) {
  let inClass = false;
  for (let i = from; i < line.length; i++) {
    const ch = line[i];
    if (ch === "\\") i++;
    else if (ch === "[") inClass = true;
    else if (ch === "]") inClass = false;
    else if (ch === "/" && !inClass) return i;
  }
  return -1;
}

function lexSlash(lines, ext) {
  const found = new Map();
  let open = null;
  let prev = "";
  let word = "";
  lines.forEach((line, index) => {
    const n = index + 1;
    let i = 0;
    if (open?.block) {
      found.set(n, { text: line.trim(), doc: open.doc });
      const end = line.indexOf("*/");
      if (end === -1) return;
      i = end + 2;
      open = null;
    } else if (open) {
      const end = closingQuote(line, 0, open.quote);
      if (end === -1) return;
      i = end + open.quote.length;
      open = null;
      prev = '"';
    }
    while (i < line.length) {
      const rest = line.slice(i);
      const ch = line[i];
      if (rest.startsWith("//")) {
        found.set(n, { text: rest.trim(), doc: /^\/\/[/!](?!\/)/.test(rest) });
        break;
      }
      if (rest.startsWith("/*")) {
        const doc = /^\/\*[*!](?!\/)/.test(rest);
        found.set(n, { text: rest.trim(), doc });
        const end = line.indexOf("*/", i + 2);
        if (end === -1) {
          open = { block: true, doc };
          break;
        }
        i = end + 2;
        continue;
      }
      const triple = TRIPLE_QUOTED.has(ext) && (rest.startsWith('"""') || (ext === "dart" && rest.startsWith("'''"))) ? rest.slice(0, 3) : null;
      if (triple) {
        const end = closingQuote(line, i + 3, triple);
        if (end === -1) {
          open = { quote: triple };
          break;
        }
        i = end + 3;
        prev = '"';
        continue;
      }
      if (ch === '"' || (ch === "`" && BACKTICK_STRINGS.has(ext))) {
        const end = closingQuote(line, i + 1, ch);
        if (end === -1) {
          if (ch === "`" || ext === "rs") open = { quote: ch };
          break;
        }
        i = end + 1;
        prev = '"';
        continue;
      }
      if (ch === "'") {
        const literal = ext === "rs" ? /^'(?:\\u\{[0-9a-fA-F]+\}|\\.|[^\\'])'/.exec(rest)?.[0] : null;
        const end = ext === "rs" ? (literal ? i + literal.length - 1 : -1) : closingQuote(line, i + 1, "'");
        if (end !== -1) {
          i = end + 1;
          prev = '"';
          continue;
        }
      }
      if (ch === "/" && JS.has(ext) && (prev === "" || "(,=:[!&|?{};+-*%<>~^".includes(prev) || REGEX_AFTER_WORD.has(word))) {
        const end = regexEnd(line, i + 1);
        if (end !== -1) {
          i = end + 1;
          prev = "/";
          word = "";
          continue;
        }
      }
      if (/[A-Za-z0-9_$]/.test(ch)) word = i > 0 && /[A-Za-z0-9_$]/.test(line[i - 1]) ? word + ch : ch;
      else if (!/\s/.test(ch)) word = "";
      if (!/\s/.test(ch)) prev = ch;
      i++;
    }
  });
  return found;
}

function lexHash(lines, ext) {
  const found = new Map();
  const shell = ["sh", "bash", "zsh"].includes(ext);
  const heredocs = shell || ext === "rb";
  let open = null;
  lines.forEach((line, index) => {
    const n = index + 1;
    if (open?.heredoc) {
      if (line.trim() === open.heredoc) open = null;
      return;
    }
    let i = 0;
    if (open) {
      const end = closingQuote(line, 0, open.quote);
      if (end === -1) return;
      i = end + open.quote.length;
      open = null;
    }
    let pendingHeredoc = null;
    while (i < line.length) {
      const rest = line.slice(i);
      const ch = line[i];
      const triple = ext === "py" && (rest.startsWith('"""') || rest.startsWith("'''")) ? rest.slice(0, 3) : null;
      if (triple) {
        const end = closingQuote(line, i + 3, triple);
        if (end === -1) {
          open = { quote: triple };
          break;
        }
        i = end + 3;
        continue;
      }
      if (ch === '"' || ch === "'") {
        const end = ch === "'" && shell ? line.indexOf("'", i + 1) : closingQuote(line, i + 1, ch);
        if (end === -1) {
          if (shell || ext === "rb") open = { quote: ch };
          break;
        }
        i = end + 1;
        continue;
      }
      if (heredocs && rest.startsWith("<<") && rest[2] !== "<") {
        const match = /^<<[-~]?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1/.exec(rest);
        if (match) {
          pendingHeredoc = match[2];
          i += match[0].length;
          continue;
        }
      }
      if (ch === "#" && (!shell || i === 0 || SHELL_WORD_START.test(line[i - 1]))) {
        found.set(n, { text: rest.trim(), doc: false });
        break;
      }
      i++;
    }
    if (pendingHeredoc && !open) open = { heredoc: pendingHeredoc };
  });
  return found;
}

function lexSimple(lines, start, end) {
  const found = new Map();
  let inBlock = false;
  lines.forEach((line, index) => {
    if (inBlock) {
      found.set(index + 1, { text: line.trim(), doc: false });
      if (end && line.includes(end)) inBlock = false;
      return;
    }
    const code = line.replace(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/g, (s) => " ".repeat(s.length));
    const at = code.indexOf(start);
    if (at === -1) return;
    found.set(index + 1, { text: line.slice(at).trim(), doc: false });
    if (end && !line.includes(end, at + start.length)) inBlock = true;
  });
  return found;
}

export function commentLines(text, ext) {
  const lines = text.split("\n");
  if (SLASH.includes(ext)) return lexSlash(lines, ext);
  if (HASH.includes(ext)) return lexHash(lines, ext);
  if (DASH.includes(ext)) return lexSimple(lines, "--", null);
  if (MARKUP.includes(ext)) return lexSimple(lines, "<!--", "-->");
  return new Map();
}

export function commentIn(line, ext) {
  const found = commentLines(line, ext).get(1);
  if (found) return found.text;
  if (SLASH.includes(ext) && /^\s*\*(?!\*)/.test(line)) return line.trim();
  return null;
}

export function findNewComments(repo, base, config = {}) {
  const allow = [...ALWAYS_ALLOWED, ...(config.allow ?? []).map((p) => new RegExp(p))];
  const exclude = (config.exclude ?? []).map(globToRegExp);
  const known = new Set([...SLASH, ...HASH, ...DASH, ...MARKUP]);
  const diff = git(repo, ["diff", "--unified=0", "--no-color", "--no-ext-diff", base, "--"]);
  const untracked = git(repo, ["ls-files", "--others", "--exclude-standard"]).split("\n").filter(Boolean);
  const addedLines = new Map();
  let file = null;
  let lineNo = 0;
  for (const line of diff.split("\n")) {
    if (line.startsWith("+++ ")) file = line.startsWith("+++ b/") ? line.slice(6) : null;
    else if (line.startsWith("@@")) lineNo = Number(/\+(\d+)/.exec(line)[1]);
    else if (line.startsWith("+") && file) {
      if (!addedLines.has(file)) addedLines.set(file, new Set());
      addedLines.get(file).add(lineNo++);
    }
  }
  for (const f of untracked) addedLines.set(f, null);
  const found = [];
  for (const [f, lines] of addedLines) {
    const ext = path.extname(f).slice(1).toLowerCase();
    const full = path.join(repo, f);
    if (!known.has(ext) || exclude.some((re) => re.test(f)) || !fs.existsSync(full)) continue;
    const comments = commentLines(fs.readFileSync(full, "utf8"), ext);
    for (const [line, { text, doc }] of [...comments].sort(([a], [b]) => a - b)) {
      if (lines && !lines.has(line)) continue;
      if (doc || text.length < 3) continue;
      if (allow.some((re) => re.test(text))) continue;
      found.push({ file: f, line, comment: text });
    }
  }
  return found;
}

function main() {
  const args = process.argv.slice(2);
  if (args.includes("-h") || args.includes("--help")) {
    process.stdout.write(USAGE);
    return;
  }
  const option = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
  const repo = git(process.cwd(), ["rev-parse", "--show-toplevel"]).trim();
  const configFile = path.join(repo, option("--config", ".agents/no-new-comments.json"));
  const config = fs.existsSync(configFile) ? JSON.parse(fs.readFileSync(configFile, "utf8")) : {};
  const found = findNewComments(repo, option("--base", defaultBase(repo)), config);
  if (found.length === 0) {
    process.stdout.write("no-new-comments: no new comments\n");
    return;
  }
  for (const f of found) process.stderr.write(`${f.file}:${f.line}: ${f.comment}\n`);
  process.stderr.write(
    `no-new-comments: ${found.length} new comment(s). Say it with names, types, or structure instead, then delete the comment. ` +
      "Keep only license headers, public API docs, lint suppressions, and links to an external constraint. Run /no-comments for help.\n",
  );
  process.exit(config.mode === "warn" ? 0 : 1);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) main();
