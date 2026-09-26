"""The Zoom port: raw-HTTPS request shapes, per-operation token use, and the
failure taxonomy the booking lifecycle relies on."""

import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from scheduler import zoom as zoom_mod
from scheduler.zoom import (InMemoryZoomMeetings, Meeting, RestZoomMeetings,
                            ZoomAuthLost, ZoomError, ZoomNotFound,
                            ZoomUnavailable, _zoom_time)

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)

REAL_URLOPEN = urllib.request.urlopen


def _respond(status=200, payload=None, raw=b"", calls=None):
    def fake_urlopen(request, timeout):
        if calls is not None:
            calls.append(request)
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "err", {},
                                         io.BytesIO(json.dumps(payload or {}).encode()))
        body = raw or json.dumps(payload or {}).encode()
        return io.BytesIO(body)
    return fake_urlopen


def test_zoom_time_is_utc_suffix_form():
    assert _zoom_time(NOW) == "2026-10-06T09:00:00Z"
    naive = datetime(2026, 10, 6, 9, 0)
    assert _zoom_time(naive) == "2026-10-06T09:00:00Z"


def test_create_sends_scheduled_meeting_and_uses_the_token_supplier():
    issued = []
    tokens = lambda: f"tok-{len(issued.append(1) or issued)}"
    calls = []
    port = RestZoomMeetings(tokens)
    zoom_mod.urllib.request.urlopen = _respond(
        payload={"id": 987654, "join_url": "https://zoom.test/j/987"}, calls=calls)
    try:
        meeting = port.create_meeting(topic="Chat: Ada", start=NOW, duration_min=30,
                                      invitee_email="ada@example.com")
    finally:
        urllib.request.urlopen = REAL_URLOPEN
    assert meeting == Meeting(id="987654", join_url="https://zoom.test/j/987")
    request = calls[0]
    assert request.full_url == "https://api.zoom.us/v2/users/me/meetings"
    assert request.get_method() == "POST"
    body = json.loads(request.data.decode())
    assert body["type"] == 2
    assert body["start_time"] == "2026-10-06T09:00:00Z"
    assert body["duration"] == 30
    assert body["topic"] == "Chat: Ada"
    # One fresh token per operation, carried as a bearer — never stored.
    assert request.get_header("Authorization") == "Bearer tok-1"


def test_failure_taxonomy_maps_http_codes():
    port = RestZoomMeetings(lambda: "tok")
    for status, exc_type in ((401, ZoomAuthLost), (403, ZoomAuthLost),
                             (404, ZoomNotFound), (429, ZoomUnavailable),
                             (500, ZoomUnavailable)):
        zoom_mod.urllib.request.urlopen = _respond(status)
        try:
            with pytest.raises(exc_type):
                port._request("GET", "/meetings/1")
        finally:
            urllib.request.urlopen = REAL_URLOPEN


def test_update_and_delete_target_the_meeting_and_delete_tolerates_gone():
    port = RestZoomMeetings(lambda: "tok")
    calls = []
    seen_statuses = iter((200, 204, 404))
    zoom_mod.urllib.request.urlopen = _respond(calls=calls)
    status_holder = {"value": next(seen_statuses)}

    def fake(request, timeout):
        calls.append(request)
        status = status_holder["value"]
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(b"{}"))
        return io.BytesIO(b"")

    zoom_mod.urllib.request.urlopen = fake
    try:
        port.update_meeting("42", start=NOW, duration_min=60)
        assert calls[-1].get_method() == "PATCH"
        assert calls[-1].full_url == "https://api.zoom.us/v2/meetings/42"
        assert json.loads(calls[-1].data.decode())["start_time"] == "2026-10-06T09:00:00Z"

        status_holder["value"] = next(seen_statuses)
        assert port.delete_meeting("42") is True
        assert calls[-1].get_method() == "DELETE"
        assert calls[-1].full_url == ("https://api.zoom.us/v2/meetings/42"
                                      "?schedule_for_reminder=false")

        status_holder["value"] = next(seen_statuses)
        assert port.delete_meeting("42") is False  # already gone: idempotent
    finally:
        urllib.request.urlopen = REAL_URLOPEN


def test_memory_double_records_moves_and_deletes():
    zoom = InMemoryZoomMeetings()
    meeting = zoom.create_meeting(topic="t", start=NOW, duration_min=30)
    assert meeting.id == "zm-1"
    zoom.update_meeting(meeting.id, start=NOW, duration_min=45)
    assert zoom.updates == [("zm-1", "2026-10-06T09:00:00Z")]
    assert zoom.delete_meeting(meeting.id) is True
    assert zoom.delete_meeting(meeting.id) is False
    zoom.fail_create = ZoomUnavailable("down")
    with pytest.raises(ZoomError):
        zoom.create_meeting(topic="t", start=NOW, duration_min=30)
