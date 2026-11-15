import { afterAll, beforeAll, expect, test } from "vitest";
import { KuzuStore, SelfGraph } from "../src/index.js";
import { tempKuzu } from "./helpers.js";

const path = tempKuzu("kuzu-filters-");
const store = new KuzuStore(path, { buffer_pool_size: 64 << 20, max_db_size: 512 << 20 });
const graph = new SelfGraph(store);
beforeAll(async () => {
  await graph.entities.create("Ann", "Person", { aliases: ["Annie", "Anna"] });
  await graph.entities.create("Bob", "Person");
  await store.createEdge("default", "KNOWS", "Entity", "person:ann", "Entity", "person:bob", {
    id: "relation:tags",
    revision: 1,
    is_current: true,
    tags: ["a", "b"],
  });
});
afterAll(async () => {
  await graph.close();
});

test("matches SDK entity aliases by exact list value", async () => {
  const rows = await store.matchNodes("default", "Entity", { equals: { aliases: ["Annie", "Anna"] } });
  expect(rows.map((r) => r.id)).toEqual(["person:ann"]);
});
test("matches a list-valued any_of candidate", async () => {
  const rows = await store.matchNodes("default", "Entity", { any_of: { aliases: [["Annie", "Anna"]] } });
  expect(rows.map((r) => r.id)).toEqual(["person:ann"]);
});
test("matches list-valued edge properties", async () => {
  const rows = await store.matchEdges("default", { from_label: "Entity", equals: { tags: ["a", "b"] } });
  expect(rows.map((r) => r.props.id)).toEqual(["relation:tags"]);
});

test.each(["id", "name", "aliases"])("matches nothing for empty any_of %s", async (key) => {
  expect(await store.matchNodes("default", "Entity", { any_of: { [key]: [] } })).toEqual([]);
});

test("preserves scalar filters", async () => {
  expect(await store.matchNodes("default", "Entity", { equals: { name: "Ann" } })).toHaveLength(1);
  expect(await store.matchNodes("default", "Entity", { any_of: { name: ["Ann"] } })).toHaveLength(1);
});

test("deletes by exact list value", async () => {
  expect(await store.deleteNodesBy("default", "Entity", { aliases: ["Annie", "Anna"] })).toBe(1);
});

test.each([
  { name: "strings", value: ["Annie", "Anna"] },
  { name: "numbers", value: [1, 2] },
  { name: "object", value: { a: 1, b: 2 } },
  { name: "nested-object", value: { profile: { name: "Zoë", tags: ["a", "b"] }, active: true } },
  { name: "empty-list", value: [] },
  { name: "empty-object", value: {} },
  { name: "string", value: "Ann" },
  { name: "escaped-unicode", value: 'Zoë 北京 "quoted" \\ path\nnext\tline' },
  { name: "number", value: 2 },
  { name: "boolean", value: true },
])("matches and deletes JSON property values: $name", async ({ name: ns, value }) => {
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
