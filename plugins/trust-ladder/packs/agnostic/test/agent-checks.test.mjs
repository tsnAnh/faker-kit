import { describe, expect, test } from "bun:test";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { checkBoundaries } from "../files/scripts/agent-checks/import-boundaries.mjs";
import { checkSet } from "../files/scripts/agent-checks/locale-keys.mjs";
import { commentIn, findNewComments } from "../files/scripts/agent-checks/no-new-comments.mjs";
import { runRatchet } from "../files/scripts/agent-checks/ratchet.mjs";

const CHECKS = path.resolve(import.meta.dir, "../files/scripts/agent-checks");

function repo(files) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "agent-checks-"));
  const git = (...args) => execFileSync("git", ["-C", dir, ...args]);
  git("init", "-q", "-b", "main");
  git("config", "user.email", "t@example.com");
  git("config", "user.name", "t");
  write(dir, files);
  git("add", "-A");
  git("commit", "-q", "-m", "base");
  return { dir, git, write: (more) => write(dir, more), commit: () => (git("add", "-A"), git("commit", "-q", "-m", "change")) };
}

function write(dir, files) {
  for (const [file, content] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true });
    fs.writeFileSync(path.join(dir, file), content);
  }
}

describe("ratchet", () => {
  const config = { rules: [{ id: "rust-unwrap", pattern: "\\.unwrap\\(\\)", files: ["*.rs"], message: "Return a Result instead." }] };

  test("allows the baseline, fails on growth, and only ratchets down", () => {
    const r = repo({ ".agents/ratchet.json": JSON.stringify(config), "src/a.rs": "fn a() { x.unwrap(); y.unwrap(); }\n" });
    expect(runRatchet(r.dir, "update", ".agents/ratchet.json", ".agents/ratchet-baseline.json").failed).toBe(false);
    r.commit();
    expect(runRatchet(r.dir, "check", ".agents/ratchet.json", ".agents/ratchet-baseline.json").failed).toBe(false);

    r.write({ "src/b.rs": "fn b() { z.unwrap(); }\n" });
    r.commit();
    const grown = runRatchet(r.dir, "check", ".agents/ratchet.json", ".agents/ratchet-baseline.json");
    expect(grown.failed).toBe(true);
    expect(grown.lines.join("\n")).toContain("rust-unwrap: 3 found, baseline allows 2. Return a Result instead.");
    expect(grown.lines.join("\n")).toContain("src/b.rs (0 -> 1)");

    const kept = runRatchet(r.dir, "update", ".agents/ratchet.json", ".agents/ratchet-baseline.json");
    expect(kept.lines[0]).toContain("kept baseline 2");
  });
});

describe("no-new-comments", () => {
  test("finds comments by language and ignores comment markers inside strings", () => {
    expect(commentIn('val url = "http://x" // workaround', "kt")).toBe("// workaround");
    expect(commentIn('let s = "// not a comment"', "swift")).toBeNull();
    expect(commentIn("x = 1  # for now", "py")).toBe("# for now");
    expect(commentIn("  * continued block", "ts")).toBe("* continued block");
  });

  test("flags new explanatory comments and keeps docs, suppressions, links, and licenses", () => {
    const r = repo({ "src/app.ts": "export const a = 1\n" });
    r.write({
      "src/app.ts": [
        "// Copyright 2026 Example Inc.",
        "export const a = 1",
        "// HACK: retry twice because the API is flaky",
        "/** Formats a price for display. */",
        "export function price() {}",
        "// eslint-disable-next-line no-console",
        "// See https://github.com/example/issue/12",
        "const b = 2 // temporary",
        "",
      ].join("\n"),
      "tools/run.py": "def run():\n    return 1  # for now\n",
    });
    const found = findNewComments(r.dir, "HEAD").map((f) => `${f.file}:${f.line}: ${f.comment}`);
    expect(found).toEqual([
      "src/app.ts:3: // HACK: retry twice because the API is flaky",
      "src/app.ts:8: // temporary",
      "tools/run.py:2: # for now",
    ]);
  });

  test("respects exclude globs and allow patterns", () => {
    const r = repo({ "a.swift": "let a = 1\n" });
    r.write({ "a.swift": "let a = 1\n// MARK: - Layout\n", "Generated/x.swift": "// generated\n" });
    expect(findNewComments(r.dir, "HEAD", { allow: ["^// MARK:"], exclude: ["Generated/**"] })).toEqual([]);
  });

  test("the CLI fails with guidance", () => {
    const r = repo({ "a.rs": "fn a() {}\n" });
    r.write({ "a.rs": "fn a() {} // quick fix\n" });
    const out = spawnSync("node", [path.join(CHECKS, "no-new-comments.mjs"), "--base", "HEAD"], { cwd: r.dir, encoding: "utf8" });
    expect(out.status).toBe(1);
    expect(out.stderr).toContain("a.rs:1: // quick fix");
    expect(out.stderr).toContain("Say it with names, types, or structure instead");
  });
});

