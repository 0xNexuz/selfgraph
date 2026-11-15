from helpers import FakeLLM, fresh_graph, run, utc

from selfgraph.extraction.extractor import _lenient_event_time, _RelationsOut, _RelOut


def test_lenient_event_time_accepts_year_and_iso_and_drops_noise():
    assert _lenient_event_time("2021") == utc(2021)
    assert _lenient_event_time(2021) == utc(2021)
    assert _lenient_event_time("2021-06-15T12:00:00Z") == utc(2021, 6, 15).replace(hour=12)
    assert _lenient_event_time("March 2021") is None
    assert _lenient_event_time("") is None
    assert _lenient_event_time(None) is None


def test_rel_out_keeps_year_as_string_not_unix_seconds():
    rel = _RelOut(from_name="a", type="T", to_name="b", event_time="2021")
    assert rel.event_time == "2021"
    assert (
        _RelationsOut.model_validate(
            {"relations": [{"from_name": "a", "type": "T", "to_name": "b", "event_time": "2021"}]}
        )
        .relations[0]
        .event_time
        == "2021"
    )


def test_insert_text_stores_year_event_time_and_keeps_unparseable_relation():
    class YearLLM(FakeLLM):
        async def generate(self, *, system, user, output):
            name = output.__name__
            if name == "_EntitiesOut":
                return {
                    "entities": [
                        {"type": "Person", "name": "Ann"},
                        {"type": "Company", "name": "Acme"},
                    ]
                }
            if name == "_RelationsOut":
                return {
                    "relations": [
                        {"from_name": "Ann", "type": "JOINED", "to_name": "Acme", "event_time": "2021"},
                        {"from_name": "Ann", "type": "KNOWS", "to_name": "Acme", "event_time": "March 2021"},
                    ]
                }
            return {name[1:-3].lower(): []}

    g = fresh_graph("extract-year", llm=YearLLM(), resolver="exact")
    res = run(g.insert.text("Ann joined Acme in 2021.", source_id="doc"))
    by_type = {r.type: r for r in res.relations}
    assert by_type["JOINED"].event_time == utc(2021)
    assert by_type["KNOWS"].event_time is None
    early = run(g.retrieve("Ann Acme", as_of="2000-01-01"))
    assert [(r.type, r.to_entity) for r in early.subgraph.relations] == []
