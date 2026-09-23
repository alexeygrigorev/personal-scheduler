"""Booking lifecycle (spec sections 7, 8): durable reservations, host-level
concurrency protection, idempotent operations, pending-operation recovery,
and secure invitee cancel/reschedule flows.

Locks serialize reservation creation only. Reservations themselves are the
cross-request protection: once a protected interval is durable, every later
request — in this or any other container — sees it and backs off.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import availability as av
from . import dapier as dapier_mod
from . import emailer
from . import models
from . import security, store
from .calendar import (AuthLost, CalendarError, ConflictDetected, DefinitiveFailure,
                       IncompleteResult, UnknownOutcome)
from .models import (EventType, canonical_payload, parse_iso, utcnow, validate_duration,
                     validate_invitee)

OPERATION_LEASE_SECONDS = 120
LOCK_TTL_SECONDS = 60
REQUIRED_SCOPES = ["calendar.freebusy", "calendar.events.owned"]
REMINDER_OFFSETS_MIN = (24 * 60, 60)


class BookingError(Exception):
    def __init__(self, code, message, status=400, extra=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra or {}


def _now(now) -> datetime:
    return now or utcnow()


def _require_wiring(provider):
    """Fail closed: without a bound provider no slot is offered or confirmed."""
    if provider is None:
        raise BookingError("connection_unavailable",
                           "Calendar access is temporarily unavailable. Try again shortly.",
                           status=503)


def _host_day_bounds(start: datetime, end: datetime, tzname: str):
    tz = ZoneInfo(tzname)
    day = start.astimezone(tz).date()
    lo = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)
    return lo.isoformat(), (lo + timedelta(days=2)).isoformat()


def _load_event_type(event_type_id: str) -> EventType:
    item = store.get_event_type(event_type_id)
    if item is None:
        raise BookingError("unknown_type", "Unknown meeting type.", status=404)
    return EventType.from_item(item)


def _policy_host() -> dict:
    return store.get_host()


def _verify_calendar_access(dapier_client) -> tuple[dict, object]:
    """Obtain usable provider access through Dapier and bind it to the
    expected account and calendar. Wrong-account answers are fatal; outages
    and lost authorization pause new confirmations instead of guessing."""
    connection = store.get_calendar_connection()
    if not connection.get("selected_calendar_id"):
        raise BookingError("not_configured", "Calendar is not connected yet.", status=503)
    try:
        access = dapier_client.get_access(connection["dapier_connection_ref"], REQUIRED_SCOPES)
    except (dapier_mod.GrantDenied, dapier_mod.InsufficientScope,
            dapier_mod.DapierUnavailable, dapier_mod.DapierNotConfigured) as exc:
        raise BookingError("connection_unavailable",
                           "Calendar access is temporarily unavailable. Try again shortly.",
                           status=503) from exc
    expected = connection.get("expected_account", "")
    if expected and access.account != expected:
        raise BookingError("connection_unavailable",
                           "Calendar access is temporarily unavailable. Try again shortly.",
                           status=503)
    if access.provider != connection.get("expected_provider", access.provider):
        raise BookingError("connection_unavailable",
                           "Calendar access is temporarily unavailable. Try again shortly.",
                           status=503)
    return connection, access


def _fresh_busy(provider, calendar_id: str, start: datetime, end: datetime):
    try:
        busy, incomplete = provider.query_busy(calendar_id, start, end)
    except (AuthLost, IncompleteResult) as exc:
        raise BookingError("connection_unavailable",
                           "Calendar access is temporarily unavailable. Try again shortly.",
                           status=503) from exc
    if incomplete:
        raise BookingError("connection_unavailable",
                           "Calendar access is temporarily unavailable. Try again shortly.",
                           status=503)
    return [(b if isinstance(b, datetime) else parse_iso(b),
             e if isinstance(e, datetime) else parse_iso(e)) for b, e in busy]


def _slot_check(*, event_type: EventType, host: dict, schedule: dict, duration_min: int,
                start: datetime, busy, local_protected, blocks,
                existing_bookings, exclude_booking_id="",
                own_event_interval=None, now=None) -> None:
    """A cached offer is never permission to book: re-derive the day's offers
    from current policy and confirm the exact start is among them."""
    at = _now(now)
    end = start + timedelta(minutes=duration_min)
    day = start.astimezone(ZoneInfo(schedule.get("timezone", "Europe/Berlin"))).date()
    result = av.generate_slots(schedule=schedule, event_type=event_type.to_item(), host=host,
                               duration_min=duration_min, busy=busy,
                               local_protected=local_protected, blocks=blocks,
                               existing_bookings=existing_bookings, now_utc=at,
                               exclude_booking_id=exclude_booking_id,
                               own_event_interval=own_event_interval,
                               from_date=day, to_date=day)
    if all(offer.start != start for offer in result["slots"]):
        reasons = av.why_unavailable(
            moment=start, duration_min=duration_min, schedule=schedule,
            event_type=event_type.to_item(), host=host, busy=busy,
            local_protected=local_protected, blocks=blocks,
            existing_bookings=existing_bookings, now_utc=at)
        if "notice" in reasons or "horizon" in reasons:
            raise BookingError("policy_violation", "That time is outside the booking policy.",
                               status=422)
        raise BookingError("slot_unavailable", "That time was just taken. Pick another.",
                           status=409, extra={"reasons": reasons})


def _acquire_lock_or_unavailable(owner: str):
    for _ in range(20):
        if store.acquire_host_lock(owner, LOCK_TTL_SECONDS):
            return
        time.sleep(0.1)
    raise BookingError("service_unavailable", "The scheduler is busy. Try again shortly.",
                       status=503)


def _protection_window(event_type: EventType, host: dict, schedule: dict,
                       start: datetime, duration_min: int):
    policy = av.resolve_policy(event_type.to_item(), host)
    end = start + timedelta(minutes=duration_min)
    return (start - timedelta(minutes=policy.pre_buffer_min),
            end + timedelta(minutes=policy.post_buffer_min),
            end, policy)


def _existing_for_limits(window_lo: str, window_hi: str) -> list[dict]:
    bookings = store.query_bookings(window_lo, window_hi)
    for reservation in store.list_active_reservations():
        if reservation.get("state") != "active":
            continue
        bookings.append({"id": reservation.get("id", ""), "event_type_id": reservation.get("event_type_id", ""),
                         "status": "pending_confirmation", "duration_min": reservation.get("duration_min", 0),
                         "start_iso": reservation.get("protected_start", window_lo)})
    return bookings


def _provider_event_payload(*, event_type: EventType, invitee_name: str, invitee_email: str,
                            notes: str, answers: dict | None, start: datetime, end: datetime,
                            schedule_tz: str, location_link: str = "") -> dict:
    location = event_type.location_text
    if event_type.location_mode == "fixed_url":
        location = event_type.location_text
    elif event_type.location_mode == "provided_later":
        location = "Joining details will be provided separately."
    body: dict = {
        "summary": f"{event_type.title}: {invitee_name}",
        "description": models.format_answers(event_type.questions, answers or {}, notes)[:2000],
        "start": {"dateTime": start.isoformat(), "timeZone": schedule_tz},
        "end": {"dateTime": end.isoformat(), "timeZone": schedule_tz},
        "attendees": [{"email": invitee_email}],
        "transparency": "opaque",
        "guestsCanModify": False,
        "status": "confirmed",
    }
    if location:
        body["location"] = location[:500]
    if location_link:
        body["location"] = location_link[:500]
    if event_type.location_mode == "auto_meet":
        body["conferenceData"] = {"createRequest": {"requestId": "pending"}}
    return body


def _conference_status(provider_event: dict | None, mode: str) -> dict:
    if mode != "auto_meet":
        return {"mode": mode, "status": "none", "link": ""}
    entries = ((provider_event or {}).get("conferenceData") or {}).get("entryPoints") or []
    video = [e for e in entries if e.get("entryPointType") == "video"]
    if video and video[0].get("uri"):
        return {"mode": mode, "status": "ready", "link": video[0]["uri"]}
    return {"mode": mode, "status": "pending", "link": ""}


def _queue_booking_notifications(booking: dict, *, kind_prefix="", manage_url: str = ""):
    """Queue confirmation/host-notice plus 24h and 1h reminders. The
    confirmation carries the invitee's management URL (magic link); the raw
    token lives in this one server-side row until delivery, then expires."""
    start = parse_iso(booking["start_iso"])
    immediate_kind = "confirmation" if kind_prefix in ("",) else "reschedule_notice"
    if kind_prefix in ("", "rescheduled"):
        store.queue_notification({"booking_id": booking["id"], "revision": booking["revision"],
                                  "recipient": booking["invitee_email"], "kind": immediate_kind,
                                  "manage_url": manage_url,
                                  "scheduled_for": store.now_iso()})
        host_email = store.get_host().get("host_notification_email", "")
        if host_email:
            store.queue_notification({"booking_id": booking["id"], "revision": booking["revision"],
                                      "recipient": host_email, "kind": "host_notice",
                                      "scheduled_for": store.now_iso()})
    for offset in REMINDER_OFFSETS_MIN:
        remind_at = start - timedelta(minutes=offset)
        if remind_at <= utcnow():
            continue  # never schedule a reminder in the past
        store.queue_notification({"booking_id": booking["id"], "revision": booking["revision"],
                                  "recipient": booking["invitee_email"], "kind": "reminder",
                                  "reminder_offset_min": offset,
                                  "scheduled_for": remind_at.isoformat()})


def _public_receipt(booking: dict) -> dict:
    return {"reference": booking["reference"], "event_type_id": booking["event_type_id"],
            "title": (booking.get("snapshot", {}) or {}).get("title", ""),
            "start": booking["start_iso"], "end": booking["end_iso"],
            "duration_min": booking["duration_min"], "display_tz": booking.get("display_tz", "UTC"),
            "status": booking["status"], "revision": booking["revision"],
            "conference": booking.get("conference", {"status": "none"})}


# --- create -------------------------------------------------------------------

def create_booking(*, event_type_id: str, duration_min: int, start_iso: str, name: str,
                   email: str, notes: str = "", answers: dict | None = None,
                   display_tz: str = "UTC", idempotency_key: str = "",
                   dapier_client=None, provider=None, now=None) -> dict:
    at = _now(now)
    if not idempotency_key:
        raise BookingError("invalid_input", "An idempotency key is required.", status=400)
    event_type = _load_event_type(event_type_id)
    host = _policy_host()
    if host.get("pause_new_bookings"):
        raise BookingError("paused", "New bookings are paused right now.", status=503)
    if not event_type.is_bookable():
        raise BookingError("type_disabled", "This meeting type is not bookable.", status=410)
    ok, reason = validate_duration(event_type, duration_min)
    if not ok:
        raise BookingError("unsupported_duration", "That duration is not offered.", status=422)
    duration_min = int(duration_min)
    try:
        start = parse_iso(start_iso)
    except ValueError:
        raise BookingError("invalid_input", "Enter a valid start time.", status=400)
    _require_wiring(provider)
    errors = validate_invitee(name, email, event_type.questions, answers or {},
                              require_agenda=event_type.require_agenda, notes=notes)
    if errors:
        raise BookingError("invalid_input", "Check the highlighted fields.", status=422,
                           extra={"fields": errors})

    payload = {"type": event_type_id, "duration": duration_min, "start": start.isoformat(),
               "name": name.strip(), "email": email.strip().lower(), "notes": notes or "",
               "answers": answers or {}, "tz": display_tz}
    fingerprint = security.hash_management_token(canonical_payload(payload))
    operation_id = security.new_id("op_")
    intended = {**payload, "end": (start + timedelta(minutes=duration_min)).isoformat()}
    operation = {"id": operation_id, "kind": "create", "idempotency_key": idempotency_key,
                 "payload_fingerprint": fingerprint, "revision_expected": 0,
                 "intended": intended, "provider_correlation": f"create:{operation_id}",
                 "state": "in_progress", "attempts": 1, "error_sanitized": "",
                 "created_at": at.isoformat(), "updated_at": at.isoformat(),
                 "lease_expires": (at + timedelta(seconds=OPERATION_LEASE_SECONDS)).isoformat()}
    try:
        store.claim_operation(operation)
    except store.ConditionalFailed:
        return _replay_create(idempotency_key, fingerprint)

    schedule = store.get_schedule(event_type.schedule_id) or \
        {"id": event_type.schedule_id, "timezone": host.get("timezone", "Europe/Berlin"),
         "weekly_windows": {}, "overrides": {}}
    blocks = store.list_blocks()
    end = start + timedelta(minutes=duration_min)
    protected_start, protected_end, _, policy = _protection_window(
        event_type, host, schedule, start, duration_min)

    owner = security.new_id("lock_")
    _acquire_lock_or_unavailable(owner)
    try:
        window_lo = (protected_start - timedelta(hours=6)).isoformat()
        window_hi = (protected_end + timedelta(hours=6)).isoformat()
        # Fresh day-range busy for the slot check; the exact-span pre-write
        # check happens again after the reservation, just before the write.
        day_lo, day_hi = _host_day_bounds(start, end, schedule.get("timezone", "Europe/Berlin"))
        day_busy = _fresh_busy(provider, store.get_calendar_connection()["selected_calendar_id"],
                               parse_iso(day_lo), parse_iso(day_hi)) if provider else []
        local = [(parse_iso(s), parse_iso(e)) for s, e, _ in
                 store.active_protection(window_lo, window_hi)]
        existing = _existing_for_limits(
            (start - timedelta(days=1)).isoformat(), (end + timedelta(days=1)).isoformat())
        current_type = store.get_event_type(event_type_id)  # revalidate under lock
        if current_type is None or EventType.from_item(current_type).visibility == "disabled":
            raise BookingError("type_disabled", "This meeting type is not bookable.", status=410)
        event_type = EventType.from_item(current_type)
        _slot_check(event_type=event_type, host=store.get_host(), schedule=schedule,
                    duration_min=duration_min, start=start, busy=day_busy,
                    local_protected=local, blocks=blocks, existing_bookings=existing, now=at)
        reservation_id = security.new_id("res_")
        store.put_reservation({"id": reservation_id, "protected_start": protected_start.isoformat(),
                               "protected_end": protected_end.isoformat(),
                               "operation_id": operation_id, "state": "active",
                               "event_type_id": event_type_id, "duration_min": duration_min,
                               "lease_expires": (at + timedelta(seconds=OPERATION_LEASE_SECONDS)).isoformat(),
                               "lease_expires_epoch": int(at.timestamp()) + OPERATION_LEASE_SECONDS,
                               "created_at": at.isoformat()})
    except BookingError as exc:
        store.update_operation(operation_id, {"state": "failed", "error_sanitized": exc.code})
        raise
    finally:
        store.release_host_lock(owner)

    # The reservation now protects the interval across every container; the
    # provider write proceeds without holding the host lock.
    if dapier_client is None:
        raise BookingError("connection_unavailable",
                           "Calendar access is temporarily unavailable. Try again shortly.",
                           status=503)
    connection, _access = _verify_calendar_access(dapier_client)
    calendar_id = connection["selected_calendar_id"]
    correlation = f"create:{operation_id}"
    try:
        fresh = _fresh_busy(provider, calendar_id, protected_start, protected_end)
        if fresh:
            raise ConflictDetected("slot taken before write")
        provider_event = provider.create_event(
            calendar_id,
            _provider_event_payload(event_type=event_type, invitee_name=name.strip(),
                                    invitee_email=email.strip().lower(), notes=notes or "",
                                    answers=answers,
                                    start=start, end=end,
                                    schedule_tz=schedule.get("timezone", "Europe/Berlin")),
            correlation)
    except ConflictDetected as exc:
        store.update_reservation(reservation_id, {"state": "released"})
        store.update_operation(operation_id, {"state": "failed", "error_sanitized": "slot_unavailable"})
        raise BookingError("slot_unavailable", "That time was just taken. Pick another.",
                           status=409) from exc
    except (UnknownOutcome, CalendarError) as exc:
        store.update_operation(operation_id, {"state": "unknown", "error_sanitized": "provider_unknown",
                                              "lease_expires": (utcnow() + timedelta(
                                                  seconds=OPERATION_LEASE_SECONDS)).isoformat()})
        return {"status": "pending", "operation_id": operation_id}
    except BookingError:
        store.update_reservation(reservation_id, {"state": "released"})
        store.update_operation(operation_id, {"state": "failed", "error_sanitized": "connection_unavailable"})
        raise

    return _confirm_create(operation_id=operation_id, reservation_id=reservation_id,
                           provider_event=provider_event, intended=intended,
                           event_type=event_type, schedule=schedule, display_tz=display_tz,
                           provider=provider, calendar_id=calendar_id, at=at)


def _confirm_create(*, operation_id, reservation_id, provider_event, intended,
                    event_type, schedule, display_tz, provider, calendar_id, at):
    booking_id = security.new_id("bk_")
    booking = {
        "id": booking_id, "reference": security.new_reference(),
        "event_type_id": intended["type"], "snapshot": event_type.snapshot(),
        "invitee_name": intended["name"], "invitee_email": intended["email"],
        "notes": intended.get("notes", ""), "answers": intended.get("answers", {}),
        "start_iso": intended["start"], "end_iso": intended["end"],
        "duration_min": intended["duration"], "display_tz": display_tz,
        "pre_buffer_min": event_type.pre_buffer_min, "post_buffer_min": event_type.post_buffer_min,
        "calendar_ref": calendar_id, "provider_event_id": (provider_event or {}).get("id", ""),
        "provider_uid": (provider_event or {}).get("uid", (provider_event or {}).get("id", "")),
        "provider_version": str((provider_event or {}).get("version", "1")),
        "conference": _conference_status(provider_event, event_type.location_mode),
        "status": "confirmed", "revision": 1,
        "created_at": at.isoformat(), "updated_at": at.isoformat(),
    }
    try:
        store.put_booking(booking)
    except Exception:
        # Local confirmation failed after a successful provider write: keep
        # the operation recoverable; reconciliation completes it.
        store.update_operation(operation_id, {"state": "unknown", "error_sanitized": "confirm_interrupted"})
        return {"status": "pending", "operation_id": operation_id}
    if not str(reservation_id).startswith("res_orphan_"):
        store.update_reservation(reservation_id, {"state": "consumed"})
    store.update_operation(operation_id, {"state": "succeeded", "booking_id": booking_id})
    raw_token, digest = security.new_management_token()
    from datetime import timedelta as _td
    store.put_capability(digest, {"booking_id": booking_id, "actions": ["view", "cancel", "reschedule"],
                                  "created_at": at.isoformat(),
                                  "expires_at": (at + _td(seconds=365 * 24 * 3600)).isoformat(),
                                  "expires_epoch": int(at.timestamp()) + 365 * 24 * 3600,
                                  "revoked": False})
    base_url = (store.get_host().get("public_base_url", "") or "").rstrip("/")
    manage_url = f"{base_url}/m/{raw_token}" if base_url else ""
    _queue_booking_notifications(booking, manage_url=manage_url)
    store.audit("invitee", "booking.create", "booking", booking_id,
                actor_ref=booking["reference"], result="confirmed")
    _post_write_verification(provider, calendar_id, booking)
    return {"status": "confirmed", "booking": _public_receipt(booking), "manage_token": raw_token}


def _post_write_verification(provider, calendar_id: str, booking: dict):
    """The provider is an independent system: verify promptly after the write
    and flag — never silently delete — a newly detected external conflict."""
    try:
        busy, _ = provider.query_busy(calendar_id, parse_iso(booking["start_iso"]),
                                      parse_iso(booking["end_iso"]))
    except CalendarError:
        return
    own = (parse_iso(booking["start_iso"]), parse_iso(booking["end_iso"]))
    clashes = [iv for iv in busy if not (iv[1] <= own[0] or own[1] <= iv[0])]
    # Exclude the booking's own event: it always overlaps itself.
    own_id = booking.get("provider_event_id", "")
    if own_id:
        try:
            event = provider.get_event(calendar_id, own_id)
        except CalendarError:
            event = None
        if event is not None:
            clashes = [iv for iv in clashes
                       if iv[0] != own[0] or iv[1] != own[1]]
    if clashes:
        store.update_booking(booking["id"], {"provider_version": booking.get("provider_version", "")})
        store.audit("system", "booking.external_conflict", "booking", booking["id"],
                    actor_ref=booking["reference"], result="flagged for host review")


def _replay_create(idempotency_key: str, fingerprint: str) -> dict:
    existing = store.get_operation_by_key(idempotency_key)
    if existing is None:  # lost race with expiry; treat as new request
        raise BookingError("service_unavailable", "Try again shortly.", status=503)
    if existing.get("_payload_fingerprint", "") != fingerprint:
        raise BookingError("idempotency_conflict",
                           "That submission key was already used for different details.",
                           status=409)
    if existing.get("state") == "succeeded" and existing.get("booking_id"):
        booking = store.get_booking(existing["booking_id"])
        if booking is not None:
            return {"status": "confirmed", "booking": _public_receipt(booking),
                    "duplicate": True}
    return {"status": "pending", "operation_id": existing["id"], "duplicate": True}


# --- operation status and recovery --------------------------------------------

def get_operation_status(operation_id: str) -> dict:
    operation = store.get_operation(operation_id)
    if operation is None:
        raise BookingError("unknown_operation", "Unknown operation.", status=404)
    result = {"id": operation["id"], "kind": operation["kind"], "state": operation["state"]}
    if operation.get("state") == "succeeded" and operation.get("booking_id"):
        booking = store.get_booking(operation["booking_id"])
        if booking is not None:
            result["booking"] = _public_receipt(booking)
    return result


def reconcile_operation(operation_id: str, *, provider=None, now=None) -> dict:
    """Recover stalled work. Lease expiry never proves failure: the interval
    stays protected until the provider record settles the question."""
    at = _now(now)
    operation = store.get_operation(operation_id)
    if operation is None:
        raise BookingError("unknown_operation", "Unknown operation.", status=404)
    if operation.get("state") in ("succeeded", "failed"):
        return get_operation_status(operation_id)
    correlation = operation.get("provider_correlation", "")
    calendar_id = store.get_calendar_connection().get("selected_calendar_id", "")
    try:
        event = provider.get_event(calendar_id, correlation) if provider else None
    except CalendarError:
        event = None
    kind = operation.get("kind")
    if kind == "create":
        return _reconcile_create(operation, event, calendar_id, provider, at)
    if kind == "cancel":
        return _reconcile_cancel(operation, provider, calendar_id, at)
    if kind == "reschedule":
        return _reconcile_reschedule(operation, provider, calendar_id, at)
    return _reconcile_backoff(operation, at)


def _reconcile_backoff(operation, at) -> dict:
    attempts = int(operation.get("attempts", 0)) + 1
    if attempts >= 6:
        # Repeated reconciliation found nothing: release and fail loudly for
        # the host to resolve explicitly.
        store.update_operation(operation["id"], {"state": "failed", "attempts": attempts,
                                                 "error_sanitized": "provider_unknown"})
        _release_operation_reservation(operation["id"])
        return {"status": "failed", "operation_id": operation["id"]}
    store.update_operation(operation["id"], {
        "state": "unknown", "attempts": attempts,
        "lease_expires": (at + timedelta(seconds=OPERATION_LEASE_SECONDS)).isoformat()})
    return {"status": "pending", "operation_id": operation["id"]}


def _reconcile_create(operation, event, calendar_id, provider, at) -> dict:
    operation_id = operation["id"]
    if event is not None:
        intended = operation.get("intended", {})
        schedule = store.get_schedule((store.get_event_type(intended.get("type", "")) or {})
                                      .get("schedule_id", "default")) or {"timezone": "Europe/Berlin"}
        event_type = _load_event_type(intended["type"]) if intended.get("type") else None
        # Crash between provider success and local confirmation: recover the
        # booking from the pre-existing operation and provider event.
        existing = store.query_bookings(intended.get("start", ""), intended.get("start", ""))
        for booking in existing:
            if booking.get("provider_event_id") == event.get("id"):
                store.update_operation(operation_id, {"state": "succeeded",
                                                      "booking_id": booking["id"]})
                return get_operation_status(operation_id)
        confirmed = _confirm_create(
            operation_id=operation_id, reservation_id=_reservation_for(operation_id),
            provider_event=event, intended=intended, event_type=event_type,
            schedule=schedule, display_tz=intended.get("tz", "UTC"),
            provider=provider, calendar_id=calendar_id, at=at)
        return confirmed if confirmed.get("status") == "confirmed" \
            else {"status": "pending", "operation_id": operation_id}
    return _reconcile_backoff(operation, at)


def _reconcile_cancel(operation, provider, calendar_id, at) -> dict:
    """A pending cancellation completes only when provider removal is known;
    local protection holds until then."""
    operation_id = operation["id"]
    booking = store.get_booking(operation.get("booking_id", ""))
    if booking is None or booking.get("status") == "canceled":
        store.update_operation(operation_id, {"state": "succeeded",
                                              "booking_id": operation.get("booking_id", "")})
        return get_operation_status(operation_id)
    try:
        event = provider.get_event(calendar_id, booking.get("provider_event_id", "")) \
            if provider else None
    except CalendarError:
        return _reconcile_backoff(operation, at)
    if event is None:
        # Provider removal is now known: mark canceled, release protection.
        try:
            store.update_booking(booking["id"], {"status": "canceled",
                                                 "revision": int(booking.get("revision", 0)) + 1,
                                                 "pending_action": ""})
        except store.ConditionalFailed:
            pass
        store.suppress_booking_notifications(booking["id"])
        store.update_operation(operation_id, {"state": "succeeded", "booking_id": booking["id"]})
        store.audit("system", "booking.cancel_reconciled", "booking", booking["id"],
                    actor_ref=booking.get("reference", ""), result="canceled")
        return get_operation_status(operation_id)
    try:
        provider.delete_event(calendar_id, booking["provider_event_id"]) if provider else None
    except CalendarError:
        pass
    return _reconcile_backoff(operation, at)


def _reconcile_reschedule(operation, provider, calendar_id, at) -> dict:
    """A pending reschedule keeps the original booking effective until the new
    provider state is established; on definitive failure the new protection
    releases and the original stands."""
    operation_id = operation["id"]
    booking = store.get_booking(operation.get("booking_id", ""))
    intended = operation.get("intended", {}) or {}
    if booking is None or booking.get("status") != "confirmed":
        store.update_operation(operation_id, {"state": "failed", "error_sanitized": "state"})
        _release_operation_reservation(operation_id)
        return {"status": "failed", "operation_id": operation_id}
    try:
        event = provider.get_event(calendar_id, booking.get("provider_event_id", "")) \
            if provider else None
    except CalendarError:
        return _reconcile_backoff(operation, at)
    if event is not None:
        try:
            actual_start = parse_iso(event["start"]["dateTime"])
            actual_end = parse_iso(event["end"]["dateTime"])
        except (KeyError, ValueError):
            actual_start = actual_end = None
        if actual_start is not None and actual_start.isoformat() == intended.get("start") \
                and actual_end is not None and actual_end.isoformat() == intended.get("end"):
            # The provider update landed; complete the local side.
            try:
                store.update_booking(booking["id"], {"start_iso": intended["start"],
                                                     "end_iso": intended["end"],
                                                     "duration_min": intended.get(
                                                         "duration", booking.get("duration_min")),
                                                     "provider_version": str(event.get("version", "2")),
                                                     "revision": int(booking.get("revision", 0)) + 1,
                                                     "pending_action": ""},
                                     expected_revision=int(operation.get("revision_expected", 0)))
            except store.ConditionalFailed:
                return _reconcile_backoff(operation, at)
            _release_operation_reservation(operation_id)
            store.update_operation(operation_id, {"state": "succeeded", "booking_id": booking["id"]})
            store.suppress_booking_notifications(booking["id"])
            _queue_booking_notifications(store.get_booking(booking["id"]), kind_prefix="rescheduled")
            return get_operation_status(operation_id)
    attempts = int(operation.get("attempts", 0)) + 1
    if attempts >= 6:
        store.update_operation(operation_id, {"state": "failed", "attempts": attempts,
                                              "error_sanitized": "provider_error"})
        _release_operation_reservation(operation_id)
        return {"status": "failed", "operation_id": operation_id}
    return _reconcile_backoff(operation, at)
    return {"status": "pending", "operation_id": operation_id}


def _reservation_for(operation_id: str) -> str:
    for reservation in store.list_active_reservations():
        if reservation.get("operation_id") == operation_id:
            return reservation.get("id", "")
    return security.new_id("res_orphan_")


def _release_operation_reservation(operation_id: str):
    for reservation in store.list_active_reservations():
        if reservation.get("operation_id") == operation_id:
            store.update_reservation(reservation["id"], {"state": "released"})


# --- cancellation ---------------------------------------------------------------

def cancel_booking(*, booking_id: str, actor: str, expected_revision: int,
                   idempotency_key: str, reason: str = "", provider=None,
                   host_override: bool = False, now=None) -> dict:
    at = _now(now)
    booking = store.get_booking(booking_id)
    if booking is None:
        raise BookingError("unknown_booking", "Unknown booking.", status=404)
    _require_wiring(provider)
    if booking.get("status") == "canceled":
        return {"status": "canceled", "booking": _public_receipt(booking), "duplicate": True}
    payload = {"booking": booking_id, "revision": expected_revision, "reason": reason or ""}
    fingerprint = security.hash_management_token(canonical_payload(payload))
    operation_id = security.new_id("op_")
    operation = {"id": operation_id, "kind": "cancel", "idempotency_key": idempotency_key,
                 "payload_fingerprint": fingerprint, "booking_id": booking_id,
                 "revision_expected": expected_revision,
                 "intended": payload, "provider_correlation": f"cancel:{operation_id}",
                 "state": "in_progress", "attempts": 1, "error_sanitized": "",
                 "created_at": at.isoformat(), "updated_at": at.isoformat(),
                 "lease_expires": (at + timedelta(seconds=OPERATION_LEASE_SECONDS)).isoformat()}
    try:
        store.claim_operation(operation)
    except store.ConditionalFailed:
        existing = store.get_operation_by_key(idempotency_key)
        if existing and existing.get("_payload_fingerprint", "") != fingerprint:
            raise BookingError("idempotency_conflict",
                               "That submission key was already used for different details.",
                               status=409)
        current = store.get_booking(booking_id)
        return {"status": current.get("status", "unknown"),
                "booking": _public_receipt(current) if current else {}}

    owner = security.new_id("lock_")
    _acquire_lock_or_unavailable(owner)
    try:
        current = store.get_booking(booking_id)
        if current is None or current.get("status") == "canceled":
            store.update_operation(operation_id, {"state": "succeeded", "booking_id": booking_id})
            return {"status": "canceled",
                    "booking": _public_receipt(current) if current else {}, "duplicate": True}
        if int(current.get("revision", 0)) != int(expected_revision):
            store.update_operation(operation_id, {"state": "failed", "error_sanitized": "stale_revision"})
            raise BookingError("stale_revision", "This booking changed. Reload and try again.",
                               status=409)
        store.suppress_booking_notifications(booking_id)  # stop reminders at request time
        calendar_id = current.get("calendar_ref") or \
            store.get_calendar_connection().get("selected_calendar_id", "")
        try:
            removed = provider.delete_event(calendar_id, current.get("provider_event_id", "")) \
                if provider else True
        except UnknownOutcome as exc:
            store.update_operation(operation_id, {"state": "unknown", "error_sanitized": "provider_unknown",
                                                  "lease_expires": (utcnow() + timedelta(
                                                      seconds=OPERATION_LEASE_SECONDS)).isoformat()})
            store.update_booking(booking_id, {"pending_action": "cancel"})
            return {"status": "pending", "operation_id": operation_id}
        except CalendarError as exc:
            # Provider unavailable: "Cancellation pending" — retry safely via
            # reconciliation; never imply the invitee's calendar updated.
            store.update_operation(operation_id, {"state": "unknown", "error_sanitized": "provider_unknown",
                                                  "lease_expires": (utcnow() + timedelta(
                                                      seconds=OPERATION_LEASE_SECONDS)).isoformat()})
            store.update_booking(booking_id, {"pending_action": "cancel"})
            return {"status": "pending", "operation_id": operation_id}
        store.update_booking(booking_id, {"status": "canceled",
                                          "revision": int(current.get("revision", 0)) + 1,
                                          "pending_action": ""},
                             expected_revision=int(expected_revision))
        store.update_operation(operation_id, {"state": "succeeded", "booking_id": booking_id})
        canceled = store.get_booking(booking_id)
        store.queue_notification({"booking_id": booking_id, "revision": canceled["revision"],
                                  "recipient": canceled["invitee_email"], "kind": "cancellation",
                                  "scheduled_for": store.now_iso()})
        host_email = store.get_host().get("host_notification_email", "")
        if host_email:
            store.queue_notification({"booking_id": booking_id, "revision": canceled["revision"],
                                      "recipient": host_email, "kind": "host_cancellation",
                                      "scheduled_for": store.now_iso()})
        store.audit(actor, "booking.cancel", "booking", booking_id,
                    actor_ref=current.get("reference", ""), result="canceled")
        return {"status": "canceled", "booking": _public_receipt(store.get_booking(booking_id))}
    except store.ConditionalFailed as exc:
        store.update_operation(operation_id, {"state": "failed", "error_sanitized": "stale_revision"})
        raise BookingError("stale_revision", "This booking changed. Reload and try again.",
                           status=409) from exc
    finally:
        store.release_host_lock(owner)


# --- rescheduling -----------------------------------------------------------------

def reschedule_booking(*, booking_id: str, actor: str, expected_revision: int,
                       new_start_iso: str, new_duration_min: int | None,
                       idempotency_key: str, dapier_client=None, provider=None,
                       host_override: bool = False, now=None) -> dict:
    at = _now(now)
    booking = store.get_booking(booking_id)
    if booking is None:
        raise BookingError("unknown_booking", "Unknown booking.", status=404)
    _require_wiring(provider)
    if booking.get("status") != "confirmed":
        raise BookingError("invalid_state", "Only confirmed bookings can be rescheduled.",
                           status=409)
    if booking.get("pending_action") == "cancel":
        # A cancel is reconciling in the background: accepting a reschedule
        # now would race it, and the event may already be gone when the
        # patch lands. Refuse until the outcome settles — the invitee's page
        # reflects it (a reload shows canceled; a settled failure reopens
        # this path).
        raise BookingError("action_conflict",
                           "A cancellation is being processed for this booking — wait for it to land before rescheduling.",
                           status=409)
    event_type = _load_event_type(booking["event_type_id"])
    duration_min = int(new_duration_min or booking["duration_min"])
    if duration_min != booking["duration_min"]:
        ok, _ = validate_duration(event_type, duration_min)
        if not ok:
            raise BookingError("unsupported_duration", "That duration is not offered.", status=422)
    try:
        new_start = parse_iso(new_start_iso)
    except ValueError:
        raise BookingError("invalid_input", "Enter a valid start time.", status=400)
    new_end = new_start + timedelta(minutes=duration_min)

    payload = {"booking": booking_id, "revision": expected_revision,
               "start": new_start.isoformat(), "duration": duration_min}
    fingerprint = security.hash_management_token(canonical_payload(payload))
    operation_id = security.new_id("op_")
    operation = {"id": operation_id, "kind": "reschedule", "idempotency_key": idempotency_key,
                 "payload_fingerprint": fingerprint, "booking_id": booking_id,
                 "revision_expected": expected_revision,
                 "intended": {**payload, "end": new_end.isoformat()},
                 "provider_correlation": f"reschedule:{operation_id}",
                 "state": "in_progress", "attempts": 1, "error_sanitized": "",
                 "created_at": at.isoformat(), "updated_at": at.isoformat(),
                 "lease_expires": (at + timedelta(seconds=OPERATION_LEASE_SECONDS)).isoformat()}
    try:
        store.claim_operation(operation)
    except store.ConditionalFailed:
        existing = store.get_operation_by_key(idempotency_key)
        if existing and existing.get("_payload_fingerprint", "") != fingerprint:
            raise BookingError("idempotency_conflict",
                               "That submission key was already used for different details.",
                               status=409)
        current = store.get_booking(booking_id)
        return {"status": "pending", "operation_id": existing["id"] if existing else ""}

    host = store.get_host()
    schedule = store.get_schedule(event_type.schedule_id) or {"timezone": host.get("timezone", "Europe/Berlin"),
                                                              "weekly_windows": {}, "overrides": {}}
    blocks = store.list_blocks()
    policy = av.resolve_policy(event_type.to_item(), host)
    new_protected = (new_start - timedelta(minutes=policy.pre_buffer_min),
                     new_end + timedelta(minutes=policy.post_buffer_min))
    if not host_override:
        notice_at = at + timedelta(hours=policy.notice_hours)
        if new_start < notice_at:
            store.update_operation(operation_id, {"state": "failed", "error_sanitized": "policy"})
            raise BookingError("policy_violation", "The new time violates the booking policy.",
                               status=422)

    owner = security.new_id("lock_")
    _acquire_lock_or_unavailable(owner)
    try:
        current = store.get_booking(booking_id)
        if current is None or current.get("status") != "confirmed":
            store.update_operation(operation_id, {"state": "failed", "error_sanitized": "state"})
            raise BookingError("invalid_state", "This booking can no longer be rescheduled.",
                               status=409)
        if int(current.get("revision", 0)) != int(expected_revision):
            store.update_operation(operation_id, {"state": "failed", "error_sanitized": "stale_revision"})
            raise BookingError("stale_revision", "This booking changed. Reload and try again.",
                               status=409)
        window_lo = (new_protected[0] - timedelta(hours=6)).isoformat()
        window_hi = (new_protected[1] + timedelta(hours=6)).isoformat()
        calendar_id = current.get("calendar_ref") or \
            store.get_calendar_connection().get("selected_calendar_id", "")
        own_event = None
        if provider and current.get("provider_event_id"):
            try:
                fetched = provider.get_event(calendar_id, current["provider_event_id"])
            except CalendarError:
                fetched = None
            if fetched is not None:
                own_event = (parse_iso(current["start_iso"]), parse_iso(current["end_iso"]))
        day_busy = _fresh_busy(provider, calendar_id,
                               parse_iso(_host_day_bounds(new_start, new_end,
                                                          schedule.get("timezone", "Europe/Berlin"))[0]),
                               parse_iso(_host_day_bounds(new_start, new_end,
                                                          schedule.get("timezone", "Europe/Berlin"))[1])) \
            if provider else []
        local = [(parse_iso(s), parse_iso(e)) for s, e, _ in
                 store.active_protection(window_lo, window_hi)
                 if not (s == current["start_iso"] and e == current["end_iso"])]
        existing = [b for b in _existing_for_limits(
            (new_start - timedelta(days=1)).isoformat(), (new_end + timedelta(days=1)).isoformat())
            if b.get("id") != booking_id]
        day = new_start.astimezone(ZoneInfo(schedule.get("timezone", "Europe/Berlin"))).date()
        result = av.generate_slots(schedule=schedule, event_type=event_type.to_item(), host=host,
                                   duration_min=duration_min, busy=day_busy, local_protected=local,
                                   blocks=blocks, existing_bookings=existing, now_utc=at,
                                   exclude_booking_id=booking_id,
                                   own_event_interval=own_event, from_date=day, to_date=day)
        if all(offer.start != new_start for offer in result["slots"]):
            store.update_operation(operation_id, {"state": "failed", "error_sanitized": "slot_unavailable"})
            raise BookingError("slot_unavailable", "That new time is not available.", status=409)
        new_reservation = security.new_id("res_")
        store.put_reservation({"id": new_reservation, "protected_start": new_protected[0].isoformat(),
                               "protected_end": new_protected[1].isoformat(), "operation_id": operation_id,
                               "booking_id": booking_id, "state": "active",
                               "event_type_id": event_type.id, "duration_min": duration_min,
                               "lease_expires": (at + timedelta(seconds=OPERATION_LEASE_SECONDS)).isoformat(),
                               "lease_expires_epoch": int(at.timestamp()) + OPERATION_LEASE_SECONDS,
                               "created_at": at.isoformat()})
    except BookingError:
        raise
    finally:
        store.release_host_lock(owner)

    # The old booking stays effective until the provider update succeeds.
    try:
        updated = provider.update_event(
            calendar_id, current["provider_event_id"],
            {"start": {"dateTime": new_start.isoformat(),
                       "timeZone": schedule.get("timezone", "Europe/Berlin")},
             "end": {"dateTime": new_end.isoformat(),
                     "timeZone": schedule.get("timezone", "Europe/Berlin")}},
            expected_version=current.get("provider_version", "")) if provider else {"version": "2"}
    except (UnknownOutcome, ConflictDetected, CalendarError) as exc:
        unknown = isinstance(exc, UnknownOutcome)
        store.update_operation(operation_id, {
            "state": "unknown" if unknown else "failed",
            "error_sanitized": "provider_unknown" if unknown else "provider_error",
            "lease_expires": (utcnow() + timedelta(seconds=OPERATION_LEASE_SECONDS)).isoformat()})
        if not unknown:
            store.update_reservation(new_reservation, {"state": "released"})
            raise BookingError("connection_unavailable", "Rescheduling failed; the original time stands.",
                               status=503) from exc
        return {"status": "pending", "operation_id": operation_id}

    try:
        store.update_booking(booking_id, {"start_iso": new_start.isoformat(),
                                          "end_iso": new_end.isoformat(),
                                          "duration_min": duration_min,
                                          "provider_version": str(updated.get("version", "2")),
                                          "revision": int(current.get("revision", 0)) + 1,
                                          "pending_action": ""},
                             expected_revision=int(expected_revision))
    except store.ConditionalFailed as exc:
        store.update_operation(operation_id, {"state": "unknown", "error_sanitized": "confirm_interrupted"})
        return {"status": "pending", "operation_id": operation_id}
    store.update_reservation(new_reservation, {"state": "consumed"})
    store.update_operation(operation_id, {"state": "succeeded", "booking_id": booking_id})
    store.suppress_booking_notifications(booking_id)
    store.revoke_booking_capabilities(booking_id)
    raw_token, digest = security.new_management_token()
    from datetime import timedelta as _td
    store.put_capability(digest, {"booking_id": booking_id, "actions": ["view", "cancel", "reschedule"],
                                  "created_at": at.isoformat(),
                                  "expires_at": (at + _td(seconds=365 * 24 * 3600)).isoformat(),
                                  "expires_epoch": int(at.timestamp()) + 365 * 24 * 3600,
                                  "revoked": False})
    base_url = (store.get_host().get("public_base_url", "") or "").rstrip("/")
    manage_url = f"{base_url}/m/{raw_token}" if base_url else ""
    _queue_booking_notifications(store.get_booking(booking_id), kind_prefix="rescheduled",
                                 manage_url=manage_url)
    store.audit(actor, "booking.reschedule", "booking", booking_id,
                actor_ref=current.get("reference", ""), result="rescheduled")
    return {"status": "rescheduled", "booking": _public_receipt(store.get_booking(booking_id)),
            "manage_token": raw_token}


# --- invitee capability resolution --------------------------------------------------

def resolve_management_token(raw_token: str):
    """Return (capability, booking) for a valid token, else (None, None).
    An invalid token reveals nothing about any booking reference."""
    if not raw_token:
        return None, None
    capability = store.get_capability(security.hash_management_token(raw_token))
    if capability is None:
        return None, None
    booking = store.get_booking(capability.get("booking_id", ""))
    if booking is None:
        return None, None
    return capability, booking


# --- worker entry points ---------------------------------------------------------------

def process_due_notifications(email_port, *, limit=25, now=None) -> dict:
    """Deliver queued notifications. Each send re-checks booking version and
    status so an old queued reminder can never announce the wrong time."""
    at = _now(now)
    handled = {"sent": 0, "suppressed": 0, "failed": 0}
    for item in store.due_notifications(at.isoformat(), limit=limit):
        booking = store.get_booking(item.get("booking_id", ""))
        if booking is None or booking.get("status") == "canceled" \
                or int(booking.get("revision", 0)) != int(item.get("revision", 0)):
            store.update_notification(item["id"], {"status": "suppressed"})
            handled["suppressed"] += 1
            continue
        try:
            subject, text = _notification_content(item, booking)
            message_id = email_port.send(to=item["recipient"], subject=subject, text=text,
                                         reply_to=store.get_host().get("contact_fallback", ""))
            store.update_notification(item["id"], {"status": "sent", "provider_msg_id": message_id,
                                                   "attempts": int(item.get("attempts", 0)) + 1})
            handled["sent"] += 1
        except emailer.EmailError as exc:
            attempts = int(item.get("attempts", 0)) + 1
            status = "failed" if attempts >= 8 else "queued"
            store.update_notification(item["id"], {"status": status, "attempts": attempts,
                                                   "last_error": str(exc)[:500]})
            handled["failed"] += 1
    return handled


def _notification_content(item: dict, booking: dict) -> tuple[str, str]:
    title = (booking.get("snapshot", {}) or {}).get("title", "Meeting")
    when = f"{booking['start_iso']} ({booking.get('display_tz', 'UTC')})"
    base_url = store.get_host().get("public_base_url", "") or ""
    kind = item.get("kind", "confirmation")
    if kind == "reminder":
        return (f"Reminder: {title}",
                f"Reminder: {title} at {when}.\nReference: {booking['reference']}\n")
    if kind == "cancellation":
        return (f"Canceled: {title}",
                f"Your booking has been canceled.\n\nEvent: {title}\nWas: {when}\n"
                f"Reference: {booking['reference']}\n")
    if kind in ("host_cancellation",):
        return (f"Canceled booking: {title}",
                f"A booking was canceled.\n\nEvent: {title}\nWas: {when}\n"
                f"Invitee: {booking.get('invitee_name', '')} "
                f"<{booking.get('invitee_email', '')}>\n")
    if kind == "reschedule_notice":
        return (f"Rescheduled: {title}",
                emailer.render_confirmation(
                    event_title=title, start_human=when,
                    duration_min=booking.get("duration_min", 0),
                    timezone_name=booking.get("display_tz", "UTC"),
                    joining=_joining_text(booking),
                    manage_url=item.get("manage_url") or f"{base_url}/receipt",
                    reference=booking["reference"]))
    if kind == "host_notice":
        # The host reads people, not ids: answers are labeled with the
        # question text they were asked under at booking time.
        item_type = store.get_event_type(booking.get("event_type_id", "")) or {}
        agenda = models.format_answers(item_type.get("questions", []),
                                       booking.get("answers", {}),
                                       booking.get("notes", ""))
        return (f"New booking: {title}",
                emailer.render_host_notice(event_title=title, start_human=when,
                                           invitee_name=booking.get("invitee_name", ""),
                                           invitee_email=booking.get("invitee_email", ""),
                                           agenda=agenda))
    return (emailer.confirmation_subject(title),
            emailer.render_confirmation(
                event_title=title, start_human=when,
                duration_min=booking.get("duration_min", 0),
                timezone_name=booking.get("display_tz", "UTC"),
                joining=_joining_text(booking),
                manage_url=item.get("manage_url") or f"{base_url}/receipt",
                reference=booking["reference"]))


def _joining_text(booking: dict) -> str:
    conference = booking.get("conference", {}) or {}
    if conference.get("status") == "ready" and conference.get("link"):
        return conference["link"]
    if conference.get("status") == "pending":
        return "Joining details are being prepared."
    return "See your calendar invitation."


def reconcile_pending_operations(*, provider=None, now=None) -> dict:
    """Maintenance tick: recover stalled work and keep protection until each
    outcome is known. Safe to run concurrently and repeatedly."""
    at = _now(now)
    result = {"reconciled": 0, "pending": 0, "failed": 0}
    for operation in store.list_pending_operations():
        try:
            lease = datetime.fromisoformat(operation.get("lease_expires", at.isoformat()))
        except ValueError:
            lease = at
        if lease.tzinfo is None:
            lease = lease.replace(tzinfo=timezone.utc)
        if lease > at and operation.get("state") == "in_progress" \
                and int(operation.get("attempts", 0)) < 1:
            result["pending"] += 1
            continue
        outcome = reconcile_operation(operation["id"], provider=provider, now=at)
        if outcome.get("status") == "failed":
            result["failed"] += 1
        elif outcome.get("status") == "pending":
            result["pending"] += 1
        else:
            result["reconciled"] += 1
    return result


def periodic_sync(*, provider=None, now=None) -> dict:
    """Provider reconciliation: reflect manual external moves/deletes without
    reverting them, stop reminders for deleted meetings, and flag conflicts."""
    at = _now(now)
    result = {"checked": 0, "updated": 0, "canceled": 0}
    calendar_id = store.get_calendar_connection().get("selected_calendar_id", "")
    if not calendar_id or provider is None:
        return result
    lo = (at - timedelta(days=1)).isoformat()
    hi = (at + timedelta(days=65)).isoformat()
    for booking in store.query_bookings(lo, hi):
        if booking.get("status") != "confirmed" or not booking.get("provider_event_id"):
            continue
        result["checked"] += 1
        try:
            event = provider.get_event(calendar_id, booking["provider_event_id"])
        except CalendarError:
            continue  # failed refresh: keep known state, never guess
        if event is None:
            store.update_booking(booking["id"], {"status": "canceled",
                                                 "revision": int(booking.get("revision", 0)) + 1})
            store.suppress_booking_notifications(booking["id"])
            store.audit("system", "booking.external_delete", "booking", booking["id"],
                        actor_ref=booking.get("reference", ""), result="marked canceled")
            result["canceled"] += 1
            continue
        try:
            actual_start = parse_iso(event["start"]["dateTime"])
            actual_end = parse_iso(event["end"]["dateTime"])
        except (KeyError, ValueError):
            continue
        if actual_start.isoformat() != booking["start_iso"] \
                or actual_end.isoformat() != booking["end_iso"]:
            store.update_booking(booking["id"], {"start_iso": actual_start.isoformat(),
                                                 "end_iso": actual_end.isoformat(),
                                                 "duration_min": int((actual_end - actual_start)
                                                                     .total_seconds() // 60)})
            store.suppress_booking_notifications(booking["id"])
            _queue_booking_notifications(store.get_booking(booking["id"]),
                                         kind_prefix="rescheduled")
            store.audit("system", "booking.external_move", "booking", booking["id"],
                        actor_ref=booking.get("reference", ""), result="reflected")
            result["updated"] += 1
    return result
