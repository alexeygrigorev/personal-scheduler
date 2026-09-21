"""Admin Lambda: the console and the shared-auth login flow.

Separated from the public function so that the OIDC exchange lives behind an
authenticated path only, and so a burst of booking traffic cannot throttle the
host's own access. Every admin operation re-checks the allowlist: a signed-in
non-host identity is refused, never served.
"""

import logging
import urllib.parse
from datetime import datetime, timedelta, timezone

from scheduler import config, http, oidc, render, security, service, store, wiring
from scheduler.calendar import AuthLost, CalendarError
from scheduler.dapier import DapierError
from scheduler.http import HttpError
from scheduler.models import EventType, parse_iso

logging.getLogger().setLevel(logging.INFO)


def _signed_in(event):
    return security.session_email(http.cookie(event, config.SESSION_COOKIE))


def _login(event):
    if not oidc.configured():
        return render.notice(
            "Sign-in unavailable", "Authentication is not configured for this deployment.", status=503
        )
    url, token = oidc.begin(http.query(event).get("next", "/admin"))
    return http.redirect(
        url,
        cookies=[
            http.set_cookie(config.OIDC_COOKIE, token, max_age=config.OIDC_TTL_SECONDS, path_="/auth")
        ],
    )


def _callback(event):
    clear = http.clear_cookie(config.OIDC_COOKIE, path_="/auth")
    pending = security.verify(http.cookie(event, config.OIDC_COOKIE), kind="oidc")
    query = http.query(event)
    email, next_path = oidc.complete(pending, query.get("code", ""), query.get("state", ""))
    if not email:
        return http.redirect("/auth/error", status=303, cookies=[clear])
    return http.redirect(
        next_path or "/admin",
        cookies=[
            clear,
            http.set_cookie(
                config.SESSION_COOKIE, security.new_session_token(email), max_age=config.SESSION_TTL_SECONDS
            ),
        ],
    )


def _logout():
    return http.redirect(
        oidc.logout_url() if oidc.configured() else "/",
        cookies=[http.clear_cookie(config.SESSION_COOKIE)],
    )


def _require_same_origin(event):
    """State-changing admin calls come from the console fetch layer, which is
    same-origin JSON. A cross-site form cannot set the JSON content type, and
    an explicit origin mismatch is refused outright."""
    origin = http.header(event, "origin", "")
    if origin and origin.rstrip("/") != config.SITE_URL:
        raise HttpError(403, "forbidden", "Cross-site requests are refused.")


def _overview():
    now = datetime.now(timezone.utc)
    upcoming = store.query_bookings(now.isoformat(),
                                    (now + timedelta(days=60)).isoformat(),
                                    status="confirmed", limit=100)
    connection = store.get_calendar_connection()
    return {"upcoming_count": len(upcoming),
            "upcoming": [{"reference": b["reference"], "start_iso": b["start_iso"],
                          "event_type_id": b["event_type_id"],
                          "invitee_name": b.get("invitee_name", "")} for b in upcoming[:10]],
            "pending_operations": len(store.list_pending_operations()),
            "paused": bool(store.get_host().get("pause_new_bookings")),
            "health": {"calendar": (connection.get("health") or {}).get("status", "unknown"),
                       "dapier": (connection.get("health") or {}).get("dapier", "unknown"),
                       "last_check": connection.get("last_check", "")}}


def _event_type_update(event_type_id, body):
    current = store.get_event_type(event_type_id)
    if current is None:
        raise HttpError(404, "unknown_type", "Unknown meeting type.")
    if int(body.get("expected_version", -1)) != int(current.get("version", 0)):
        raise HttpError(409, "stale_revision", "This type changed. Reload and try again.")
    merged = dict(current)
    for key in ("slug", "title", "description", "duration_mode", "fixed_duration_min",
                "allowed_durations", "visibility", "schedule_id", "pre_buffer_min",
                "post_buffer_min", "grid_min", "notice_hours", "horizon_days",
                "daily_count_limit", "daily_minutes_limit", "location_mode",
                "location_text", "require_agenda", "questions", "aliases", "position"):
        if key in body:
            merged[key] = body[key]
    merged["version"] = int(current.get("version", 0)) + 1
    try:
        store.put_event_type(merged)
    except ValueError as exc:
        raise HttpError(422, "invalid_input", str(exc))
    store.audit("admin", "event_type.update", "event_type", event_type_id,
                result=f"version {merged['version']}")
    return store.get_event_type(event_type_id)


