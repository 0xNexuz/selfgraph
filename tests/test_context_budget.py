from datetime import datetime, timezone

import pytest

from selfgraph import Entity, Relation
from selfgraph.extraction.chunking import count_tokens
from selfgraph.retrieval import _assemble

NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def entity(name, index=0):
    return Entity(id=f"p:{index}", type="P", name=name, created_at=NOW, updated_at=NOW)


def assemble(*, passages=(), entities=(), relations=(), names=None, budget=200000):
    return _assemble(list(passages), list(entities), list(relations), [], [], names or {}, {}, budget)


@pytest.mark.parametrize("size", [100, 200, 400])
def test_tokenizer_work_is_linear_in_rendered_context(monkeypatch, size):
    calls = []

    def measure(text):
        calls.append(len(text))
        return max(1, len(text) // 4)

    monkeypatch.setattr("selfgraph.retrieval.count_tokens", measure)
    relations = [
        Relation(
            id=f"relation:{i}",
            revision=1,
            from_entity="p:ann",
            type="KNOWS",
            to_entity=f"p:{i}",
            properties={"note": "long evidence " * 80},
            created_at=NOW,
            is_current=True,
        )
        for i in range(size)
    ]
    context = assemble(relations=relations, budget=1000000)
    assert len(context.splitlines()) == size
    assert max(calls) <= max(map(len, context.splitlines())) + 1
    assert sum(calls) <= 3 * len(context)


@pytest.mark.parametrize("names", [("A", "B", "C"), ("😀", "🚀", "🦉")])
def test_fallback_rounding_and_unicode_do_not_exceed_budget(monkeypatch, names):
    monkeypatch.setattr("selfgraph.retrieval.count_tokens", lambda text: max(1, len(text) // 4))
    entities = [entity(name, i) for i, name in enumerate(names)]
    full = assemble(entities=entities)
    assert len(full) == 41
    context = assemble(entities=entities, budget=9)
    assert len(context.splitlines()) < 3
    assert max(1, len(context) // 4) <= 9
    for budget in range(1, 18):
        context = assemble(entities=entities, budget=budget)
        assert context and max(1, len(context) // 4) <= budget
        assert full.startswith(context)


def test_rejected_passages_do_not_consume_budget_and_global_overflow_stops(monkeypatch):
    monkeypatch.setattr("selfgraph.retrieval.count_tokens", lambda text: max(1, len(text) // 4))
    assert assemble(passages=["Ann\n\n[][]"], entities=[entity("A")], budget=12) == "GRAPH | A [P]"
    assert assemble(passages=["Ann " * 100], entities=[entity("A")], budget=4) == "GRAPH | A [P]"
    context = assemble(entities=[entity("A", 0), entity("long " * 100, 1), entity("B", 2)], budget=7)
    assert context == "GRAPH | A [P]"


def test_real_tokenizer_punctuation_boundaries_fit_the_budget():
    if count_tokens("") != 0:
        pytest.skip("cl100k_base tokenizer is unavailable")
    passages = ["Ann [][]", 'Ann ="";']
    full = assemble(passages=passages, entities=[entity("A")])
    assert all("SOURCE | " + passage in full for passage in passages)
    for budget in range(1, 81):
        context = assemble(passages=passages, entities=[entity("A")], budget=budget)
        assert context and count_tokens(context) <= budget
        assert all(line.startswith(("SOURCE", "GRAPH")) or "SOURCE".startswith(line) for line in context.splitlines())


def test_real_tokenizer_does_not_pay_fallback_rounding_for_each_record():
    if count_tokens("") != 0:
        pytest.skip("cl100k_base tokenizer is unavailable")
    entities = [entity(name, i).model_copy(update={"type": "Person"}) for i, name in enumerate(("A", "B"))]
    expected = "GRAPH | A [Person]\nGRAPH | B [Person]"
    assert [count_tokens(line) for line in expected.splitlines()] == [6, 6]
    assert count_tokens(expected) == 12
    assert assemble(entities=entities, budget=12) == expected


def test_real_tokenizer_does_not_charge_a_final_newline():
    if count_tokens("") != 0:
        pytest.skip("cl100k_base tokenizer is unavailable")
    relations = [
        Relation(
            id=f"relation:{target}",
            revision=1,
            from_entity="p:a",
            type="KNOWS",
            to_entity=f"p:{target}",
            created_at=NOW,
            is_current=True,
        )
        for target in ("b", "c")
    ]
    expected = "GRAPH | A -[KNOWS]-> B\nGRAPH | A -[KNOWS]-> C"
    assert [count_tokens(line) for line in expected.splitlines()] == [10, 10]
    assert count_tokens(expected) == 21
    context = assemble(relations=relations, names={"p:a": "A", "p:b": "B", "p:c": "C"}, budget=21)
    assert context == expected
    assert count_tokens(context) <= 21


def test_real_tokenizer_keeps_a_negative_newline_delta():
    if count_tokens("") != 0:
        pytest.skip("cl100k_base tokenizer is unavailable")
    passage = 'Ann ="";'
    source = "SOURCE | " + passage
    expected = source + "\nGRAPH | Ann [Person]"
    assert count_tokens(source) == 6
    assert count_tokens(source + "\n") - count_tokens(source) == -2
    assert count_tokens(expected) == 10
    context = assemble(passages=[passage], entities=[entity("Ann").model_copy(update={"type": "Person"})], budget=10)
    assert context == expected
    assert count_tokens(context) <= 10
