import { afterEach, expect, it, vi } from "vitest";

import * as chunking from "../src/chunking.js";
import type { Extractor } from "../src/index.js";
import { forEachStore, freshGraphs, type StoreSpec } from "./helpers.js";

const fallback = (text: string): number => Math.max(1, Math.floor(Array.from(text).length / 4));
const extractor: Extractor = {
  extract: async () => ({ entities: [{ type: "Person", name: "Ann" }], relations: [] }),
};

function counter(countTokens: (text: string) => number): void {
  vi.spyOn(chunking, "loadTokenizer").mockResolvedValue({ countTokens, chunkText: chunking.chunkText });
}

function suite(spec: StoreSpec): void {
  const open = freshGraphs(spec, "context-budget");
  afterEach(() => vi.restoreAllMocks());

  it.skipIf(spec.name !== "kuzu").each([100, 200, 400])(
    "bounds tokenizer input volume for %i long records",
    async (size) => {
      const g = open();
      await g.insert.facts({
        entities: [
          { type: "Person", name: "Ann" },
          ...Array.from({ length: size }, (_, i) => ({ type: "Person", name: `Leaf${i}` })),
        ],
        relations: Array.from({ length: size }, (_, i) => ({
          from_entity: "person:ann",
          type: "KNOWS",
          to_entity: `person:leaf${i}`,
          properties: { note: "long evidence ".repeat(80) },
        })),
      });
      const calls: number[] = [];
      counter((text) => {
        calls.push(Array.from(text).length);
        return fallback(text);
      });
      const { context, subgraph } = await g.retrieve("Ann", {
        k: 1,
        depth: 1,
        max_nodes: size + 1,
        budget_tokens: 1000000,
      });
      expect(subgraph.relations).toHaveLength(size);
      expect(context.split("\n").filter((line) => line.includes("-[KNOWS]->"))).toHaveLength(size);
      expect(Math.max(...calls)).toBeLessThanOrEqual(
        Math.max(...context.split("\n").map((line) => Array.from(line).length)) + 1,
      );
      expect(calls.reduce((sum, length) => sum + length, 0)).toBeLessThanOrEqual(3 * Array.from(context).length);
    },
  );

  it.each([
    ["A", "B", "C"],
    ["😀", "🚀", "🦉"],
  ])("accounts for fallback rounding with names %j", async (...names) => {
    const g = open();
    await g.insert.facts({ entities: names.map((name, i) => ({ id: `p:${i}`, type: "P", name })) });
    counter(fallback);
    const full = (await g.retrieve("P", { k: 3 })).context;
    expect(Array.from(full)).toHaveLength(41);
    const limited = (await g.retrieve("P", { k: 3, budget_tokens: 9 })).context;
    expect(limited.split("\n").length).toBeLessThan(3);
    expect(fallback(limited)).toBeLessThanOrEqual(9);
    for (let budget = 1; budget < 18; budget++) {
      const { context } = await g.retrieve("P", { k: 3, budget_tokens: budget });
      expect(context).not.toBe("");
      expect(fallback(context)).toBeLessThanOrEqual(budget);
      expect(full.startsWith(context)).toBe(true);
    }
  });

  it("does not charge rejected source passages or admit records after global overflow", async () => {
    const g = open({ extractor });
    await g.insert.text("Ann\n\n[][]", { source_id: "layout" });
    counter(fallback);
    expect((await g.retrieve("Ann", { budget_tokens: 12 })).context).toBe("GRAPH | Ann [Person]");
    await g.insert.text("Ann ".repeat(100), { source_id: "too-long" });
    expect((await g.retrieve("Ann", { budget_tokens: 5 })).context).toBe("GRAPH | Ann [Person]");
    const h = open();
    await h.insert.facts({
      entities: [
        { type: "P", name: "A" },
        { type: "P", name: "B" },
      ],
      relations: [{ from_entity: "p:a", type: "KNOWS", to_entity: "p:b", properties: { note: "long ".repeat(100) } }],
    });
    const context = (await h.retrieve("P", { k: 2, budget_tokens: 7 })).context;
    expect(context).not.toContain("GRAPH | A [P]");
    expect(context).not.toContain("GRAPH | B [P]");
    expect(fallback(context)).toBeLessThanOrEqual(7);
  });

  it("does not pay fallback rounding for each real-tokenizer record", async ({ skip }) => {
    const { countTokens } = await chunking.loadTokenizer();
    if (countTokens("") !== 0) skip();
    const g = open();
    await g.insert.facts({
      entities: [
        { type: "Person", name: "A" },
        { type: "Person", name: "B" },
      ],
    });
    const expected = ["GRAPH | A [Person]", "GRAPH | B [Person]"];
    expect(expected.map(countTokens)).toEqual([6, 6]);
    expect(countTokens(expected.join("\n"))).toBe(12);
    const { context } = await g.retrieve("Person", { k: 2, budget_tokens: 12 });
    expect(context.split("\n").sort()).toEqual(expected);
    expect(countTokens(context)).toBe(12);
  });

  it("does not charge a final newline on a real-tokenizer relation pair", async ({ skip }) => {
    const { countTokens } = await chunking.loadTokenizer();
    if (countTokens("") !== 0) skip();
    const g = open();
    await g.insert.facts({
      entities: ["A", "B", "C"].map((name) => ({ type: "P", name })),
      relations: ["b", "c"].map((target) => ({ from_entity: "p:a", type: "KNOWS", to_entity: `p:${target}` })),
    });
    const expected = ["GRAPH | A -[KNOWS]-> B", "GRAPH | A -[KNOWS]-> C"];
    expect(expected.map(countTokens)).toEqual([10, 10]);
    expect(countTokens(expected.join("\n"))).toBe(21);
    const { context } = await g.retrieve("P", { k: 3, budget_tokens: 21 });
    expect(context.split("\n").sort()).toEqual(expected);
    expect(countTokens(context)).toBeLessThanOrEqual(21);
  });

  it("keeps a negative real-tokenizer newline delta", async ({ skip }) => {
    const { countTokens } = await chunking.loadTokenizer();
    if (countTokens("") !== 0) skip();
    const g = open({ extractor });
    const passage = 'Ann ="";';
    await g.insert.text(passage, { source_id: "negative-delta" });
    const source = `SOURCE | ${passage}`;
    const expected = `${source}\nGRAPH | Ann [Person]`;
    expect(countTokens(source)).toBe(6);
    expect(countTokens(`${source}\n`) - countTokens(source)).toBe(-2);
    expect(countTokens(expected)).toBe(10);
    const { context } = await g.retrieve("Ann", { budget_tokens: 10 });
    expect(context).toBe(expected);
    expect(countTokens(context)).toBeLessThanOrEqual(10);
  });

  it("keeps actual token counts within budget across punctuation suffixes", async () => {
    const g = open({ extractor });
    await g.insert.text("Ann [][]", { source_id: "brackets" });
    await g.insert.text('Ann ="";', { source_id: "quotes" });
    const { countTokens } = await chunking.loadTokenizer();
    const full = (await g.retrieve("Ann")).context;
    expect(full).toContain("SOURCE | Ann [][]");
    expect(full).toContain('SOURCE | Ann ="";');
    for (let budget = 1; budget <= 80; budget++) {
      const { context } = await g.retrieve("Ann", { budget_tokens: budget });
      expect(context).not.toBe("");
      expect(countTokens(context)).toBeLessThanOrEqual(budget);
      expect(
        context
          .split("\n")
          .every((line) => line.startsWith("SOURCE") || line.startsWith("GRAPH") || "SOURCE".startsWith(line)),
      ).toBe(true);
    }
  });
}

await forEachStore("context budget", suite);
