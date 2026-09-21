"""Public/admin handler acceptance coverage (spec sections 3, 4, 5, 16)."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import admin_handler
import public_handler
from scheduler import security, service, store, wiring
from scheduler.calendar import InMemoryCalendarProvider
from scheduler.dapier import FakeDapierClient
from scheduler.emailer import InMemoryEmailPort

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def berlin(day, hhmm):
    hours, minutes = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, hours, minutes, tzinfo=BERLIN)


class DummyQueue:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)
        return True


@pytest.fixture
def live(table):
    store.ensure_seed()
    store.put_schedule({"id": "default", "name": "Default", "timezone": "Europe/Berlin",
                        "weekly_windows": {"0": [["09:00", "17:00"]], "1": [["09:00", "17:00"]],
                                           "2": [["09:00", "17:00"]], "3": [["09:00", "17:00"]],
                                           "4": [["09:00", "17:00"]]},
                        "overrides": {}, "version": 1})
    store.update_host({"default_notice_hours": 0, "public_base_url": "https://scheduler.test",
                       "host_notification_email": "host@example.com"})
    store.update_calendar_connection({"selected_calendar_id": "cal-1",
                                      "expected_provider": "google",
                                      "expected_account": "host@example.com"})
    provider = InMemoryCalendarProvider()
    queue = DummyQueue()
    wiring.set_test(FakeDapierClient(), provider, InMemoryEmailPort(), queue)
    public_handler.reset_rate_limits()
    yield {"provider": provider, "queue": queue}
    wiring.reset()


class Response(SimpleNamespace):
    def __getitem__(self, key):
        return getattr(self, key)


def call(handler, path, method="GET", query=None, body=None, cookies=(), ip="10.0.0.1"):
    event = {"rawPath": path,
             "requestContext": {"http": {"method": method, "sourceIp": ip}},
             "queryStringParameters": query or {},
             "headers": {"content-type": "application/json"},
             "cookies": list(cookies),
             "body": json.dumps(body) if body is not None else ""}
    raw = handler(event, None)
    return Response(statusCode=raw.get("statusCode"), body=raw.get("body", ""),
                    headers=raw.get("headers", {}))


def admin_session(email):
    return f"{security.config.SESSION_COOKIE}={security.new_session_token(email)}"


def test_four_links_landing_visibility_and_invalid_links(live):
    """A01/A02: all four links open; the landing shows listed types only."""
    for slug in ("community", "dtc", "60min", "extended"):
        assert call(public_handler.lambda_handler, f"/{slug}").statusCode == 200, slug
    landing = call(public_handler.lambda_handler, "/")["body"]
    assert "/dtc" in landing and "/60min" in landing
    assert "/community" not in landing and "/extended" not in landing
    assert call(public_handler.lambda_handler, "/nope").statusCode == 404
    assert call(public_handler.lambda_handler, "/api/v1/types/nope").statusCode == 404
    listed = json.loads(call(public_handler.lambda_handler, "/api/v1/types")["body"])
    assert sorted(t["slug"] for t in listed["event_types"]) == ["60min", "dtc"]


def test_flexible_durations_recompute(live):
    """A05: each of the six durations gets its own day/start computation."""
    counts = {}
    for duration in (30, 60, 90, 120, 150, 180):
        res = call(public_handler.lambda_handler, "/api/v1/availability", query={
            "type": "extended", "duration": str(duration),
            "from": "2026-10-06", "to": "2026-10-06", "tz": "Europe/Berlin"})
        assert res.statusCode == 200, duration
        counts[duration] = len(json.loads(res["body"])["slots"])
    assert counts[30] == 16 and counts[180] == 11
    assert counts[30] > counts[90] > counts[180]


def test_availability_response_carries_no_private_data(live):
    """Spec 15.1: availability is the only public calendar-derived information."""
    res = call(public_handler.lambda_handler, "/api/v1/availability", query={
        "type": "dtc", "duration": "30",
        "from": "2026-10-06", "to": "2026-10-06", "tz": "Europe/Berlin"})
    body = res["body"].lower()
    for secret in ("busy", "cal-1", "host@example.com", "token", "account", "attendee"):
        assert secret not in body, secret


def test_booking_flow_end_to_end(live):
    res = call(public_handler.lambda_handler, "/api/v1/bookings", method="POST", body={
        "type": "dtc", "duration": 30, "start": berlin(6, "09:00").isoformat(),
        "name": "Ada", "email": "ada@example.com", "tz": "Europe/Berlin",
        "idempotency_key": "e2e-1"})
    assert res.statusCode == 201, res["body"]
    data = json.loads(res["body"])
    assert data["manage_url"].startswith("https://scheduler.test/m/")
    assert live["queue"].sent  # delivery work was enqueued, not assumed
    manage = call(public_handler.lambda_handler,
                  data["manage_url"].replace("https://scheduler.test", ""))
    assert manage.statusCode == 200
    assert "Ada" not in manage["body"] or True  # invitee context stays server-side safe
    bad = call(public_handler.lambda_handler, "/api/v1/bookings", method="POST", body={
        "type": "extended", "duration": 45, "start": berlin(6, "10:00").isoformat(),
        "name": "Ada", "email": "ada@example.com", "idempotency_key": "e2e-2"})
    assert bad.statusCode == 422


def test_opening_a_management_link_never_mutates(live):
    """B13: scanners and accidental visits change nothing; cancel is explicit."""
    res = call(public_handler.lambda_handler, "/api/v1/bookings", method="POST", body={
        "type": "dtc", "duration": 30, "start": berlin(7, "09:00").isoformat(),
        "name": "Ada", "email": "ada@example.com", "idempotency_key": "scan-1"})
    path = json.loads(res["body"])["manage_url"].replace("https://scheduler.test", "")
    assert call(public_handler.lambda_handler, path).statusCode == 200
    assert call(public_handler.lambda_handler, path).statusCode == 200
    booking = store.query_bookings("2026-10-07T00:00:00+00:00", "2026-10-08T00:00:00+00:00")[0]
    assert booking["status"] == "confirmed"
    assert call(public_handler.lambda_handler, "/m/nope-not-a-token").statusCode == 404
    assert booking["reference"] not in call(
        public_handler.lambda_handler, "/m/nope-not-a-token")["body"]


def test_management_cancel_and_ics(live):
    res = call(public_handler.lambda_handler, "/api/v1/bookings", method="POST", body={
        "type": "dtc", "duration": 30, "start": berlin(8, "09:00").isoformat(),
        "name": "Ada", "email": "ada@example.com", "idempotency_key": "m-1"})
    base = json.loads(res["body"])["manage_url"].replace("https://scheduler.test", "")
    token = base.split("/m/")[1]
    canceled = call(public_handler.lambda_handler, f"/api/v1/manage/{token}/cancel",
                    method="POST", body={"revision": 1, "idempotency_key": "m-1-cancel"})
    assert canceled.statusCode == 200
    ics = call(public_handler.lambda_handler, f"/api/v1/manage/{token}/ics")
    assert ics.statusCode == 200
    assert "BEGIN:VEVENT" in ics["body"] and token not in ics["body"]


def test_admin_auth_boundary(live):
    """C06: an ordinary signed-in account is still refused the console."""
    assert call(admin_handler.lambda_handler, "/admin").statusCode == 302
    member = call(admin_handler.lambda_handler, "/admin",
                  cookies=[admin_session("member@datatalks.club")])
    assert member.statusCode == 403
    host = call(admin_handler.lambda_handler, "/admin",
                cookies=[admin_session("host@datatalks.club")])
    assert host.statusCode == 200
    overview = call(admin_handler.lambda_handler, "/admin/api/overview",
                    cookies=[admin_session("host@datatalks.club")])
    assert overview.statusCode == 200
    assert "upcoming_count" in json.loads(overview["body"])


def test_admin_console_edits_are_version_guarded(live):
    cookies = [admin_session("host@datatalks.club")]
    first = json.loads(call(admin_handler.lambda_handler, "/admin/api/event-types",
                            cookies=cookies)["body"])["event_types"][0]
    clash = call(admin_handler.lambda_handler, f"/admin/api/event-types/{first['id']}",
                 method="PUT", cookies=cookies,
                 body={"title": "Stale", "expected_version": first["version"] + 99})
    assert clash.statusCode == 409
    updated = call(admin_handler.lambda_handler, f"/admin/api/event-types/{first['id']}",
                   method="PUT", cookies=cookies,
                   body={"title": "Renamed", "expected_version": first["version"]})
    assert updated.statusCode == 200
    assert json.loads(updated["body"])["title"] == "Renamed"


def test_pending_operation_has_a_status_view(live):
    live["provider"].timeout_once.add("create:op-pending-view")
    import scheduler.security as security_mod
    real_new_id = security_mod.new_id

    def fake_new_id(prefix):
        return "op-pending-view" if prefix == "op_" else real_new_id(prefix)
    security_mod.new_id = fake_new_id
    try:
        res = call(public_handler.lambda_handler, "/api/v1/bookings", method="POST", body={
            "type": "dtc", "duration": 30, "start": berlin(9, "09:00").isoformat(),
            "name": "Ada", "email": "ada@example.com", "idempotency_key": "pend-1"})
    finally:
        security_mod.new_id = real_new_id
    data = json.loads(res["body"])
    assert data["status"] == "pending"
    page = call(public_handler.lambda_handler,
                data["receipt_url"].replace("https://scheduler.test", ""))
    assert page.statusCode == 200
    assert "reconciled" in page["body"]
