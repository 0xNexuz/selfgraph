import { describe, expect, it, vi } from "vitest";

import { type GraphStore, HydraDBStore, SelfGraph, StoreError } from "../src/index.js";
import { encodeRelationProps } from "../src/serde.js";
import { forEachStore, freshGraphs, type StoreSpec, uniqueNs } from "./helpers.js";

const props = (ns: string, id: string) => ({ id, ns, name: id });

function suite(spec: StoreSpec) {
  it.runIf(spec.name === "hydradb")("runs a nested transaction's writes on another store", async () => {
    const [outer, inner]: GraphStore[] = [spec.make(), spec.make()];
    const ns = uniqueNs("queue");
    try {
      await outer.transaction(async (a) => {
        await a.upsertNode(ns, "Source", "x:outer", props(ns, "x:outer"));
        await inner.transaction((b) => b.upsertNode(ns, "Source", "x:inner", props(ns, "x:inner")));
      });
      const found = await Promise.all(["x:outer", "x:inner"].map((id) => outer.getNode(ns, "Source", id)));
      expect(found.map((n) => n !== null)).toEqual([true, true]);
    } finally {
      await outer.close?.();
      await inner.close?.();
    }
  });

  it.runIf(spec.name === "hydradb")("checks the label of an edge endpoint that is still queued", async () => {
    const store: GraphStore = spec.make();
    const ns = uniqueNs("queue");
    try {
      const [wrong, right] = await store.transaction(async (tx) => {
        await tx.upsertNode(ns, "Source", "x:a", props(ns, "x:a"));
        await tx.upsertNode(ns, "Profile", "x:b", props(ns, "x:b"));
        return [
          await tx.createEdge(ns, "KNOWS", "Source", "x:a", "Source", "x:b", { id: "e1", ns }),
          await tx.createEdge(ns, "KNOWS", "Source", "x:a", "Profile", "x:b", { id: "e2", ns }),
        ];
      });
      expect([wrong, right]).toEqual([false, true]);
      expect((await store.listEdgeVersions(ns, "e2")).map((e) => e.props.id)).toEqual(["e2"]);
    } finally {
      await store.close?.();
    }
  });

  it.runIf(spec.name === "hydradb")("leaves evidence unwritten when a write queued before it fails", async () => {
    const store: GraphStore = spec.make();
    const ns = uniqueNs("queue");
    const queue = store as unknown as { exec: (query: string, params?: unknown) => Promise<unknown> };
    const real = queue.exec.bind(store);
    try {
      queue.exec = (query, params) =>
        query.includes("SET n:Source") ? Promise.reject(new StoreError("write failed")) : real(query, params);
      await expect(
        store.transaction(async (tx) => {
          await tx.upsertNode(ns, "Source", "x:s", props(ns, "x:s"));
          await tx.upsertNode(ns, "Evidence", "x:e", { ...props(ns, "x:e"), target_id: "x:s" });
        }),
      ).rejects.toThrow(StoreError);
      queue.exec = real;
      expect(await store.getNode(ns, "Evidence", "x:e")).toBeNull();
    } finally {
      await store.close?.();
    }
  });

  it.runIf(spec.name === "hydradb")("applies extra_label to the node", async () => {
    const store: GraphStore = spec.make();
    const ns = uniqueNs("queue");
    try {
      await store.transaction(async (tx) => {
        await tx.upsertNode(ns, "Source", "x:a", props(ns, "x:a"), { extra_label: "CustomLabel" });
      });
      const byPrimary = await store.getNode(ns, "Source", "x:a");
      const byExtra = await store.getNode(ns, "CustomLabel", "x:a");
      expect(byPrimary).not.toBeNull();
      expect(byExtra).not.toBeNull();
      expect(byExtra?.id).toBe("x:a");
    } finally {
      await store.close?.();
    }
  });

  it.runIf(spec.name === "hydradb")("writes entity types that are not HydraDB labels", async () => {
    const ns = uniqueNs("labels");
    const graph = new SelfGraph(spec.make(), { namespace: ns });
    try {
      await graph.initialize();
      for (const [name, type] of [
        ["Acme", "Big Co"],
        ["Delhi", "शहर"],
        ["Ada", "Person"],
        ["Bot", "Big-Co.v2"],
      ] as const) {
        await graph.entities.create(name, type);
      }
      const types = Object.fromEntries((await graph.entities.list()).items.map((e) => [e.id, e.type]));
      expect(types).toEqual({
        "big-co:acme": "Big Co",
        "शहर:delhi": "शहर",
        "person:ada": "Person",
        "big-co.v2:bot": "Big-Co.v2",
      });
      expect(await graph.store.getNode(ns, "Person", "person:ada")).not.toBeNull();
    } finally {
      await graph.close();
    }
  });

  it.runIf(spec.name === "hydradb")(
    "scans edges by the endpoint labels each edge stores, not by labelling both ends",
    async () => {
      const g = freshGraphs(spec, "scan")();
      await g.entities.create("A", "T");
      await g.entities.create("B", "T");
      const preference = await g.preferences.create("t:a", "drink", "t:b");
      const stamp = new Date();
      await g.store.createEdges(
        g.namespace,
        "Entity",
        "Entity",
        Array.from({ length: 300 }, (_, i) => [
          "KNOWS",
          "t:a",
          "t:b",
          encodeRelationProps({
            ns: g.namespace,
            id: "relation:many",
            revision: 300 - i,
            properties: {},
            event_time: null,
            created_at: stamp,
            is_current: i === 0,
            source_count: 0,
          }),
        ]),
      );
      const run = vi.spyOn(g.store as unknown as { _run: (q: string, p?: unknown) => Promise<unknown> }, "_run");
      try {
        const between = await g.store.matchEdges(g.namespace, {
          from_label: "Entity",
          to_label: "Entity",
          limit: 1000,
        });
        const fromProfiles = await g.store.matchEdges(g.namespace, { from_label: "Profile", limit: 1000 });
        expect(new Set(between.map((e) => e.props.id))).toEqual(new Set(["relation:many"]));
        expect(between.map((e) => e.props.revision)).toEqual(Array.from({ length: 300 }, (_, i) => i + 1));
        expect(fromProfiles.map((e) => e.props.id)).toEqual([preference.id]);
        const queries = run.mock.calls.map(([q]) => q);
        expect(queries.length).toBeGreaterThan(0);
        expect(queries.every((q) => !q.includes("(a:") && !q.includes("(b:"))).toBe(true);
      } finally {
        run.mockRestore();
        await g.close();
      }
    },
  );
}

await forEachStore("HydraDB write queue", suite);

describe("HydraDB write queue failure", () => {
  it("rejects the transaction without an unhandled rejection when a queued write fails", async () => {
    const store = new HydraDBStore("bolt://127.0.0.1:1", { auth: ["neo4j", "unused"] });
    const queue = store as unknown as { exec: (query: string, params?: unknown) => Promise<unknown> };
    queue.exec = (query) =>
      query.includes("SET n:Source") ? Promise.reject(new StoreError("write failed")) : Promise.resolve({ rows: [] });
    const unhandled: unknown[] = [];
    const record = (reason: unknown) => unhandled.push(reason);
    process.on("unhandledRejection", record);
    try {
      await expect(store.transaction((tx) => tx.upsertNode("ns", "Source", "x:s", props("ns", "x:s")))).rejects.toThrow(
        "write failed",
      );
      await new Promise((resolve) => setTimeout(resolve, 10));
      expect(unhandled).toEqual([]);
    } finally {
      process.off("unhandledRejection", record);
      await store.close();
    }
  });
});
