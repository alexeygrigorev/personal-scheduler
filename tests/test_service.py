"""Booking lifecycle acceptance coverage (spec sections 7, 8, 16.2)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scheduler import service, store
from scheduler.calendar import InMemoryCalendarProvider
from scheduler.dapier import FakeDapierClient
from scheduler.emailer import InMemoryEmailPort
from scheduler.models import parse_iso

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)  # a Monday


def berlin(day, month, hhmm):
    hours, minutes = map(int, hhmm.split(":"))
    return datetime(2026, month, day, hours, minutes, tzinfo=BERLIN)


@pytest.fixture
def env(table):
    store.ensure_seed()
    store.put_schedule({"id": "default", "name": "Default", "timezone": "Europe/Berlin",
                        "weekly_windows": {"0": [["09:00", "17:00"]], "1": [["09:00", "17:00"]],
                                           "2": [["09:00", "17:00"]], "3": [["09:00", "17:00"]],
                                           "4": [["09:00", "17:00"]]},
                        "overrides": {}, "version": 1})
    store.update_host({"default_notice_hours": 0, "host_notification_email": "host@example.com",
                       "public_base_url": "https://scheduler.test"})
    store.update_calendar_connection({"selected_calendar_id": "cal-1",
                                      "expected_provider": "google",
                                      "expected_account": "host@example.com"})
    return SimpleNamespace(provider=InMemoryCalendarProvider(),
                           dapier=FakeDapierClient(),
                           email=InMemoryEmailPort())


def book(env, **overrides):
    # dtc-30 seeds a required question (mirroring the host's Calendly page),
    # so the default payload answers it; explicit overrides replace it whole.
    params = {"event_type_id": "dtc-30", "duration_min": 30,
              "start_iso": berlin(6, 10, "09:00").isoformat(),
              "name": "Ada", "email": "ada@example.com",
              "answers": {"discuss": "Scheduler migration chat"},
              "idempotency_key": f"key-{len(env.provider.events)}-{berlin(6,10,'09:00').isoformat()}",
              "dapier_client": env.dapier, "provider": env.provider, "now": NOW}
    params.update(overrides)
    return service.create_booking(**params)


def test_long_meeting_is_one_booking_and_one_event(env):
    """B01: three hours produce one booking and one three-hour event."""
    result = book(env, event_type_id="flexible", duration_min=180,
                  start_iso=berlin(6, 10, "09:00").isoformat(), notes="podcast agenda",
                  idempotency_key="long-1")
    assert result["status"] == "confirmed"
    assert result["booking"]["duration_min"] == 180
    assert len(env.provider.events) == 1
    event = next(iter(env.provider.events.values()))
    assert (parse_iso(event["end"]["dateTime"]) - parse_iso(event["start"]["dateTime"])) \
        == timedelta(minutes=180)
    assert result["manage_token"]


def test_booking_through_one_link_removes_offers_from_every_other(env):
    """B02: all four links share one conflict domain."""
    book(env, idempotency_key="b02")
    from scheduler import availability as av
    host = store.get_host()
    sched = store.get_schedule("default")
    day = berlin(6, 10, "00:00").date()
    for et_id, duration in (("dtc-30", 30), ("general-60", 60), ("flexible", 180)):
        et = store.get_event_type(et_id)
        result = av.generate_slots(
            schedule=sched, event_type=et, host=host, duration_min=duration,
            busy=[], local_protected=[(parse_iso(s), parse_iso(e))
                                      for s, e, _ in store.active_protection(
                                          "2026-10-06T00:00:00+00:00", "2026-10-07T00:00:00+00:00")],
            blocks=[], existing_bookings=[], now_utc=NOW, from_date=day, to_date=day)
        assert all(offer.start != berlin(6, 10, "09:00") for offer in result["slots"]), et_id


def test_overlapping_concurrent_requests_conflict(env):
    """B03: identical starts with different durations, and overlapping starts,
    are conflicts — not just identical requests."""
    book(env, idempotency_key="b03-first")
    with pytest.raises(service.BookingError) as exc:
        book(env, event_type_id="general-60", duration_min=60, idempotency_key="b03-second")
    assert exc.value.code == "slot_unavailable"
    with pytest.raises(service.BookingError):
        book(env, start_iso=berlin(6, 10, "09:15").isoformat(), idempotency_key="b03-third")


def test_double_submit_returns_the_same_booking(env):
    """B04/B05: identical retries are idempotent; a reused key with different
    data is rejected rather than silently changing intent."""
    first = book(env, idempotency_key="b04")
    params = {"event_type_id": "dtc-30", "duration_min": 30,
              "start_iso": berlin(6, 10, "09:00").isoformat(),
              "name": "Ada", "email": "ada@example.com",
              "answers": {"discuss": "Scheduler migration chat"},
              "idempotency_key": "b04",
              "dapier_client": env.dapier, "provider": env.provider, "now": NOW}
    second = service.create_booking(**params)
    assert second["duplicate"] is True
    assert second["booking"]["reference"] == first["booking"]["reference"]
    assert len(env.provider.events) == 1
    with pytest.raises(service.BookingError) as exc:
        service.create_booking(**{**params, "name": "Mallory"})
    assert exc.value.code == "idempotency_conflict"


def test_unknown_write_outcome_reconciles_without_duplication(env):
    """B06/B07/B08: timeout after success recovers one booking; protection
    holds while the outcome is unknown."""
    env.provider.timeout_once.add("create:op-crash")
    import scheduler.security as security
    real_new_id = security.new_id
    monkey_calls = {"n": 0}

    def fake_new_id(prefix):
        if prefix == "op_":
            return "op-crash"
        return real_new_id(prefix)
    security.new_id = fake_new_id
    try:
        result = book(env, idempotency_key="b06")
    finally:
        security.new_id = real_new_id
    assert result["status"] == "pending"
    # Lease expiry is not proof of failure: the interval stays protected.
    assert store.active_protection("2026-10-06T00:00:00+00:00", "2026-10-07T00:00:00+00:00")
    recovered = service.reconcile_operation(result["operation_id"], provider=env.provider, now=NOW)
    assert recovered["status"] == "confirmed"
    assert len(env.provider.events) == 1


def test_stale_busy_at_submit_time_is_a_conflict(env):
    """B09: a cached offer is not permission to book."""
    env.provider.busy_extra.append((berlin(6, 10, "09:00"), berlin(6, 10, "09:30")))
    with pytest.raises(service.BookingError) as exc:
        book(env, idempotency_key="b09")
    assert exc.value.code == "slot_unavailable"


def test_failed_reschedule_keeps_the_original(env):
    """B11: a definitive provider failure leaves the original booking valid
    and releases the new protection."""
    created = book(env, idempotency_key="b11")
    booking = store.get_booking_by_reference(created["booking"]["reference"])
    env.provider.events.clear()  # event gone: update is definitively impossible
    with pytest.raises(service.BookingError):
        service.reschedule_booking(
            booking_id=booking["id"], actor="invitee", expected_revision=1,
            new_start_iso=berlin(6, 10, "14:00").isoformat(), new_duration_min=30,
            idempotency_key="b11-resched", provider=env.provider, now=NOW)
    current = store.get_booking(booking["id"])
    assert current["start_iso"] == booking["start_iso"]
    assert current["status"] == "confirmed"


def test_reschedule_excludes_only_its_own_event(env):
    """B12: the booking's own event is excluded; a sibling conflict still blocks."""
    created = book(env, start_iso=berlin(6, 10, "14:00").isoformat(), idempotency_key="b12")
    booking = store.get_booking_by_reference(created["booking"]["reference"])
    env.provider.busy_extra.append((berlin(6, 10, "14:15"), berlin(6, 10, "15:00")))
    with pytest.raises(service.BookingError) as exc:
        service.reschedule_booking(
            booking_id=booking["id"], actor="invitee", expected_revision=1,
            new_start_iso=berlin(6, 10, "14:15").isoformat(), new_duration_min=30,
            idempotency_key="b12-resched", provider=env.provider, now=NOW)
    assert exc.value.code == "slot_unavailable"
    moved = service.reschedule_booking(
        booking_id=booking["id"], actor="invitee", expected_revision=1,
        new_start_iso=berlin(6, 10, "10:00").isoformat(), new_duration_min=30,
        idempotency_key="b12-resched-2", provider=env.provider, now=NOW)
    assert moved["status"] == "rescheduled"
    assert moved["manage_token"]  # capabilities rotate on reschedule


