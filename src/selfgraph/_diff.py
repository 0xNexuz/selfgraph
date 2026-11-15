from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any

from . import _ops
from ._serde import L_ENTITY, aware, decode_entity
from .models import GraphDiff, Relation


async def graph_diff(runner: Any, ns: str, since: datetime, until: datetime) -> GraphDiff:
    """Read stored creations, revisions, and retirements in the known-time window (since, until].

    @param runner (Any): the graph runner.
    @param ns (str): the namespace.
    @param since (datetime): exclusive lower bound.
    @param until (datetime): inclusive upper bound.
    @return (GraphDiff): changes in deterministic id and revision order.
    """
    rows = await _ops._paged(
        partial(runner.match_edges, ns, from_label=L_ENTITY, to_label=L_ENTITY, current_only=False, order_by="id")
    )
    relations = sorted((_ops._relation_of(row) for row in rows), key=lambda r: (r.id, r.revision))
    result = GraphDiff(since=since, until=until)
    retired: dict[tuple[str, datetime], Relation] = {}
    for relation in relations:
        if since < aware(relation.created_at) <= until:
            target = result.relations_added if relation.revision == 1 else result.relations_revised
            target.append(relation)
        ended = relation.invalidated_known_at
        if ended is not None and since < aware(ended) <= until and aware(relation.created_at) <= aware(ended):
            # Later revisions may copy a retirement; keep the last revision that existed when it happened.
            retired[(relation.id, ended)] = relation
    result.relations_retired = list(retired.values())
    entities = await _ops._paged(partial(runner.match_nodes, ns, L_ENTITY, order_by="id"))
    for row in entities:
        entity = decode_entity(row)
        if since < aware(entity.created_at) <= until:
            result.entities_created.append(entity)
    return result
