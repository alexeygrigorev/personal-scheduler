"""Calendar provider port (spec section 9).

One explicit writable calendar is used for conflict checking and writing.
The provider event is authoritative for its own external state after manual
edits; this application is authoritative for scheduling policy and intent.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone


class CalendarError(Exception):
    pass


class UnknownOutcome(CalendarError):
    """The write may have succeeded remotely; the caller must reconcile by
    correlation id instead of retrying blindly."""


class DefinitiveFailure(CalendarError):
    """The write definitely did not happen; releasing protection is safe."""


class AuthLost(CalendarError):
    """Calendar authorization is gone: fail closed, keep records, reconnect."""


class IncompleteResult(CalendarError):
    """Busy data came back partial or errored: offer nothing as free."""


class ConflictDetected(CalendarError):
    """Fresh pre-write check found the slot taken after all."""


def compliant_event_id(correlation: str) -> str:
    """Google accepts caller-supplied event ids over a restricted alphabet;
    derive a stable one from the booking operation correlation."""
    digest = hashlib.sha256(correlation.encode()).hexdigest()
    return f"s{digest[:31]}"


def _span(value) -> datetime:
    """Accept a datetime or a Google-shaped {"dateTime": ...} mapping."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value["dateTime"])


class CalendarProvider:
    """Retry-safe, correlation-bound event lifecycle."""

    def check_writable(self, calendar_id: str):
        raise NotImplementedError

    def list_calendars(self):
        """Calendar entries for the connected account, writable ones only:
        [{id, summary, access_role, primary}]."""
        raise NotImplementedError

    def query_busy(self, calendar_id: str, start: datetime, end: datetime):
        """Return (busy_intervals, incomplete). Busy intervals are UTC
        (start, end) tuples. incomplete=True fails the range closed."""
        raise NotImplementedError

    def get_event(self, calendar_id: str, event_id: str):
        raise NotImplementedError

    def create_event(self, calendar_id: str, event: dict, correlation: str):
        raise NotImplementedError

    def update_event(self, calendar_id: str, event_id: str, patch: dict,
                     expected_version: str = ""):
        raise NotImplementedError

    def delete_event(self, calendar_id: str, event_id: str):
        raise NotImplementedError


class InMemoryCalendarProvider(CalendarProvider):
    """Deterministic test double with the production semantics: idempotent
    creation by correlation, unknown-outcome injection, free/cancelled events
    that never block, and recurring instances."""

    def __init__(self):
        self.events: dict[str, dict] = {}
        self.by_correlation: dict[str, str] = {}
        self.writable = True
        self.auth_lost = False
        self.calendars = [{"id": "cal-1", "summary": "Bookings",
                           "access_role": "owner", "primary": True}]
        self.incomplete = False
        self.timeout_once: set[str] = set()
        self.busy_extra: list = []

    def _guard(self):
        if self.auth_lost:
            raise AuthLost("calendar authorization lost")
        if self.incomplete:
            raise IncompleteResult("busy result partial")

    def check_writable(self, calendar_id: str):
        self._guard()
        if not self.writable:
            raise DefinitiveFailure("calendar is read-only")

    def list_calendars(self):
        self._guard()
        return [dict(c) for c in self.calendars]

    def _blocks(self, event: dict) -> bool:
        if event.get("status") in ("cancelled", "deleted"):
            return False
        if event.get("transparency") == "transparent":  # explicitly free
            return False
        return True

    def _occurrences(self, event: dict, start: datetime, end: datetime) -> list:
        out = []
        for instance in event.get("instances", [None]):
            if instance is None:
                s, e = _span(event["start"]), _span(event["end"])
            else:
                if instance.get("status") == "cancelled":
                    continue  # canceled instances must not block
                s, e = _span(instance["start"]), _span(instance["end"])
            if s < end and start < e:
                out.append((s, e))
        return out

    def query_busy(self, calendar_id: str, start: datetime, end: datetime):
        self._guard()
        busy = [iv for event in self.events.values() if self._blocks(event)
                for iv in self._occurrences(event, start, end)]
        busy.extend([iv for iv in self.busy_extra if iv[0] < end and start < iv[1]])
        return busy, False

    def get_event(self, calendar_id: str, event_id: str):
        self._guard()
        event = self.by_correlation.get(event_id, event_id)
        real = self.events.get(event)
        if real is None or real.get("status") in ("cancelled", "deleted"):
            return None
        return dict(real)

    def create_event(self, calendar_id: str, event: dict, correlation: str):
        self._guard()
        if correlation in self.by_correlation:
            return dict(self.events[self.by_correlation[correlation]])
        if correlation in self.timeout_once:
            # Simulate a timeout AFTER the remote write landed: the record
            # exists, but the caller only learns this via reconciliation.
            event_id = compliant_event_id(correlation)
            stored = dict(event, id=event_id, status="confirmed", version="1")
            self.events[event_id] = stored
            self.by_correlation[correlation] = event_id
            self.timeout_once.discard(correlation)
            raise UnknownOutcome("provider timeout after write")
        event_id = compliant_event_id(correlation)
        stored = dict(event, id=event_id, status="confirmed", version="1")
        self.events[event_id] = stored
        self.by_correlation[correlation] = event_id
        return dict(stored)

    def update_event(self, calendar_id: str, event_id: str, patch: dict,
                     expected_version: str = ""):
        self._guard()
        stored = self.events.get(event_id)
        if stored is None or stored.get("status") in ("cancelled", "deleted"):
            raise DefinitiveFailure("event no longer exists")
        if expected_version and stored.get("version") != expected_version:
            raise ConflictDetected("stale event version")
        stored.update({k: v for k, v in patch.items() if k != "id"})
        stored["version"] = str(int(stored.get("version", "1")) + 1)
        return dict(stored)

    def delete_event(self, calendar_id: str, event_id: str):
        self._guard()
        stored = self.events.get(event_id)
        if stored is None:
            return False  # already gone: cancellation stays idempotent
        stored["status"] = "cancelled"
        return True