def test_cancel_is_idempotent_and_suppresses_reminders(env):
    """B13/C08: explicit cancellation is idempotent; queued reminders die."""
    created = book(env, idempotency_key="b13")
    booking = store.get_booking_by_reference(created["booking"]["reference"])
    assert store.due_notifications("2026-10-06T00:00:00+00:00")
    first = service.cancel_booking(booking_id=booking["id"], actor="invitee",
                                   expected_revision=1, idempotency_key="b13-cancel",
                                   provider=env.provider, now=NOW)
    assert first["status"] == "canceled"
    second = service.cancel_booking(booking_id=booking["id"], actor="invitee",
                                    expected_revision=2, idempotency_key="b13-cancel-2",
                                    provider=env.provider, now=NOW)
    assert second["duplicate"] is True
    remaining = store.due_notifications("2026-10-06T00:00:00+00:00")
    assert [n for n in remaining if n["kind"] == "reminder"] == []


def test_stale_revision_is_rejected(env):
    """B14: a stale browser cannot overwrite a newer cancellation."""
    created = book(env, idempotency_key="b14")
    booking = store.get_booking_by_reference(created["booking"]["reference"])
    service.cancel_booking(booking_id=booking["id"], actor="invitee", expected_revision=1,
                           idempotency_key="b14-cancel", provider=env.provider, now=NOW)
    with pytest.raises(service.BookingError) as exc:
        service.reschedule_booking(
            booking_id=booking["id"], actor="invitee", expected_revision=1,
            new_start_iso=berlin(6, 10, "14:00").isoformat(), new_duration_min=30,
            idempotency_key="b14-resched", provider=env.provider, now=NOW)
    assert exc.value.code in ("stale_revision", "invalid_state")


