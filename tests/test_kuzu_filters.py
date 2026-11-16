import asyncio

import pytest

from selfgraph import KuzuStore, SelfGraph

pytest.importorskip("kuzu")


def test_kuzu_list_property_filters(tmp_path):
    async def check():
        store = KuzuStore(str(tmp_path / "graph.kuzu"), buffer_pool_size=64 << 20, max_db_size=512 << 20)
        async with SelfGraph(store) as graph:
            await graph.entities.create("Ann", "Person", aliases=["Annie", "Anna"])
            await graph.entities.create("Bob", "Person")
            equals = await store.match_nodes("default", "Entity", equals={"aliases": ["Annie", "Anna"]})
            any_of = await store.match_nodes("default", "Entity", any_of={"aliases": [["Annie", "Anna"]]})
            await store.create_edge(
                "default",
                "KNOWS",
                "Entity",
                "person:ann",
                "Entity",
                "person:bob",
                {"id": "relation:tags", "revision": 1, "is_current": True, "tags": ["a", "b"]},
            )
            edges = await store.match_edges("default", from_label="Entity", equals={"tags": ["a", "b"]})
            assert [r["id"] for r in equals] == ["person:ann"]
            assert [r["id"] for r in any_of] == ["person:ann"]
            assert [r["props"]["id"] for r in edges] == ["relation:tags"]
            for key in ("id", "name", "aliases"):
                assert await store.match_nodes("default", "Entity", any_of={key: []}) == []
            assert len(await store.match_nodes("default", "Entity", equals={"name": "Ann"})) == 1
            assert len(await store.match_nodes("default", "Entity", any_of={"name": ["Ann"]})) == 1
            assert await store.delete_nodes_by("default", "Entity", equals={"aliases": ["Annie", "Anna"]}) == 1

    asyncio.run(check())


@pytest.mark.parametrize(
    "value",
    [
        {"profile": {"name": "Zoë", "tags": ["a", "b"]}, "active": True},
        [],
        'Zoë 北京 "quoted" \\ path\nnext\tline',
    ],
    ids=[
        "nested-object",
        "empty-list",
        "escaped-unicode",
    ],
)
def test_kuzu_json_property_filters(tmp_path, value):
    async def check():
        store = KuzuStore(str(tmp_path / "graph.kuzu"), buffer_pool_size=64 << 20, max_db_size=512 << 20)
        async with SelfGraph(store):
            await store.upsert_node("filters", "Entity", "a", {"id": "a", "value": value})
            await store.upsert_node("filters", "Entity", "b", {"id": "b", "value": "different"})
            await store.create_edge(
                "filters",
                "KNOWS",
                "Entity",
                "a",
                "Entity",
                "b",
                {"id": "edge:a", "revision": 1, "is_current": True, "value": value},
            )
            rows = await store.match_nodes("filters", "Entity", equals={"value": value})
            assert [row["id"] for row in rows] == ["a"]
            rows = await store.match_nodes(
                "filters", "Entity", equals={"id": "a"}, any_of={"value": ["not a match", value], "id": ["a", "b"]}
            )
            assert [row["id"] for row in rows] == ["a"]
            assert await store.match_nodes("filters", "Entity", equals={"value": value}, any_of={"id": []}) == []
            edges = await store.match_edges("filters", from_label="Entity", equals={"value": value})
            assert [row["props"]["id"] for row in edges] == ["edge:a"]
            assert await store.delete_nodes_by("filters", "Entity", equals={"value": value}) == 1
            assert await store.get_node("filters", "Entity", "a") is None
            assert await store.get_node("filters", "Entity", "b") is not None

    asyncio.run(check())