def _admin_api(event, segments, method, email):
    from scheduler import availability as av
    from zoneinfo import ZoneInfo

    if segments == ["overview"] and method == "GET":
        return http.json_response(200, _overview())
    if segments == ["event-types"] and method == "GET":
        return http.json_response(200, {"event_types": store.list_event_types()})
    if segments == ["event-types"] and method == "POST":
        _require_same_origin(event)
        body = http.body(event)
        if body.get("action") == "duplicate" and body.get("id"):
            source = store.get_event_type(body["id"])
            if source is None:
                raise HttpError(404, "unknown_type", "Unknown meeting type.")
            clone = dict(source, id=security.new_id("et_"),
                         slug=f"{source['slug']}-copy", aliases=[],
                         title=f"{source['title']} (copy)", version=1,
                         position=int(source.get("position", 0)) + 1)
            store.put_event_type(clone)
            return http.json_response(201, store.get_event_type(clone["id"]))
        body = dict(body, id=body.get("id") or security.new_id("et_"), version=1)
        try:
            store.put_event_type(body)
        except ValueError as exc:
            raise HttpError(422, "invalid_input", str(exc))
        return http.json_response(201, store.get_event_type(body["id"]))
    if len(segments) == 2 and segments[0] == "event-types" and method == "PUT":
        _require_same_origin(event)
        return http.json_response(200, _event_type_update(segments[1], http.body(event)))
    if segments == ["schedules"] and method == "GET":
        return http.json_response(200, {"schedules": [
            s for s in [store.get_schedule("default")] if s]})
    if len(segments) == 2 and segments[0] == "schedules" and method == "PUT":
        _require_same_origin(event)
        body = http.body(event)
        current = store.get_schedule(segments[1]) or {}
        if int(body.get("expected_version", -1)) != int(current.get("version", 0)):
            raise HttpError(409, "stale_revision", "This schedule changed. Reload and try again.")
        body = dict(body, id=segments[1], version=int(current.get("version", 0)) + 1)
        store.put_schedule(body)
        return http.json_response(200, store.get_schedule(segments[1]))
    if segments == ["blocks"] and method == "GET":
        return http.json_response(200, {"blocks": store.list_blocks()})
    if segments == ["blocks"] and method == "POST":
        _require_same_origin(event)
        return http.json_response(201, store.add_block(http.body(event)))
    if len(segments) == 2 and segments[0] == "blocks" and method == "DELETE":
        _require_same_origin(event)
        store.delete_block(segments[1])
        return http.json_response(200, {"ok": True})
    if segments == ["bookings"] and method == "GET":
        query = http.query(event)
        now = datetime.now(timezone.utc)
        lo = query.get("from") or (now - timedelta(days=30)).isoformat()
        hi = query.get("to") or (now + timedelta(days=90)).isoformat()
        bookings = store.query_bookings(lo, hi, status=query.get("status") or None,
                                        event_type_id=query.get("type") or None, limit=200)
        term = (query.get("q") or "").lower()
        if term:
            bookings = [b for b in bookings
                        if term in b.get("invitee_name", "").lower()
                        or term in b.get("invitee_email", "").lower()]
        return http.json_response(200, {"bookings": bookings})
    if len(segments) == 2 and segments[0] == "bookings" and method == "GET":
        booking = store.get_booking(segments[1])
        if booking is None:
            raise HttpError(404, "unknown_booking", "Unknown booking.")
        return http.json_response(200, booking)
    if len(segments) == 3 and segments[0] == "bookings" and method == "POST":
        _require_same_origin(event)
        body = http.body(event)
        if not body.get("idempotency_key"):
            raise HttpError(400, "invalid_input", "'idempotency_key' is required.")
        _dapier, provider, _email_port, _queue = wiring.get()
        if segments[2] == "cancel":
            try:
                result = service.cancel_booking(
                    booking_id=segments[1], actor=f"admin:{email}",
                    expected_revision=int(body.get("revision", -1)),
                    idempotency_key=str(body["idempotency_key"]),
                    reason=str(body.get("reason", "") or ""),
                    provider=provider, host_override=True)
            except service.BookingError as exc:
                return http.json_response(exc.status, {"error": {"code": exc.code,
                                                                 "message": exc.message}})
            return http.json_response(200, result)
        if segments[2] == "reschedule":
            try:
                result = service.reschedule_booking(
                    booking_id=segments[1], actor=f"admin:{email}",
                    expected_revision=int(body.get("revision", -1)),
                    new_start_iso=str(body.get("start", "")),
                    new_duration_min=body.get("duration"),
                    idempotency_key=str(body["idempotency_key"]),
                    dapier_client=_dapier, provider=provider, host_override=True)
            except service.BookingError as exc:
                return http.json_response(exc.status, {"error": {"code": exc.code,
                                                                 "message": exc.message}})
            return http.json_response(200, result)
        raise HttpError(404, "invalid_link", "Unknown booking action.")
    if segments == ["calendar"] and method == "GET":
        return http.json_response(200, store.get_calendar_connection())
    if segments == ["dapier"] and method == "GET":
        connection = store.get_calendar_connection()
        return http.json_response(200, {
            "connection_ref": connection.get("dapier_connection_ref"),
            "expected_provider": connection.get("expected_provider"),
            "expected_account": connection.get("expected_account"),
            "health": (connection.get("health") or {}).get("dapier", "unknown")})
    if segments == ["settings"] and method == "GET":
        return http.json_response(200, {"host": store.get_host()})
    if segments == ["settings"] and method == "PUT":
        _require_same_origin(event)
        store.update_host(http.body(event))
        store.audit("admin", "host.update", "host", "main", actor_ref=email, result="updated")
        return http.json_response(200, {"host": store.get_host()})
    if segments == ["health"] and method == "GET":
        overview = _overview()
        return http.json_response(200, {"status": "ok", **overview})
    if segments == ["availability-preview"] and method == "GET":
        query = http.query(event)
        item = store.get_event_type(query.get("type", ""))
        if item is None:
            raise HttpError(404, "unknown_type", "Unknown meeting type.")
        schedule = store.get_schedule(item.get("schedule_id", "default"))
        host = store.get_host()
        day = query.get("date", "")
        try:
            target = datetime.fromisoformat(day).date()
        except ValueError:
            raise HttpError(400, "invalid_input", "A date is required.")
        result = av.generate_slots(
            schedule=schedule, event_type=item, host=host,
            duration_min=int(query.get("duration", item.get("fixed_duration_min") or 30)),
            busy=[], local_protected=[], blocks=store.list_blocks(), existing_bookings=[],
            now_utc=datetime.now(timezone.utc), from_date=target, to_date=target)
        return http.json_response(200, {
            "slots": [{"start": o.start.isoformat(), "end": o.end.isoformat()}
                      for o in result["slots"]]})
    if segments == ["why-unavailable"] and method == "GET":
        query = http.query(event)
        item = store.get_event_type(query.get("type", ""))
        if item is None:
            raise HttpError(404, "unknown_type", "Unknown meeting type.")
        schedule = store.get_schedule(item.get("schedule_id", "default"))
        try:
            moment = parse_iso(query.get("moment", ""))
        except ValueError:
            raise HttpError(400, "invalid_input", "A moment is required.")
        return http.json_response(200, {"reasons": av.why_unavailable(
            moment=moment, duration_min=int(query.get("duration", 30)), schedule=schedule,
            event_type=item, host=store.get_host(), busy=[], local_protected=[],
            blocks=store.list_blocks(), now_utc=datetime.now(timezone.utc))})
    raise HttpError(404, "invalid_link", "Unknown admin resource.")