def _google_time(value: dict, default_tz: str) -> datetime:
    if "dateTime" in value:
        dt = datetime.fromisoformat(value["dateTime"])
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    # All-day: exclusive end, interpreted in the event/calendar timezone.
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(value.get("timeZone", default_tz or "UTC"))
    day = datetime.fromisoformat(value["date"]).date()
    return datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)


class RestGoogleCalendarProvider(CalendarProvider):
    """Google Calendar over raw HTTPS (no SDK dependency in the Lambda).
    Access tokens arrive per-operation from Dapier and live only in memory."""

    API = "https://www.googleapis.com/calendar/v3"

    def __init__(self, token_supplier, timeout: int = 10):
        self._token = token_supplier
        self._timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None,
                 params: dict | None = None) -> dict:
        token, _meta = self._token()
        url = f"{self.API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url, data=data, method=method,
            headers={"authorization": f"Bearer {token}",
                     "content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as handle:
                raw = handle.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthLost(f"calendar refused authorization: {exc.code}") from exc
            if exc.code == 404:
                raise DefinitiveFailure("calendar or event not found") from exc
            if exc.code == 409:
                raise ConflictDetected("conflicting event state") from exc
            if 500 <= exc.code < 600:
                raise UnknownOutcome(f"provider {exc.code}: outcome unknown") from exc
            raise DefinitiveFailure(f"provider refused: {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise UnknownOutcome(f"provider unreachable: {exc}") from exc

    def check_writable(self, calendar_id: str):
        entry = self._request("GET", f"/users/me/calendarList/{urllib.parse.quote(calendar_id)}")
        if entry.get("accessRole") not in ("owner", "writer"):
            raise DefinitiveFailure("selected calendar is not writable")

    def list_calendars(self):
        response = self._request("GET", "/users/me/calendarList",
                                 params={"minAccessRole": "writer",
                                         "showHidden": "false", "maxResults": "250"})
        entries = []
        for item in response.get("items") or []:
            if item.get("deleted"):
                continue
            entries.append({"id": item["id"],
                            "summary": item.get("summary") or item["id"],
                            "access_role": item.get("accessRole", "reader"),
                            "primary": bool(item.get("primary"))})
        return entries

    def query_busy(self, calendar_id: str, start: datetime, end: datetime):
        try:
            response = self._request("POST", "/freeBusy", {
                "timeMin": start.isoformat(), "timeMax": end.isoformat(),
                "items": [{"id": calendar_id}]})
        except CalendarError:
            raise
        calendars = (response.get("calendars") or {}).get(calendar_id, {})
        if calendars.get("errors"):
            raise IncompleteResult(f"per-calendar errors: {calendars['errors']}")
        busy = []
        for span in calendars.get("busy", []):
            busy.append((datetime.fromisoformat(span["start"]),
                         datetime.fromisoformat(span["end"])))
        return busy, False

    def get_event(self, calendar_id: str, event_id: str):
        try:
            event = self._request(
                "GET", f"/calendars/{urllib.parse.quote(calendar_id)}/events/{event_id}")
        except DefinitiveFailure:
            return None
        if event.get("status") == "cancelled":
            return None
        return event

    def create_event(self, calendar_id: str, event: dict, correlation: str):
        body = dict(event, id=compliant_event_id(correlation))
        try:
            return self._request(
                "POST", f"/calendars/{urllib.parse.quote(calendar_id)}/events",
                body, params={"sendUpdates": "all"})
        except ConflictDetected:
            # The correlation id already exists: a retried create after an
            # unknown outcome finds its original event instead of duplicating.
            existing = self.get_event(calendar_id, body["id"])
            if existing is not None:
                return existing
            raise

    def update_event(self, calendar_id: str, event_id: str, patch: dict,
                     expected_version: str = ""):
        return self._request(
            "PATCH", f"/calendars/{urllib.parse.quote(calendar_id)}/events/{event_id}",
            patch, params={"sendUpdates": "all"})

    def delete_event(self, calendar_id: str, event_id: str):
        try:
            self._request(
                "DELETE", f"/calendars/{urllib.parse.quote(calendar_id)}/events/{event_id}",
                params={"sendUpdates": "all"})
            return True
        except DefinitiveFailure:
            return False
