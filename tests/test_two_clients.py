from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from helpers import KUZU, run

from selfgraph import KuzuStore, SelfGraph
from selfgraph.errors import TransientError
from selfgraph.stores.kuzu import _kuzu_error

MAX_ONE = {
    "entities": [{"name": "Person"}, {"name": "Company"}],
    "relations": [{"type": "WORKS_AT", "from": {"types": ["Person"], "max_count": 1}}],
}


@pytest.mark.skipif(not KUZU, reason="two stores over one embedded database")
def test_two_clients_on_one_database_serialize_their_writes():
    path = str(Path(tempfile.mkdtemp()) / "graph.kuzu")
    first = SelfGraph(store=KuzuStore(path), namespace="two", schema=MAX_ONE, contradiction="supersede")
    second = SelfGraph(store=KuzuStore(path), namespace="two", schema=MAX_ONE, contradiction="supersede")
    run(
        first.insert.facts(
            {
                "entities": [
                    {"type": "Person", "name": "Ann"},
                    {"type": "Company", "name": "Acme"},
                    {"type": "Company", "name": "Globex"},
                ]
            }
        )
    )

    async def both_writes() -> None:
        await asyncio.gather(
            first.relations.create("person:ann", "WORKS_AT", "company:acme"),
            second.relations.create("person:ann", "WORKS_AT", "company:globex"),
        )

    run(both_writes())
    current = [r.to_entity for r in run(first.relations.list()).items]
    assert len(current) == 1, f"max_count 1 was violated: {current}"
    run(first.close())
    run(second.close())


def test_the_kuzu_write_conflict_is_reported_as_transient():
    exc = RuntimeError(
        "Cannot start a new write transaction in the system. "
        "Only one write transaction at a time is allowed in the system."
    )
    assert isinstance(_kuzu_error(exc, "Kuzu query failed"), TransientError)
