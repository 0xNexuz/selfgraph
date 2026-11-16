from datetime import datetime, timezone

import pytest

from selfgraph._serde import from_iso, to_iso


def test_from_iso_handles_trailing_z_as_utc():
    """A trailing Z or z is valid ISO 8601 and must read as UTC, even on Python 3.10.

    @param (none)
    @return (None)
    """
    for value in ("2024-01-02T03:04:05Z", "2024-01-02T03:04:05z"):
        got = from_iso(value)
        assert got == datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        assert got.tzinfo is not None


def test_from_iso_matches_parse_time_on_z():
    """from_iso must agree with _ops.parse_time, which already accepts a trailing Z.

    @param (none)
    @return (None)
    """
    from selfgraph._ops import parse_time

    for value in ("2024-01-02T03:04:05Z", "2024-01-02T03:04:05+00:00"):
        assert from_iso(value) == parse_time(value, "event_time")


def test_from_iso_round_trips_with_to_iso():
    """to_iso writes +00:00 and from_iso must read it back unchanged.

    @param (none)
    @return (None)
    """
    dt = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert from_iso(to_iso(dt)) == dt


def test_from_iso_none_and_passthrough():
    """None maps to None and a datetime is returned as given.

    @param (none)
    @return (None)
    """
    assert from_iso(None) is None
    dt = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert from_iso(dt) is dt


def test_from_iso_rejects_bad_text():
    """Garbage input must raise ValidationError like parse_time does.

    @param (none)
    @return (None)
    """
    from selfgraph.errors import ValidationError

    with pytest.raises(ValidationError):
        from_iso("not-a-date")
