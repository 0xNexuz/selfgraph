from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest
from helpers import KUZU, run

from selfgraph import KuzuStore, SelfGraph, SyncSelfGraph
from selfgraph.stores.kuzu import _LOCKS, _WRITE_LOCKS

MAX_ONE = {
    "entities": [{"name": "Person"}, {"name": "Company"}],
    "relations": [{"type": "WORKS_AT", "from": {"types": ["Person"], "max_count": 1}}],
}

MANY_WRITERS = 64
MANY_WRITERS_SCRIPT = f"""
import asyncio, tempfile
from pathlib import Path

from selfgraph import KuzuStore, SelfGraph


async def main():
    path = str(Path(tempfile.mkdtemp()) / "graph.kuzu")
    graph = SelfGraph(store=KuzuStore(path), namespace="pool")
    await graph.initialize()

    async def one(i):
        await graph.entities.create(f"Thing {{i}}", "Thing")

    await asyncio.wait_for(asyncio.gather(*(one(i) for i in range({MANY_WRITERS}))), timeout=45)
    print(len((await graph.entities.list(limit={MANY_WRITERS} + 5)).items))


asyncio.run(main())
"""


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


@pytest.mark.skipif(not KUZU, reason="one embedded database per path")
def test_two_threads_with_their_own_loops_write_once_each():
    path = a_path()
    done: list[str] = []
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        try:
            graph = SyncSelfGraph(store=KuzuStore(path), namespace="threads")
            graph.insert.facts({"entities": [{"type": "Thing", "name": name}]})
            done.append(name)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(name,), daemon=True) for name in ("alpha", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads), "a thread hung: the path lock is not safe across loops"
    assert not errors, errors
    assert sorted(done) == ["alpha", "beta"]
    tally = SyncSelfGraph(store=KuzuStore(path), namespace="threads")
    assert len(tally.entities.list().items) == 2, "a thread wrote to a database the other one never saw"


@pytest.mark.skipif(not KUZU, reason="one embedded database per path")
def test_a_thread_and_an_event_loop_share_one_path():
    path = a_path()
    done: list[str] = []
    errors: list[BaseException] = []

    def sync_side() -> None:
        try:
            graph = SyncSelfGraph(store=KuzuStore(path), namespace="mixed")
            graph.insert.facts({"entities": [{"type": "Thing", "name": "sync-side"}]})
            done.append("sync")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def async_side() -> None:
        try:

            async def go() -> None:
                graph = SelfGraph(store=KuzuStore(path), namespace="mixed")
                await graph.initialize()
                await graph.insert.facts({"entities": [{"type": "Thing", "name": "async-side"}]})
                await graph.close()

            asyncio.run(go())
            done.append("async")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=sync_side, daemon=True),
        threading.Thread(target=async_side, daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads), "a thread hung: the path lock is not safe across threads"
    assert not errors, errors
    assert sorted(done) == ["async", "sync"]


@pytest.mark.skipif(not KUZU, reason="one embedded database per path")
def test_a_cancelled_waiter_does_not_keep_the_lock():
    path = a_path()
    store = KuzuStore(path)
    lock = store._lock  # noqa: SLF001
    holding = asyncio.Event()

    async def hold() -> None:
        async with lock:
            holding.set()
            await asyncio.sleep(0.3)

    async def wait() -> None:
        async with lock:
            pass

    async def scenario() -> None:
        holder = asyncio.create_task(hold())
        await holding.wait()
        waiter = asyncio.create_task(wait())
        await asyncio.sleep(0.05)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await holder
        await asyncio.wait_for(wait(), timeout=5)

    run(scenario())
    run(store.close())


@pytest.mark.skipif(not KUZU, reason="one embedded database per path")
def test_a_cancelled_waiter_does_not_unblock_the_waiter_behind_it():
    path = a_path()
    store = KuzuStore(path)
    lock = store._lock  # noqa: SLF001
    holding = asyncio.Event()
    released = asyncio.Event()
    entered = asyncio.Event()
    entered_while_holding = asyncio.Event()

    async def hold() -> None:
        async with lock:
            holding.set()
            await asyncio.sleep(0.3)
            released.set()

    async def wait() -> None:
        async with lock:
            pass

    async def wait_and_check() -> None:
        async with lock:
            entered.set()
            if holding.is_set() and not released.is_set():
                entered_while_holding.set()

    async def scenario() -> None:
        holder = asyncio.create_task(hold())
        await holding.wait()
        first = asyncio.create_task(wait())
        await asyncio.sleep(0.05)
        second = asyncio.create_task(wait_and_check())
        await asyncio.sleep(0.05)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not entered.is_set(), "the waiter behind the cancelled one entered while the lock was held"
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert not entered_while_holding.is_set(), "the second waiter took the lock before the holder released it"
        await second
        await holder

    run(scenario())
    run(store.close())


@pytest.mark.skipif(not KUZU, reason="the executor pool is Kuzu's")
def test_many_writers_on_one_store_do_not_starve_the_pool():
    result = subprocess.run(
        [sys.executable, "-u", "-c", MANY_WRITERS_SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == str(MANY_WRITERS), result.stdout[-2000:]
