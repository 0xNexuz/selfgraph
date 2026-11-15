import type { SelfGraph } from "../../src/index.js";
import { epochMicros } from "../../src/internal/time.js";

/**
 * Compare every timestamp-boundary window over the fixture's stored changes.
 *
 * @param graph (SelfGraph): the shared graph.
 * @return (unknown): compact diff outputs for exact cross-SDK comparison.
 */
export async function diffWindows(graph: SelfGraph): Promise<unknown> {
  const all = await graph.diff("1900-01-01", "2100-01-01");
  const times = [
    ...new Map(
      [...all.relations_added, ...all.relations_revised, ...all.relations_retired]
        .flatMap((r) => [r.created_at, r.invalidated_known_at].filter((at): at is Date => at !== null))
        .map((at) => [epochMicros(at), at]),
    ).values(),
  ].sort((a, b) => epochMicros(a) - epochMicros(b));
  const out = [];
  for (const since of times) {
    for (const until of times) {
      if (epochMicros(since) > epochMicros(until)) continue;
      const d = await graph.diff(since, until);
      out.push({
        since,
        until,
        added: d.relations_added.map((r) => [r.id, r.revision]),
        revised: d.relations_revised.map((r) => [r.id, r.revision]),
        retired: d.relations_retired.map((r) => [r.id, r.revision, r.invalidated_by]),
        entities: d.entities_created.map((e) => e.id),
        limitations: d.limitations,
      });
    }
  }
  return out;
}
