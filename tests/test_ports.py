"""Integration ports: busy semantics, retry-safe correlation, Dapier boundary."""

from datetime import datetime, timezone

import pytest

from scheduler import calendar as cal
from scheduler import dapier, emailer

UTC = timezone.utc


def dt(hour, minute=0, day=6):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def test_free_and_cancelled_events_never_block():
    """A15/A16: moved occurrences block at their real time; canceled ones and
    explicitly-free events do not."""
    provider = cal.InMemoryCalendarProvider()
    provider.events["series"] = {
        "id": "series", "start": dt(9), "end": dt(10), "status": "confirmed",
        "instances": [
            {"start": dt(11), "end": dt(12)},  # one moved occurrence
            {"start": dt(9), "end": dt(10), "status": "cancelled"},
        ],
    }
    provider.events["focus"] = {"id": "focus", "start": dt(13), "end": dt(14),
                                "status": "confirmed", "transparency": "transparent"}
    busy, incomplete = provider.query_busy("cal", dt(0), dt(23))
    assert incomplete is False
    assert (dt(11), dt(12)) in busy
    assert not any(s == dt(9) for s, _ in busy)
    assert not any(s == dt(13) for s, _ in busy)


def test_create_is_idempotent_by_correlation():
    provider = cal.InMemoryCalendarProvider()
    first = provider.create_event("cal", {"summary": "x", "start": dt(9), "end": dt(10)}, "op-1")
    second = provider.create_event("cal", {"summary": "x", "start": dt(9), "end": dt(10)}, "op-1")
    assert first["id"] == second["id"]
    assert len(provider.events) == 1


def test_unknown_outcome_reconciles_to_the_original_event():
    """B06: a timeout after a successful write must not create another event."""
    provider = cal.InMemoryCalendarProvider()
    provider.timeout_once.add("op-9")
    with pytest.raises(cal.UnknownOutcome):
        provider.create_event("cal", {"summary": "x", "start": dt(9), "end": dt(10)}, "op-9")
    found = provider.get_event("cal", "op-9")  # correlation lookup recovers it
    assert found is not None
    again = provider.create_event("cal", {"summary": "x", "start": dt(9), "end": dt(10)}, "op-9")
    assert again["id"] == found["id"]
    assert len(provider.events) == 1


def test_auth_loss_fails_closed():
    provider = cal.InMemoryCalendarProvider()
    provider.auth_lost = True
    with pytest.raises(cal.AuthLost):
        provider.query_busy("cal", dt(0), dt(1))
    with pytest.raises(cal.AuthLost):
        provider.create_event("cal", {}, "op-x")


def test_dapier_renews_without_refresh_tokens():
    """C01: the scheduler obtains usable access; rotation stays in Dapier."""
    client = dapier.FakeDapierClient()
    first = client.get_access("calendar-alexey", ["calendar.events.owned"])
    second = client.get_access("calendar-alexey", ["calendar.events.owned"])
    assert first.usable() and second.usable()
    assert first.token != second.token
    assert client.renewals == 2


def test_dapier_wrong_account_is_fatal_not_a_fallback():
    """C02: never read or write with the wrong authorization."""
    client = dapier.FakeDapierClient()
    client.wrong_account = "someone-else@example.com"
    with pytest.raises(dapier.AccountMismatch):
        client.get_access("calendar-alexey", [])


def test_dapier_denies_ungranted_connections_and_outages_close_booking():
    """C03/C04: no grant means no access; an outage pauses confirmations."""
    client = dapier.FakeDapierClient()
    with pytest.raises(dapier.GrantDenied):
        client.get_access("youtube-someone", [])
    client.missing_scopes.add("calendar.events.owned")
    with pytest.raises(dapier.InsufficientScope):
        client.get_access("calendar-alexey", ["calendar.events.owned"])
    client.missing_scopes.clear()
    client.outage = True
    with pytest.raises(dapier.DapierUnavailable):
        client.get_access("calendar-alexey", [])


def test_email_outbox_and_injected_failure():
    port = emailer.InMemoryEmailPort()
    port.fail_next = 1
    with pytest.raises(emailer.EmailError):
        port.send(to="a@example.com", subject="s", text="b")
    message_id = port.send(to="a@example.com", subject="s", text="b")
    assert port.outbox[0]["id"] == message_id
