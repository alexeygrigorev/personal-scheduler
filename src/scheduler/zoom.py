"""Zoom meetings port. The scheduler never holds Zoom OAuth itself: access
tokens arrive per-operation from Dapier (connection "zoom-meetings") and live
only in memory, exactly like the calendar port's.

Meeting lifecycle mirrors the booking lifecycle — create with the calendar
event, move with the reschedule, delete with the cancellation — and the join
URL is the one artifact that escapes this module (onto the calendar event and
the booking's conference record). Meetings carry no secrets and no agenda.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone


class ZoomError(Exception):
    """Base class: the caller degrades or fails closed, never retries blind."""


class ZoomAuthLost(ZoomError):
    """Zoom refused the token: fail closed, keep records, reconnect."""


class ZoomNotFound(ZoomError):
    """The meeting is already gone: deletes stay idempotent."""


class ZoomUnavailable(ZoomError):
    """Zoom could not be reached or failed; the outcome may be unknown."""


# Dapier connection reference and the granular Zoom scopes the scheduler
# needs. The token call fails closed when the connection carries less.
ZOOM_CONNECTION_ID = "zoom-meetings"
REQUIRED_ZOOM_SCOPES = [
    "meeting:write:meeting",   # create the booking's meeting
    "meeting:update:meeting",  # move it on reschedule
    "meeting:delete:meeting",  # remove it on cancellation
]


@dataclass
class Meeting:
    id: str
    join_url: str


def _zoom_time(start: datetime) -> str:
    """Zoom expects ISO-8601; UTC with a Z suffix avoids timezone fields."""
    utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    return utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ZoomPort:
    def create_meeting(self, *, topic: str, start: datetime, duration_min: int,
                       invitee_email: str = "") -> Meeting:
        raise NotImplementedError

    def update_meeting(self, meeting_id: str, *, start: datetime,
                       duration_min: int) -> None:
        raise NotImplementedError

    def delete_meeting(self, meeting_id: str) -> bool:
        raise NotImplementedError


class RestZoomMeetings(ZoomPort):
    """Zoom REST API v2 over raw HTTPS (no SDK dependency in the Lambda)."""

    API = "https://api.zoom.us/v2"

    def __init__(self, token_supplier, timeout: int = 10):
        self._token = token_supplier
        self._timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None,
                 params: dict | None = None) -> dict:
        url = f"{self.API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url, data=data, method=method,
            headers={"authorization": f"Bearer {self._token()}",
                     "content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as handle:
                raw = handle.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ZoomAuthLost(f"zoom refused authorization: {exc.code}") from exc
            if exc.code == 404:
                raise ZoomNotFound("zoom meeting not found") from exc
            raise ZoomUnavailable(f"zoom error {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ZoomUnavailable(f"zoom unreachable: {exc}") from exc

    def create_meeting(self, *, topic: str, start: datetime, duration_min: int,
                       invitee_email: str = "") -> Meeting:
        response = self._request("POST", "/users/me/meetings", {
            "topic": topic[:200],
            "type": 2,  # scheduled
            "start_time": _zoom_time(start),
            "duration": int(duration_min),
            "timezone": "UTC",
            "settings": {
                "join_before_host": True,
                "approval_type": 2,  # no registration: the join URL is the link
                "waiting_room": False,
            },
        })
        return Meeting(id=str(response.get("id", "")),
                       join_url=str(response.get("join_url", "")))

    def update_meeting(self, meeting_id: str, *, start: datetime,
                       duration_min: int) -> None:
        self._request("PATCH", f"/meetings/{urllib.parse.quote(str(meeting_id))}", {
            "start_time": _zoom_time(start),
            "duration": int(duration_min),
            "timezone": "UTC",
        })

    def delete_meeting(self, meeting_id: str) -> bool:
        try:
            self._request(
                "DELETE", f"/meetings/{urllib.parse.quote(str(meeting_id))}",
                params={"schedule_for_reminder": "false"})
            return True
        except ZoomNotFound:
            return False  # already gone: cancellation stays idempotent


class InMemoryZoomMeetings(ZoomPort):
    """Deterministic test double: creation is observable, moves and deletes
    are recorded, and failures are injectable per operation."""

    def __init__(self):
        self.meetings: dict[str, Meeting] = {}
        self.updates: list[tuple[str, str]] = []
        self.deletes: list[str] = []
        self.fail_create: ZoomError | None = None
        self.fail_update: ZoomError | None = None
        self.fail_delete: ZoomError | None = None
        self.counter = 0

    def create_meeting(self, *, topic: str, start: datetime, duration_min: int,
                       invitee_email: str = "") -> Meeting:
        if self.fail_create:
            raise self.fail_create
        self.counter += 1
        meeting = Meeting(id=f"zm-{self.counter}", join_url=f"https://zoom.test/j/{self.counter}")
        self.meetings[meeting.id] = meeting
        return meeting

    def update_meeting(self, meeting_id: str, *, start: datetime,
                       duration_min: int) -> None:
        if self.fail_update:
            raise self.fail_update
        self.updates.append((meeting_id, _zoom_time(start)))

    def delete_meeting(self, meeting_id: str) -> bool:
        if self.fail_delete:
            raise self.fail_delete
        self.deletes.append(meeting_id)
        return self.meetings.pop(meeting_id, None) is not None
