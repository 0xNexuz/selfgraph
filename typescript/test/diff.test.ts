import { expect, it, vi } from "vitest";
import { StoreError, ValidationError } from "../src/index.js";
import { CypherPrimitives } from "../src/stores/cypherPrimitives.js";
import { forEachStore, freshGraphs, type StoreSpec } from "./helpers.js";

function suite(spec: StoreSpec): void {
  const open = freshGraphs(spec, "diff");
  it("reports known-time revisions and exact boundaries without writes", async () => {
    const g = open();
    await g.entities.create("Ann", "Person");
    await g.entities.create("Acme", "Company");
    const first = await g.relations.create("person:ann", "WORKS_AT", "company:acme", { event_time: "2000-01-01" });
    const second = await g.relations.update(first.id, { set_properties: { role: "cto" } });
    const diff = await g.diff("1999-01-01", second.created_at);
    expect(diff.relations_added.map((r) => [r.id, r.revision])).toEqual([[first.id, 1]]);
    expect(diff.relations_revised.map((r) => [r.id, r.revision])).toEqual([[first.id, 2]]);
    expect(diff.entities_created.map((e) => e.id)).toEqual(["company:acme", "person:ann"]);
    expect(diff.limitations).toContain("Deletes and merges are not visible.");
    const boundary = await g.diff(first.created_at, second.created_at);
    expect(boundary.relations_added).toEqual([]);
    expect(boundary.relations_revised.map((r) => r.revision)).toEqual([2]);
    expect((await g.diff(second.created_at, second.created_at)).relations_revised).toEqual([]);
    expect(await open().diff("1999-01-01", "2100-01-01")).toMatchObject({ entities_created: [], relations_added: [] });
    await expect(g.diff("bad")).rejects.toThrow(ValidationError);
    await expect(g.diff("2026-01-02", "2026-01-01")).rejects.toThrow("until must be at or after since");
    await g.close();
    await expect(g.diff("1999-01-01")).rejects.toThrow(StoreError);
  });
  it("normalizes offsets, preserves microsecond boundaries, and counts updates as revisions", async () => {
    const g = open();
    await g.entities.create("Ann", "Person");
    await g.entities.create("Acme", "Company");
    const first = await g.relations.create("person:ann", "WORKS_AT", "company:acme");
    const again = await g.relations.update(first.id, { set_properties: { role: "cto" } });
    const result = await g.diff("1900-01-01T05:30:00+05:30", "2100-01-01");
    expect(result.since.toISOString()).toBe("1900-01-01T00:00:00.000Z");
    expect(result.relations_added.map((r) => r.revision)).toEqual([1]);
    expect(result.relations_revised.map((r) => r.revision)).toEqual([again.revision]);
    const empty = await g.diff("2100-01-01T00:00:00.000001Z", "2100-01-01T00:00:00.000002Z");
    expect(empty.relations_added).toEqual([]);
    await expect(g.diff("2100-01-01T00:00:00.000002Z", "2100-01-01T00:00:00.000001Z")).rejects.toThrow(
      "until must be at or after since",
    );
    expect((await g.diff(first.created_at)).relations_added).toEqual([]);
  });

  it("pages records and performs no writes after initialization", async () => {
    const g = open();
    const entities = Array.from({ length: 505 }, (_, i) => ({ type: "T", name: String(i) }));
    await g.insert.facts({
      entities,
      relations: entities.map((_, i) => ({
        id: `relation:page-${i}`,
        from_entity: "t:0",
        type: "KNOWS",
        to_entity: "t:1",
      })),
    });
    const edges = vi.spyOn(g.store, "matchEdges");
    const nodes = vi.spyOn(g.store, "matchNodes");
    const writes = vi.spyOn(g.store, "upsertNode").mockImplementation(async () => {
      throw new Error("diff wrote");
    });
    try {
      const result = await g.diff("1900-01-01", "2100-01-01");
      expect(result.entities_created).toHaveLength(505);
      expect(new Set(result.relations_added.map((r) => r.id)).size).toBe(505);
      expect(edges.mock.calls.every(([, o]) => o?.limit === 500)).toBe(true);
      expect(edges.mock.calls.some(([, o]) => o?.skip === 500)).toBe(true);
      expect(nodes.mock.calls.some(([, , o]) => o?.skip === 500)).toBe(true);
      expect(writes).not.toHaveBeenCalled();
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("reports supersede and source-replacement retirements", async () => {
    const g = open({
      schema: {
        entities: [{ name: "Person" }, { name: "Company" }],
        relations: [{ type: "WORKS_AT", from: { max_count: 1 } }],
      },
      contradiction: "supersede",
    });
    await g.insert.facts({
      entities: [
        { type: "Person", name: "Ann" },
        { type: "Company", name: "Acme" },
        { type: "Company", name: "Globex" },
      ],
    });
    const a = await g.relations.create("person:ann", "WORKS_AT", "company:acme", { event_time: "2000-01-01" });
    const b = await g.relations.create("person:ann", "WORKS_AT", "company:globex", { event_time: "2001-01-01" });
    const diff = await g.diff("1900-01-01", "2100-01-01");
    expect(diff.relations_retired.map((r) => [r.id, r.invalidated_by])).toEqual([[a.id, b.id]]);
    const ended = diff.relations_retired[0]!.invalidated_known_at!;
    expect((await g.diff(ended, ended)).relations_retired).toEqual([]);
    const revived = await g.relations.create("person:ann", "WORKS_AT", "company:acme");
    const later = await g.diff(ended, revived.created_at);
    expect(later.relations_revised.map((r) => [r.id, r.revision])).toEqual([[a.id, 2]]);
    expect(later.relations_retired.map((r) => r.id)).toEqual([b.id]);
    await g.insert.facts({
      relations: [
        {
          id: b.id,
          from_entity: "person:ann",
          type: "WORKS_AT",
          to_entity: "company:globex",
          properties: { note: "still retired" },
          event_time: "1900-01-01",
        },
      ],
    });
    const copied = await g.diff(revived.created_at, "2100-01-01");
    expect(copied.relations_retired).toEqual([]);
    expect(copied.relations_revised.map((r) => [r.id, r.revision])).toEqual([[b.id, 2]]);
    await g.insert.text("Ann works at Acme in Paris.", { source_id: "note" });
    await g.insert.text("Ann works at Globex in Berlin.", { source_id: "note", replace_source: true });
    expect((await g.diff("1900-01-01", "2100-01-01")).relations_retired.length).toBeGreaterThan(1);
    await g.relations.delete(b.id);
    expect((await g.diff("1900-01-01", "2100-01-01")).relations_added.some((r) => r.id === b.id)).toBe(false);
  });
}
await forEachStore("graph diff", suite);

it("orders Bolt edge pages by relation id and revision", async () => {
  class Capture extends CypherPrimitives {
    query = "";
    protected async _run(query: string) {
      this.query = query;
      return { columns: [], rows: [], counters: {}, warnings: [] };
    }
  }
  const store = new Capture();
  await store.matchEdges("test", { from_label: "Entity", current_only: false, skip: 500, limit: 500 });
  expect(store.query).toContain("ORDER BY r.id, r.revision SKIP $skip LIMIT $limit");
});
