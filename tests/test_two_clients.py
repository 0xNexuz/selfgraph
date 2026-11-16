from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from helpers import KUZU, run

from selfgraph import KuzuStore, SelfGraph
from selfgraph.stores.kuzu import _LOCKS, _WRITE_LOCKS

MAX_ONE = {
    "entities": [{"name": "Person"}, {"name": "Company"}],
    "relations": [{"type": "WORKS_AT", "from": {"types": ["Person"], "max_count": 1}}],
}


def a_path() -> str:
    return str(Path(tempfile.mkdtemp()) / "graph.kuzu")


@pytest.mark.skipif(not KUZU, reason="two stores over one embedded database")
def test_two_clients_on_one_database_serialize_their_writes():
    path = a_path()
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


@pytest.mark.skipif(not KUZU, reason="the lock caches are Kuzu's")
def test_the_shared_locks_are_dropped_with_the_database():
    path = a_path()
    store = KuzuStore(path)
    assert path in _LOCKS and path in _WRITE_LOCKS
    run(store.close())
    assert path not in _LOCKS, "the lock outlived the database"
    assert path not in _WRITE_LOCKS, "the write lock outlived the database"


@pytest.mark.skipif(not KUZU, reason="one embedded database per path")
def test_a_second_event_loop_uses_the_path_after_the_first_closes():
    path = a_path()

    async def pair(tag: str) -> int:
        first = SelfGraph(store=KuzuStore(path), namespace="loops")
        second = SelfGraph(store=KuzuStore(path), namespace="loops")
        await first.initialize()
        await second.initialize()
        await asyncio.gather(
            first.insert.facts({"entities": [{"type": "Thing", "name": f"a-{tag}"}]}),
            second.insert.facts({"entities": [{"type": "Thing", "name": f"b-{tag}"}]}),
        )
        total = len((await first.entities.list()).items)
        await first.close()
        await second.close()
        return total

    assert asyncio.run(pair("one")) == 2
    assert asyncio.run(pair("two")) == 4
