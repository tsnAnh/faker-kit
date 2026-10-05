import { describe, expect, test } from "bun:test";

import {
  defaultModelForType,
  isKnownModel,
  MODEL_CATALOG,
  renderModelCatalog,
  resolveModelSelection,
} from "../models.ts";

describe("MODEL_CATALOG", () => {
  test("every catalog entry passes isKnownModel", () => {
    for (const profile of MODEL_CATALOG)
      expect(isKnownModel(profile.slug)).toBe(true);
  });

  test("slugs are unique", () => {
    const slugs = MODEL_CATALOG.map(m => m.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
  });

  test("every backend has a default for every TaskType", () => {
    for (const backend of ["claude", "codex"] as const) {
      for (const type of ["worker", "subplanner", "verifier"] as const) {
        const slug = defaultModelForType(type, backend);
        expect(MODEL_CATALOG.find(m => m.slug === slug)?.backend).toBe(backend);
      }
    }
  });

  test("Claude planners and verifiers default to opus, workers to sonnet", () => {
    expect(defaultModelForType("subplanner", "claude")).toBe("opus");
    expect(defaultModelForType("verifier", "claude")).toBe("opus");
    expect(defaultModelForType("worker", "claude")).toBe("sonnet");
    expect(defaultModelForType("worker")).toBe("sonnet");
  });

  test("Codex planners and verifiers default to high effort", () => {
    expect(
      resolveModelSelection(defaultModelForType("verifier", "codex"))
    ).toEqual({
      id: "gpt-5.5",
      params: [{ id: "effort", value: "high" }],
    });
  });

  test("opus-max binds the opus alias at max effort", () => {
    expect(resolveModelSelection("opus-max")).toEqual({
      id: "opus",
      params: [{ id: "effort", value: "max" }],
    });
  });

  test("unknown slugs pass through as a bare id", () => {
    expect(resolveModelSelection("claude-some-future-model")).toEqual({
      id: "claude-some-future-model",
    });
  });

  test("the rendered catalog names each entry's backend", () => {
    const text = renderModelCatalog();
    expect(text).toContain("`sonnet` [claude]");
    expect(text).toContain("`gpt-5.5` [codex]");
  });
});