def test_external_move_and_delete_reconcile(env):
    """B15: manual provider edits are reflected, never reverted or recreated."""
    created = book(env, idempotency_key="b15")
    booking = store.get_booking_by_reference(created["booking"]["reference"])
    event = env.provider.events[booking["provider_event_id"]]
    event["start"] = {"dateTime": berlin(6, 10, "15:00").isoformat()}
    event["end"] = {"dateTime": berlin(6, 10, "15:30").isoformat()}
    sync = service.periodic_sync(provider=env.provider, now=NOW)
    assert sync["updated"] == 1
    assert parse_iso(store.get_booking(booking["id"])["start_iso"]) == berlin(6, 10, "15:00")
    event["status"] = "cancelled"
    sync = service.periodic_sync(provider=env.provider, now=NOW)
    assert sync["canceled"] == 1
    assert store.get_booking(booking["id"])["status"] == "canceled"
    assert len(env.provider.events) == 1  # nothing recreated


def test_email_failure_keeps_the_booking_confirmed(env):
    """C07: the meeting stands; the message retries without another event."""
    created = book(env, idempotency_key="c07")
    booking = store.get_booking_by_reference(created["booking"]["reference"])
    env.email.fail_next = 100
    handled = service.process_due_notifications(env.email, now=NOW)
    assert handled["failed"] > 0
    assert store.get_booking(booking["id"])["status"] == "confirmed"
    assert len(env.provider.events) == 1
    env.email.fail_next = 0
    handled = service.process_due_notifications(env.email, now=NOW)
    assert handled["sent"] > 0


def test_answers_reach_the_calendar_event_and_the_host_notice(env):
    """Spec 9.2/12: the host reads the invitee's context in both the event
    description and the new-booking email, labeled as it was asked."""
    created = book(env, idempotency_key="qa-1",
                   answers={"talk-about": "Corporate training",
                            "discuss": "Upskilling the team"},
                   notes="")
    event = next(iter(env.provider.events.values()))
    description = event["description"]
    assert "What would you like to talk about?: Corporate training" in description
    assert "What would you like to discuss?: Upskilling the team" in description
    service.process_due_notifications(env.email, now=NOW)
    host_notice = next(m for m in env.email.outbox if m["to"] == "host@example.com")
    assert "What would you like to talk about?: Corporate training" in host_notice["text"]
    assert created["booking"]["reference"]


def test_invalid_management_token_reveals_nothing(env):
    """C10: guessing a token or reference exposes no booking."""
    capability, booking = service.resolve_management_token("nope-not-a-token")
    assert (capability, booking) == (None, None)
    created = book(env, idempotency_key="c10")
    capability, booking = service.resolve_management_token(created["manage_token"])
    assert booking["reference"] == created["booking"]["reference"]


def test_disabled_type_stops_new_bookings_but_keeps_receipts(env):
    """A03: existing booking-management links survive a disable."""
    created = book(env, idempotency_key="a03")
    et = store.get_event_type("dtc-30")
    et["visibility"] = "disabled"
    store.put_event_type(et)
    with pytest.raises(service.BookingError) as exc:
        book(env, idempotency_key="a03-second")
    assert exc.value.code == "type_disabled"
    assert store.get_booking_by_reference(created["booking"]["reference"])["status"] == "confirmed"
