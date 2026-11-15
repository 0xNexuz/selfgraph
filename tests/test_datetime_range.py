from datetime import datetime, timedelta, timezone

import pytest
from helpers import create_entities, new_graph, run

from selfgraph import ValidationError
from selfgraph._ops import parse_time
from selfgraph._serde import to_iso


@pytest.mark.parametrize(
    "text",
    [
        "0000-01-01T00:00:00Z",
        "10000-01-01T00:00:00Z",
        "0001-01-01T00:00:00+01:00",
        "9999-12-31T23:00:00-02:00",
        datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))),
        datetime(9999, 12, 31, 23, tzinfo=timezone(timedelta(hours=-2))),
    ],
)
def test_reject_nonportable_datetimes(text):
    with pytest.raises(ValidationError):
        parse_time(text, "event_time")
    g = new_graph()
    try:
        create_entities(g, "Person:Ann", "Company:Acme")
        with pytest.raises(ValidationError):
            run(g.relations.create("person:ann", "WORKS_AT", "company:acme", event_time=text))
        assert run(g.relations.list()).items == []
    finally:
        run(g.close())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0001-01-01T00:00:00.000001Z", "0001-01-01T00:00:00.000001+00:00"),
        ("9999-12-31T23:59:59.999999Z", "9999-12-31T23:59:59.999999+00:00"),
        ("0001-01-01T01:00:00.000001+01:00", "0001-01-01T00:00:00.000001+00:00"),
        ("9999-12-31T22:59:59.999999-01:00", "9999-12-31T23:59:59.999999+00:00"),
    ],
)
def test_portable_datetime_boundaries(text, expected):
    assert to_iso(parse_time(text, "event_time")) == expected
    g = new_graph()
    try:
        create_entities(g, "Person:Ann", "Company:Acme")
        relation = run(g.relations.create("person:ann", "WORKS_AT", "company:acme", event_time=text))
        assert to_iso(relation.event_time) == expected
        assert to_iso(run(g.relations.get(relation.id)).event_time) == expected
    finally:
        run(g.close())