def lambda_handler(event, _context):
    path = http.path(event)

    try:
        if path == "/auth/login":
            return _login(event)
        if path == "/auth/callback":
            return _callback(event)
        if path == "/auth/logout":
            return _logout()
        if path == "/auth/error":
            return render.notice(
                "Sign-in failed",
                "Sign in with the host account. If the problem persists, contact the host.",
                status=403,
                link=("Try again", "/auth/login"),
            )

        email = _signed_in(event)
        if not email:
            return http.redirect(f"/auth/login?next={urllib.parse.quote(path)}")
        # Authentication proves identity; the host allowlist authorizes.
        if not store.is_admin(email):
            return render.notice(
                "Not allowed", "This console is for the host account only.", status=403
            )

        if path == "/admin" or path == "/admin/":
            return render.admin_shell(email)
        if path.startswith("/admin/api/"):
            segments = [part for part in path[len("/admin/api/"):].split("/") if part]
            try:
                return _admin_api(event, segments, http.method(event), email)
            except (DapierError, AuthLost, CalendarError) as exc:
                logging.warning("admin integration unavailable: %s", type(exc).__name__)
                return http.json_response(503, {"error": {"code": "connection_unavailable",
                                                          "message": "Integration unavailable."}})
        if path.startswith("/admin/"):
            return render.admin_shell(email)

        return render.notice("Not found", "Nothing lives at this address.", status=404)

    except HttpError as exc:
        if path.startswith("/admin/api/"):
            return http.error_response(exc)
        return render.notice("Something went wrong", exc.message, status=exc.status)
    except Exception:
        logging.exception("unhandled admin error path=%s", path)
        if path.startswith("/admin/api/"):
            return http.json_response(500, {"error": {"code": "internal", "message": "Try again."}})
        return render.notice("Something went wrong", "Please try again.", status=500)
