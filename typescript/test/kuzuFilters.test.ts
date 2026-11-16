import { afterAll, beforeAll, expect, test } from "vitest";
import { KuzuStore, SelfGraph } from "../src/index.js";
import { tempKuzu } from "./helpers.js";

const path = tempKuzu("kuzu-filters-");
const store = new KuzuStore(path, { buffer_pool_size: 64 << 20, max_db_size: 512 << 20 });
const graph = new SelfGraph(store);
beforeAll(async () => {
  await graph.initialize();
});
afterAll(async () => {
  await graph.close();
});

test("matches and deletes a nested object property", async () => {
  const ns = "filters";
  const value = { profile: { name: "Zoë", tags: ["a", "b"] }, active: true };
  await store.upsertNode(ns, "Entity", "a", { id: "a", value });
  await store.upsertNode(ns, "Entity", "b", { id: "b", value: "different" });
  await store.createEdge(ns, "KNOWS", "Entity", "a", "Entity", "b", {
    id: "edge:a",
    revision: 1,
    is_current: true,
    value,
  });
  const rows = await store.matchNodes(ns, "Entity", { equals: { value } });
  expect(rows.map((row) => row.id)).toEqual(["a"]);
  const candidates = await store.matchNodes(ns, "Entity", {
    equals: { id: "a" },
    any_of: { value: ["not a match", value], id: ["a", "b"] },
  });
  expect(candidates.map((row) => row.id)).toEqual(["a"]);
  expect(await store.matchNodes(ns, "Entity", { equals: { value }, any_of: { id: [] } })).toEqual([]);
  const edges = await store.matchEdges(ns, { from_label: "Entity", equals: { value } });
  expect(edges.map((row) => row.props.id)).toEqual(["edge:a"]);
  expect(await store.deleteNodesBy(ns, "Entity", { value })).toBe(1);
  expect(await store.getNode(ns, "Entity", "a")).toBeNull();
  expect(await store.getNode(ns, "Entity", "b")).not.toBeNull();
});
