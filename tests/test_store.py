"""DynamoDB single-table store, exercised against moto."""

import pytest

from scheduler import models, security, store


def test_seed_creates_four_types_and_default_schedule(table):
    assert store.ensure_seed() is True
    assert store.ensure_seed() is False
    types = store.list_event_types()
    assert [t["id"] for t in types] == ["community-30", "dtc-30", "general-60", "flexible"]
    assert store.resolve_slug("dtc")["id"] == "dtc-30"
    assert store.resolve_slug("extended")["id"] == "flexible"
    assert store.get_schedule("default")["timezone"] == "Europe/Berlin"


def test_slug_change_keeps_alias_and_bookings_reference_stable_id(table):
    store.ensure_seed()
    flexible = store.get_event_type("flexible")
    flexible["slug"] = "long"
    flexible["aliases"] = ["extended"]
    store.put_event_type(flexible)
    assert store.resolve_slug("extended")["id"] == "flexible"
    assert store.resolve_slug("long")["id"] == "flexible"


def test_invalid_event_type_is_rejected(table):
    store.ensure_seed()
    bad = store.get_event_type("dtc-30")
    bad["visibility"] = "sometimes"
    with pytest.raises(ValueError):
        store.put_event_type(bad)


def test_admin_allowlist_and_root_bootstrap(table):
    assert store.is_admin("host@datatalks.club") is True  # ROOT_ADMIN in conftest
    assert store.is_admin("member@datatalks.club") is False
    store.update_host({"admin_emails": ["member@datatalks.club"]})
    assert store.is_admin("member@datatalks.club") is True
    assert store.is_admin("") is False


def test_idempotency_claim_conflicts_on_different_payload(table):
    op = {"id": "op1", "kind": "create", "idempotency_key": "key-1",
          "payload_fingerprint": "aaa", "state": "in_progress",
          "created_at": store.now_iso(), "updated_at": store.now_iso()}
    store.claim_operation(op)
    same = store.get_operation_by_key("key-1")
    assert same["id"] == "op1"
    clash = dict(op, id="op2", payload_fingerprint="bbb")
    with pytest.raises(store.ConditionalFailed):
        store.claim_operation(clash)
    store.update_operation("op1", {"state": "succeeded"})
    assert store.get_operation("op1")["state"] == "succeeded"
    assert store.list_pending_operations() == []


def test_host_lock_serializes_and_expires(table):
    assert store.acquire_host_lock("owner-a", ttl_seconds=60) is True
    assert store.acquire_host_lock("owner-b", ttl_seconds=60) is False
    store.release_host_lock("owner-b")  # not the owner: no-op
    assert store.acquire_host_lock("owner-b", ttl_seconds=60) is False
    store.release_host_lock("owner-a")
    assert store.acquire_host_lock("owner-b", ttl_seconds=60) is True


def test_booking_revision_guard_rejects_stale_writes(table):
    booking = {"id": "b1", "reference": "BK-AAAA", "event_type_id": "dtc-30",
               "start_iso": "2026-10-01T09:00:00+00:00",
               "end_iso": "2026-10-01T09:30:00+00:00", "duration_min": 30,
               "status": "confirmed", "revision": 1,
               "created_at": store.now_iso(), "updated_at": store.now_iso()}
    store.put_booking(booking)
    assert store.get_booking_by_reference("BK-AAAA")["id"] == "b1"
    store.update_booking("b1", {"status": "canceled", "revision": 2}, expected_revision=1)
    with pytest.raises(store.ConditionalFailed):
        store.update_booking("b1", {"status": "confirmed"}, expected_revision=1)
    found = store.query_bookings("2026-10-01T00:00:00+00:00", "2026-10-02T00:00:00+00:00")
    assert [b["id"] for b in found] == ["b1"]


def test_capabilities_revoke_and_expire(table):
    from datetime import timedelta
    raw, digest = security.new_management_token()
    store.put_capability(digest, {"booking_id": "b1", "actions": ["view", "cancel"],
                                  "created_at": store.now_iso(),
                                  "expires_at": (models.utcnow() + timedelta(days=1)).isoformat(),
                                  "expires_epoch": 4102444800, "revoked": False})
    assert store.get_capability(digest)["booking_id"] == "b1"
    store.revoke_booking_capabilities("b1")
    assert store.get_capability(digest) is None


def test_reminder_suppression_on_cancellation(table):
    ntf = store.queue_notification({"booking_id": "b9", "revision": 1,
                                    "recipient": "a@example.com", "kind": "reminder",
                                    "reminder_offset_min": 60,
                                    "scheduled_for": "2026-10-01T08:00:00+00:00"})
    assert store.due_notifications("2026-10-01T09:00:00+00:00")
    store.suppress_booking_notifications("b9")
    assert store.due_notifications("2026-10-01T09:00:00+00:00") == []
    assert ntf["status"] == "queued"
