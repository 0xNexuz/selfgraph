import { expect, test } from "vitest";
import { ValidationError } from "../src/index.js";
import { toIso } from "../src/internal/time.js";
import { parseTime } from "../src/validate.js";
import { forEachStore, freshGraphs } from "./helpers.js";

await forEachStore("portable datetime range", (spec) => {
  const graph = freshGraphs(spec, "datetime_range");

  test.each([
    "0000-01-01T00:00:00Z",
    "10000-01-01T00:00:00Z",
    "0001-01-01T00:00:00+01:00",
    "9999-12-31T23:00:00-02:00",
    new Date("0000-01-01T00:00:00Z"),
    new Date("+010000-01-01T00:00:00Z"),
  ])("rejects %s before a relation is stored", async (value) => {
    const g = graph();
    await g.entities.create("Ann", "Person");
    await g.entities.create("Acme", "Company");
    await expect(
      g.relations.create("person:ann", "WORKS_AT", "company:acme", {
        id: "relation:invalid",
        event_time: value,
      }),
    ).rejects.toBeInstanceOf(ValidationError);
    expect((await g.relations.list()).items).toEqual([]);
  });

  test.each([
    ["0001-01-01T00:00:00.000001Z", "0001-01-01T00:00:00.000001+00:00"],
    ["9999-12-31T23:59:59.999999Z", "9999-12-31T23:59:59.999999+00:00"],
    ["0001-01-01T01:00:00.000001+01:00", "0001-01-01T00:00:00.000001+00:00"],
    ["9999-12-31T22:59:59.999999-01:00", "9999-12-31T23:59:59.999999+00:00"],
  ])("preserves valid boundary %s", async (text, expected) => {
    expect(toIso(parseTime(text, "event_time"))).toBe(expected);
    const g = graph();
    await g.entities.create("Ann", "Person");
    await g.entities.create("Acme", "Company");
    const relation = await g.relations.create("person:ann", "WORKS_AT", "company:acme", { event_time: text });
    expect(toIso(relation.event_time)).toBe(expected);
    expect(toIso((await g.relations.get(relation.id))!.event_time)).toBe(expected);
  });
});