describe("import-boundaries", () => {
  const config = {
    rules: [
      { id: "ui-no-data", from: ["lib/features/*/ui/**"], forbid: ["lib/features/*/data/**", "package:http/**"], message: "Go through the controller." },
      { id: "domain-no-framework", from: ["app/domain/**"], forbid: ["app/ui/**", "django**"], message: "Domain stays framework-free." },
      { id: "common-no-android", from: ["shared/src/commonMain/**"], forbid: ["android.**", "java.io.**"], message: "Use expect/actual." },
    ],
  };

  test("catches relative, package, and module imports across languages", () => {
    const r = repo({
      "lib/features/cart/ui/cart_page.dart": "import '../data/cart_api.dart';\nimport 'package:http/http.dart' as http;\nimport '../cart_controller.dart';\n",
      "lib/features/cart/ui/ok_page.dart": "import 'package:flutter/material.dart';\n",
      "app/domain/order.py": "from ..ui import views\nimport django.db\nfrom .money import Money\n",
      "shared/src/commonMain/kotlin/Store.kt": "package shared\n\nimport android.content.Context\nimport kotlinx.coroutines.flow.Flow\n",
    });
    const found = checkBoundaries(r.dir, config).map((v) => `${v.rule} ${v.file}:${v.line} ${v.target}`);
    expect(found).toEqual([
      "ui-no-data lib/features/cart/ui/cart_page.dart:1 ../data/cart_api.dart",
      "ui-no-data lib/features/cart/ui/cart_page.dart:2 package:http/http.dart",
      "domain-no-framework app/domain/order.py:1 ..ui",
      "domain-no-framework app/domain/order.py:2 django.db",
      "common-no-android shared/src/commonMain/kotlin/Store.kt:3 android.content.Context",
    ]);
  });
});

describe("locale-keys", () => {
  test("reports missing, extra, untranslated, and placeholder mismatches in ARB files", () => {
    const r = repo({
      "lib/l10n/app_en.arb": JSON.stringify({ appName: "Kirumi", greet: "Hello {name}", save: "Save", "@greet": { placeholders: { name: {} } } }),
      "lib/l10n/app_vi.arb": JSON.stringify({ appName: "Kirumi", greet: "Xin chào {ten}", extra: "x" }),
      "lib/l10n/app_ja.arb": JSON.stringify({ appName: "Kirumi", greet: "こんにちは {name}", save: "Save" }),
    });
    const { targets, problems } = checkSet(r.dir, { id: "app", format: "arb", source: "lib/l10n/app_en.arb", targets: "lib/l10n/app_*.arb", allowIdentical: ["appName"] });
    expect(targets).toBe(2);
    expect(problems).toEqual([
      'lib/l10n/app_ja.arb: "save" is still the source text "Save"',
      'lib/l10n/app_vi.arb: "greet" has placeholders [{ten}] but the source has [{name}]',
      'lib/l10n/app_vi.arb: missing "save"',
      'lib/l10n/app_vi.arb: extra "extra" not in lib/l10n/app_en.arb',
    ]);
  });

  test("reads Android strings.xml and Apple .strings files", () => {
    const r = repo({
      "res/values/strings.xml": '<resources><string name="title">Cart</string><string name="sku" translatable="false">SKU</string></resources>',
      "res/values-vi/strings.xml": '<resources><string name="title">Giỏ hàng</string></resources>',
      "App/en.lproj/Localizable.strings": '/* title */\n"title" = "Cart";\n"count" = "%d items";\n',
      "App/vi.lproj/Localizable.strings": '"title" = "Giỏ hàng";\n"count" = "%@ món";\n',
    });
    expect(checkSet(r.dir, { id: "android", format: "android", source: "res/values/strings.xml", targets: "res/values-*/strings.xml" }).problems).toEqual([]);
    expect(checkSet(r.dir, { id: "ios", format: "apple-strings", source: "App/en.lproj/Localizable.strings", targets: "App/*.lproj/Localizable.strings" }).problems).toEqual([
      'App/vi.lproj/Localizable.strings: "count" has placeholders [%@] but the source has [%d]',
    ]);
  });
});
