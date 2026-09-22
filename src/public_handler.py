"""Public Lambda: landing page, booking flow, management links, REST API.

This function takes anonymous booking traffic and holds no OIDC client. All
scheduling rules re-check server-side on every write: a cached, signed, or
opaque slot offer is never a lock on the calendar.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from scheduler import availability as av
from scheduler import config, http, render, security, service, store, wiring
from scheduler.timezones import viewer_timezone
from scheduler.calendar import AuthLost, CalendarError
from scheduler.dapier import DapierError
from scheduler.http import HttpError
from scheduler.models import EventType, parse_iso, validate_duration

logging.getLogger().setLevel(logging.INFO)

_rate_buckets: dict = {}


def reset_rate_limits():
    _rate_buckets.clear()


def _limited(ip: str, scope: str, count: int, window_s: int) -> bool:
    """Best-effort per-container fixed-window limiter. It backs the table's
    idempotency and the host lock, not replaces them."""
    now = time.time()
    if len(_rate_buckets) > 5000:
        _rate_buckets.clear()
    window_start, seen = _rate_buckets.get((ip, scope), (now, 0))
    if now - window_start > window_s:
        _rate_buckets[(ip, scope)] = (now, 1)
        return False
    if seen >= count:
        return True
    _rate_buckets[(ip, scope)] = (window_start, seen + 1)
    return False


def _base_url() -> str:
    try:
        configured = (store.get_host().get("public_base_url", "") or "").rstrip("/")
    except Exception:
        configured = ""
    return configured or config.SITE_URL


def _booking_error_response(exc: service.BookingError):
    body = {"error": {"code": exc.code, "message": exc.message}}
    if exc.extra:
        body["error"]["details"] = exc.extra
    return http.json_response(exc.status, body)


def _infra_error_response(exc):
    logging.warning("provider integration unavailable: %s", type(exc).__name__)
    return http.json_response(503, {"error": {"code": "connection_unavailable",
                                              "message": "Calendar access is temporarily "
                                                         "unavailable. Try again shortly."}})


def _public_type_view(item: dict) -> dict:
    return {"slug": item["slug"], "title": item["title"],
            "description": item.get("description", ""),
            "duration_mode": item["duration_mode"],
            "fixed_duration_min": item.get("fixed_duration_min"),
            "allowed_durations": item.get("allowed_durations", []),
            "location_mode": item.get("location_mode", "fixed_text"),
            "location_text": item.get("location_text", "") if item.get("location_mode") != "auto_meet" else "",
            "questions": [{"id": q.get("id"), "label": q.get("label"),
                           "required": bool(q.get("required"))}
                          for q in item.get("questions", [])]}


def _resolve_or_error(slug: str) -> dict:
    item = store.resolve_slug(slug)
    if item is None:
        raise HttpError(404, "invalid_link", "This link does not point at a bookable meeting.")
    if item.get("visibility") == "disabled":
        raise HttpError(410, "type_disabled",
                        "This meeting type no longer takes new bookings. "
                        "Existing booking links still work.")
    return item


def _availability(event):
    query = http.query(event)
    slug = (query.get("type") or "").strip()
    if not slug:
        raise HttpError(400, "invalid_input", "A meeting type is required.")
    item = _resolve_or_error(slug)
    event_type = EventType.from_item(item)
    ok, _ = validate_duration(event_type, query.get("duration", ""))
    if not ok:
        raise HttpError(422, "unsupported_duration", "That duration is not offered.")
    duration_min = int(query.get("duration"))
    viewer_tz = viewer_timezone(event)  # already validated against ZoneInfo
    try:
        viewer_from = date.fromisoformat(query.get("from", "")) if query.get("from") else None
        viewer_to = date.fromisoformat(query.get("to", "")) if query.get("to") else None
    except ValueError:
        raise HttpError(400, "invalid_input", "Dates must be YYYY-MM-DD.")
    today_viewer = datetime.now(ZoneInfo(viewer_tz)).date()
    viewer_from = viewer_from or today_viewer
    viewer_to = viewer_to or (viewer_from + timedelta(days=30))
    if (viewer_to - viewer_from).days > 62 or viewer_to < viewer_from:
        raise HttpError(400, "invalid_input", "Date range is too large.")
    host = store.get_host()
    schedule = store.get_schedule(item.get("schedule_id", "default")) or \
        {"timezone": host.get("timezone", "Europe/Berlin"), "weekly_windows": {}, "overrides": {}}
    host_from, host_to = av.expand_viewer_range(viewer_from, viewer_to, viewer_tz,
                                                schedule.get("timezone", "Europe/Berlin"))
    span_start = datetime(host_from.year, host_from.month, host_from.day,
                          tzinfo=ZoneInfo(schedule.get("timezone", "Europe/Berlin"))).astimezone(timezone.utc)
    span_end = (datetime(host_to.year, host_to.month, host_to.day,
                         tzinfo=ZoneInfo(schedule.get("timezone", "Europe/Berlin")))
                + timedelta(days=1)).astimezone(timezone.utc)
    _dapier, provider, _email, _queue = wiring.get()
    connection = store.get_calendar_connection()
    now_utc = datetime.now(timezone.utc)
    if provider is None or not connection.get("selected_calendar_id"):
        raise HttpError(503, "connection_unavailable", "Booking is temporarily unavailable.")
    busy = service._fresh_busy(provider, connection["selected_calendar_id"], span_start, span_end)
    busy = [(b if isinstance(b, datetime) else parse_iso(b),
             e if isinstance(e, datetime) else parse_iso(e)) for b, e in busy]
    local = [(parse_iso(s), parse_iso(e)) for s, e, _ in
             store.active_protection(span_start.isoformat(), span_end.isoformat())]
    existing = store.query_bookings(span_start.isoformat(), span_end.isoformat())
    result = av.generate_slots(schedule=schedule, event_type=item, host=host,
                               duration_min=duration_min, busy=busy, local_protected=local,
                               blocks=store.list_blocks(), existing_bookings=existing,
                               now_utc=now_utc, from_date=host_from, to_date=host_to)
    slots = []
    for offer in result["slots"]:
        viewer_day = offer.start.astimezone(ZoneInfo(viewer_tz)).date()
        if viewer_from <= viewer_day <= viewer_to:
            slots.append({"start": offer.start.isoformat(), "end": offer.end.isoformat()})
    return http.json_response(
        200,
        {"type": slug, "title": item["title"], "duration_min": duration_min,
         "timezone": viewer_tz, "slots": slots,
         "freshness": result["freshness"].isoformat(),
         "notice": "A selected slot is confirmed on submission; it is not held while you decide."},
        headers={"cache-control": "public, max-age=30"})


def _create_booking(event):
    body = http.body(event)
    for field in ("type", "duration", "start", "name", "email", "idempotency_key"):
        if body.get(field) in (None, ""):
            raise HttpError(400, "invalid_input", f"'{field}' is required.")
    item = _resolve_or_error(str(body["type"]))
    dapier_client, provider, _email, queue = wiring.get()
    try:
        result = service.create_booking(
            event_type_id=item["id"], duration_min=body["duration"], start_iso=str(body["start"]),
            name=str(body.get("name", "")), email=str(body.get("email", "")),
            notes=str(body.get("notes", "") or ""), answers=body.get("answers") or {},
            display_tz=str(body.get("tz") or "UTC"), idempotency_key=str(body["idempotency_key"]),
            dapier_client=dapier_client, provider=provider)
    except service.BookingError as exc:
        return _booking_error_response(exc)
    if queue is not None:
        try:
            queue.send({"kind": "notify"})
        except Exception:
            pass
    if result.get("status") == "confirmed":
        token = result.pop("manage_token", "")
        result["manage_url"] = f"{_base_url()}/m/{token}" if token else ""
        return http.json_response(201, result)
    result["receipt_url"] = f"{_base_url()}/receipt/{result.get('operation_id', '')}"
    return http.json_response(202, result)


def _operation_status(operation_id):
    try:
        result = service.get_operation_status(operation_id)
    except service.BookingError as exc:
        return _booking_error_response(exc)
    if result.get("state") == "succeeded":
        result["receipt_url"] = f"{_base_url()}/receipt/{operation_id}"
    return http.json_response(200, result)


def _ics_for_booking(booking: dict) -> str:
    def stamp(value):
        return parse_iso(value).strftime("%Y%m%dT%H%M%SZ")

    title = (booking.get("snapshot", {}) or {}).get("title", "Meeting")
    uid = booking.get("provider_uid") or f"{booking['id']}@scheduler"
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//scheduler//booking//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\n"
            f"DTSTART:{stamp(booking['start_iso'])}\r\n"
            f"DTEND:{stamp(booking['end_iso'])}\r\n"
            f"SUMMARY:{title}\r\n"
            "STATUS:CONFIRMED\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")


def _manage_api(event, token, action, method):
    capability, booking = service.resolve_management_token(token)
    if capability is None or booking is None:
        return http.json_response(404, {"error": {"code": "invalid_link",
                                                  "message": "This management link is invalid or expired."}})
    if method == "GET" and action == "ics":
        return http.response(200, _ics_for_booking(booking), content_type="text/calendar; charset=utf-8",
                             headers={"content-disposition": 'attachment; filename="booking.ics"',
                                      "cache-control": "no-store"})
    if method != "POST":
        return http.json_response(405, {"error": {"code": "invalid_input", "message": "Use POST."}})
    body = http.body(event)
    if "cancel" not in (capability.get("actions") or []):
        return http.json_response(403, {"error": {"code": "not_allowed",
                                                  "message": "This link no longer allows changes."}})
    _dapier, provider, _email, queue = wiring.get()
    if action == "cancel":
        if not body.get("idempotency_key"):
            raise HttpError(400, "invalid_input", "'idempotency_key' is required.")
        try:
            result = service.cancel_booking(
                booking_id=booking["id"], actor="invitee",
                expected_revision=int(body.get("revision", -1)),
                idempotency_key=str(body["idempotency_key"]),
                reason=str(body.get("reason", "") or ""), provider=provider)
        except service.BookingError as exc:
            return _booking_error_response(exc)
    elif action == "reschedule":
        for field in ("start", "idempotency_key"):
            if body.get(field) in (None, ""):
                raise HttpError(400, "invalid_input", f"'{field}' is required.")
        try:
            result = service.reschedule_booking(
                booking_id=booking["id"], actor="invitee",
                expected_revision=int(body.get("revision", -1)),
                new_start_iso=str(body["start"]),
                new_duration_min=body.get("duration") or booking["duration_min"],
                idempotency_key=str(body["idempotency_key"]),
                dapier_client=_dapier, provider=provider)
        except service.BookingError as exc:
            return _booking_error_response(exc)
        token = result.pop("manage_token", "")
        result["manage_url"] = f"{_base_url()}/m/{token}" if token else ""
    else:
        return http.json_response(404, {"error": {"code": "invalid_link", "message": "Unknown action."}})
    if queue is not None:
        try:
            queue.send({"kind": "notify"})
        except Exception:
            pass
    return http.json_response(200, result)


def _brand():
    host = store.get_host()
    return str(host.get("display_name", "") or "")


def _serve_booking_page(slug, event):
    item = store.resolve_slug(slug)
    if item is None:
        return render.notice("Invalid link", "This link does not point at a bookable meeting.",
                             status=404, link=("See bookable meetings", "/"),
                             brand_name=_brand())
    if item.get("visibility") == "disabled":
        return render.notice("Unavailable", "This meeting type no longer takes new bookings. "
                                            "Existing booking links still work.", status=410,
                             link=("See bookable meetings", "/"), brand_name=_brand())
    host = store.get_host()
    return render.booking_page(item, host.get("display_name", "Scheduling"),
                               viewer_timezone(event))


def _serve_manage_page(token):
    capability, booking = service.resolve_management_token(token)
    if capability is None or booking is None:
        # No oracle: an invalid token reads exactly like an expired one.
        return render.notice("Invalid link", "This management link is invalid or expired.",
                             status=404, link=("See bookable meetings", "/"),
                             brand_name=_brand())
    item = store.get_event_type(booking["event_type_id"]) or {}
    durations = [booking["duration_min"]] if item.get("duration_mode") == "fixed" \
        else sorted(item.get("allowed_durations", [booking["duration_min"]]))
    host = store.get_host()
    return render.manage_page(booking=booking, token=token,
                              ics_url=f"/api/v1/manage/{token}/ics", durations=durations,
                              event_title=str(item.get("title", "") or ""),
                              host_name=str(host.get("display_name", "") or ""))


def _serve_receipt(operation_id):
    try:
        result = service.get_operation_status(operation_id)
    except service.BookingError:
        return render.notice("Unknown receipt", "No booking operation matches this link.",
                             status=404, link=("See bookable meetings", "/"),
                             brand_name=_brand())
    booking = result.get("booking") if result.get("state") == "succeeded" else None
    ics_url = ""
    if booking is not None and booking.get("reference"):
        full = store.get_booking_by_reference(booking["reference"])
        booking = {**booking, "conference": (full or {}).get("conference", {})}
        if full:
            ics_url = f"/receipt/{operation_id}/ics"
    host = store.get_host()
    return render.receipt_page(operation=result, booking=booking,
                               host_name=str(host.get("display_name", "") or ""),
                               ics_url=ics_url)


def _serve_receipt_ics(operation_id):
    try:
        result = service.get_operation_status(operation_id)
    except service.BookingError:
        return render.notice("Unknown receipt", "No booking operation matches this link.",
                             status=404, link=("See bookable meetings", "/"),
                             brand_name=_brand())
    booking = result.get("booking") if result.get("state") == "succeeded" else None
    if booking is None or not booking.get("reference"):
        return render.notice("No calendar file",
                             "This link has no confirmed booking attached.",
                             status=404, link=("See bookable meetings", "/"),
                             brand_name=_brand())
    full = store.get_booking_by_reference(booking["reference"]) or {}
    return http.response(200, _ics_for_booking({**booking, **full}),
                         content_type="text/calendar; charset=utf-8",
                         headers={"content-disposition": 'attachment; filename="booking.ics"',
                                  "cache-control": "no-store"})


def lambda_handler(event, _context):
    path = http.path(event)
    method = http.method(event)
    ip = http.source_ip(event) or "unknown"

    try:
        if path == "/health" and method == "GET":
            return http.json_response(200, {"status": "ok", "service": "personal-scheduler"})
        if path.startswith("/assets/"):
            return render.asset_response(path[len("/assets/"):])
        # First-run seed is lazy and idempotent: every content/API route reads
        # event types, but health checks and static assets never touch storage.
        store.ensure_seed()
        if path == "/" and method == "GET":
            host = store.get_host()
            listed = [t for t in store.list_event_types() if t.get("visibility") == "listed"]
            return render.landing_page(host.get("display_name", "Scheduling"),
                                       host.get("contact_fallback", ""), listed)
        if path == "/api/v1/types" and method == "GET":
            listed = [t for t in store.list_event_types() if t.get("visibility") == "listed"]
            return http.json_response(200, {"event_types": [_public_type_view(t) for t in listed]})
        if path.startswith("/api/v1/types/") and method == "GET":
            try:
                item = _resolve_or_error(path[len("/api/v1/types/"):].strip("/"))
            except HttpError as exc:
                return http.error_response(exc)
            return http.json_response(200, _public_type_view(item))
        if path == "/api/v1/availability" and method == "GET":
            if _limited(ip, "availability", 120, 60):
                return http.json_response(429, {"error": {"code": "rate_limited",
                                                          "message": "Slow down and try again."}})
            try:
                return _availability(event)
            except (DapierError, AuthLost, CalendarError) as exc:
                return _infra_error_response(exc)
        if path == "/api/v1/bookings" and method == "POST":
            if _limited(ip, "bookings", 12, 60):
                return http.json_response(429, {"error": {"code": "rate_limited",
                                                          "message": "Slow down and try again."}})
            try:
                return _create_booking(event)
            except (DapierError, AuthLost, CalendarError) as exc:
                return _infra_error_response(exc)
        if path.startswith("/api/v1/operations/") and method == "GET":
            return _operation_status(path[len("/api/v1/operations/"):].strip("/"))
        if path.startswith("/api/v1/manage/"):
            if method == "POST" and _limited(ip, "manage", 30, 60):
                return http.json_response(429, {"error": {"code": "rate_limited",
                                                          "message": "Slow down and try again."}})
            rest = path[len("/api/v1/manage/"):].strip("/").split("/")
            if len(rest) == 2:
                try:
                    return _manage_api(event, rest[0], rest[1], method)
                except (DapierError, AuthLost, CalendarError) as exc:
                    return _infra_error_response(exc)
            return http.json_response(404, {"error": {"code": "invalid_link",
                                                      "message": "Unknown management action."}})
        if path.startswith("/m/") and method == "GET":
            return _serve_manage_page(path[len("/m/"):].strip("/").split("/")[0])
        if path.startswith("/receipt/") and method == "GET":
            if path.endswith("/ics"):
                return _serve_receipt_ics(path[len("/receipt/"):-len("/ics")].strip("/"))
            return _serve_receipt(path[len("/receipt/"):].strip("/").split("/")[0])
        if path.startswith("/") and method == "GET" and path.count("/") == 1:
            return _serve_booking_page(path.strip("/"), event)
        return render.notice("Not found", "Nothing lives at this address.", status=404)
    except HttpError as exc:
        if path.startswith("/api/"):
            return http.error_response(exc)
        return render.notice("Something went wrong", exc.message, status=exc.status)
    except Exception:
        logging.exception("unhandled public error path=%s method=%s", path, method)
        if path.startswith("/api/"):
            return http.json_response(500, {"error": {"code": "internal", "message": "Try again."}})
        return render.notice("Something went wrong", "Please try again.", status=500)
