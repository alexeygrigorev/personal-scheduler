"""Server-rendered pages. Dynamic scheduling state loads through the JSON
API; these shells carry public configuration only — never busy intervals,
provider ids, tokens, or credentials."""

import html as html_lib
import json
from pathlib import Path

from . import config, http
from .timezones import COMMON_ZONES, is_valid_zone

WEB_DIR = Path(__file__).parent.parent / "web"
ASSETS = {"app.css": "text/css; charset=utf-8",
          "booking.js": "text/javascript; charset=utf-8",
          "manage.js": "text/javascript; charset=utf-8",
          "admin.js": "text/javascript; charset=utf-8"}


def asset_response(name):
    if name not in ASSETS:
        return http.response(404, "Not found")
    try:
        body = (WEB_DIR / name).read_text()
    except OSError:
        return http.response(404, "Not found")
    return http.response(200, body, content_type=ASSETS[name],
                         headers={"cache-control": "public, max-age=3600"})


def _esc(value):
    return html_lib.escape(str(value or ""))


def shell(title, body, *, scripts=()):
    tags = "\n".join(f'<script src="/assets/{name}" defer></script>' for name in scripts)
    return (f"<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{_esc(title)}</title>\n"
            f"<link rel=\"stylesheet\" href=\"/assets/app.css\">\n{tags}\n</head>\n"
            f"<body>\n<a class=\"skip\" href=\"#main\">Skip to content</a>\n"
            f"<main class=\"wrap\" id=\"main\">\n{body}\n</main>\n</body>\n</html>")


def notice(title, message, *, status=400, link=None):
    link_html = ""
    if link:
        label, href = link
        link_html = f"<p><a href=\"{_esc(href)}\">{_esc(label)}</a></p>"
    return http.html_response(status, shell(title, f"<h1>{_esc(title)}</h1><p>{_esc(message)}</p>{link_html}"))


def duration_label(minutes):
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes / 60
    return f"{int(hours)} hour" if hours == 1 else f"{hours:g} hours"


def landing_page(host_name, intro, types):
    if types:
        cards = []
        for item in types:
            if item["duration_mode"] == "fixed":
                duration = duration_label(item["fixed_duration_min"])
            else:
                offered = sorted(item.get("allowed_durations", []))
                duration = f"{duration_label(offered[0])} – {duration_label(offered[-1])}" if offered else ""
            cards.append(
                f"<article class=\"card\"><h2>{_esc(item['title'])}</h2>"
                f"<p>{_esc(item.get('description', ''))}</p><p>{_esc(duration)}</p>"
                f"<a class=\"btn\" href=\"/{_esc(item['slug'])}\">Book</a></article>")
        body = f"<h1>{_esc(host_name)}</h1><p>{_esc(intro)}</p><div class=\"cards\">" + "".join(cards) + "</div>"
    else:
        body = (f"<h1>{_esc(host_name)}</h1>"
                "<p>There is nothing bookable here right now. If you have a direct link, it still works.</p>")
    return http.html_response(200, shell(host_name, body))


def booking_page(item, host_name, viewer_tz="UTC"):
    if item["duration_mode"] == "fixed":
        durations = [item["fixed_duration_min"]]
        default = item["fixed_duration_min"]
        duration_text = duration_label(default)
    else:
        durations = sorted(item.get("allowed_durations", []))
        default = durations[0] if durations else 30
        duration_text = f"{duration_label(durations[0])} – {duration_label(durations[-1])}"
    options = []
    for minutes in durations:
        checked = " checked" if minutes == default else ""
        options.append(
            f"<label><input type=\"radio\" name=\"duration\" value=\"{minutes}\"{checked}> "
            f"{_esc(duration_label(minutes))}</label>")
    duration_block = ("<fieldset><legend>Duration</legend>" + " ".join(options) + "</fieldset>"
                      if item["duration_mode"] == "selectable"
                      else f"<p>Duration: {_esc(duration_text)}</p>")
    questions = []
    for question in item.get("questions", []):
        qid = _esc(question.get("id", ""))
        required = " required" if question.get("required") else ""
        questions.append(
            f"<div class=\"field\"><label for=\"q-{qid}\">{_esc(question.get('label', qid))}</label>"
            f"<input id=\"q-{qid}\" data-question=\"{qid}\" maxlength=\"{int(question.get('max_length', 2000))}\"{required}>"
            f"<span class=\"error\" id=\"err-q-{qid}\" role=\"alert\"></span></div>")
    valid_viewer_tz = viewer_tz if is_valid_zone(viewer_tz) else "UTC"
    cfg = {"slug": item["slug"], "apiBase": "/api/v1", "defaultDuration": default,
           "defaultTimezone": valid_viewer_tz}
    zone_options = []
    zones = (valid_viewer_tz,) + COMMON_ZONES if valid_viewer_tz not in COMMON_ZONES else COMMON_ZONES
    for zone in zones:
        selected = " selected" if zone == cfg["defaultTimezone"] else ""
        zone_options.append(f"<option value=\"{_esc(zone)}\"{selected}>{_esc(zone)}</option>")
    body = f"""<div id="booking-root" data-config='{json.dumps(cfg)}'>
<h1>{_esc(item['title'])}</h1>
<div class="booking-grid">
<section class="info-panel" aria-label="Meeting information">
<p>Host: {_esc(host_name)}</p>
<p>{_esc(item.get('description', ''))}</p>
<p>Duration: {_esc(duration_text)}</p>
{duration_block}
</section>
<section aria-label="Choose a date and time">
<div class="toolbar">
<label>Timezone <select id="tz-select">
{''.join(zone_options)}
</select></label>
<button type="button" class="btn secondary" id="clock-toggle">Use 12-hour clock</button>
</div>
<div class="month-nav">
<button type="button" id="prev-month" aria-label="Previous month">‹</button>
<strong id="month-label"></strong>
<button type="button" id="next-month" aria-label="Next month">›</button>
<button type="button" class="btn secondary" id="next-day">Next available day</button>
</div>
<div class="day-grid" id="day-grid" role="group" aria-label="Days with availability"></div>
<ul class="times" id="time-list" aria-label="Available start times"></ul>
<p id="selection-summary" aria-live="polite">No time selected yet.</p>
</section>
<section aria-label="Your details">
<div class="status" id="booking-status" role="status">Choose a duration, date, and time to begin.</div>
<div id="details-form-wrap" hidden>
<form id="details-form" novalidate>
<div class="field"><label for="f-name">Name</label>
<input id="f-name" autocomplete="name" required><span class="error" id="err-name" role="alert"></span></div>
<div class="field"><label for="f-email">Email</label>
<input id="f-email" type="email" autocomplete="email" required><span class="error" id="err-email" role="alert"></span></div>
<div class="field"><label for="f-notes">Purpose / agenda</label>
<textarea id="f-notes" rows="3"></textarea><span class="error" id="err-notes" role="alert"></span></div>
{''.join(questions)}
<p>Availability is confirmed on submission — a selected slot is not held while you type.</p>
<p><button type="submit" class="btn" id="confirm-btn">Confirm booking</button>
<button type="button" class="btn secondary" id="back-to-times">Back</button></p>
</form>
</div>
</section>
</div>
</div>"""
    return http.html_response(200, shell(item["title"], body, scripts=("booking.js",)))


def manage_page(*, booking, token, ics_url, durations):
    status = booking.get("status", "")
    pending = booking.get("pending_action", "")
    headline = {"confirmed": "Booking confirmed", "canceled": "Booking canceled"}.get(status, "Booking")
    if pending == "cancel":
        headline += " — cancellation pending"
    duration_options = "".join(
        f"<option value=\"{m}\">{_esc(duration_label(m))}</option>" for m in durations)
    reschedule = ""
    if status == "confirmed":
        reschedule = f"""<h2>Reschedule</h2>
<form id="reschedule-form">
<div class="field"><label for="resched-start">New start (exact time)</label>
<input id="resched-start" placeholder="2026-10-07T14:00:00+02:00" required></div>
<div class="field"><label for="resched-duration">Duration</label>
<select id="resched-duration">{duration_options}</select></div>
<p><button type="submit" class="btn">Reschedule</button></p>
</form>"""
    cancel = ""
    if status == "confirmed":
        cancel = """<h2>Cancel</h2>
<form id="cancel-form">
<div class="field"><label for="cancel-reason">Reason (optional)</label>
<input id="cancel-reason" maxlength="500"></div>
<p><button type="submit" class="btn secondary">Cancel booking</button></p>
</form>"""
    joining = (booking.get("conference", {}) or {}).get("link", "") or "See your calendar invitation."
    if (booking.get("conference", {}) or {}).get("status") == "pending":
        joining = "Joining details are being prepared."
    cfg = {"token": token, "apiBase": "/api/v1",
           "revision": booking.get("revision", 0), "duration": booking.get("duration_min", 30)}
    body = f"""<div id="manage-root" data-config='{json.dumps(cfg)}'>
<h1>{_esc(headline)}</h1>
<p>Reference: <strong>{_esc(booking.get('reference', ''))}</strong></p>
<p>When: {_esc(booking.get('start_iso', ''))} – {_esc(booking.get('end_iso', ''))}
({_esc(booking.get('display_tz', 'UTC'))})</p>
<p>Joining: {_esc(joining)}</p>
<p><a href="{_esc(ics_url)}">Add to calendar (.ics snapshot)</a></p>
<div class="status" id="manage-status" role="status">Opening this page changes nothing. Canceling or rescheduling needs the explicit action below.</div>
{reschedule}
{cancel}
</div>"""
    return http.html_response(200, shell(headline, body, scripts=("manage.js",)))


def receipt_page(*, operation, booking=None):
    state = operation.get("state", "")
    if state == "succeeded" and booking:
        body = (f"<h1>Booking confirmed</h1>"
                f"<p>Reference: <strong>{_esc(booking.get('reference', ''))}</strong></p>"
                f"<p>When: {_esc(booking.get('start_iso', ''))} – {_esc(booking.get('end_iso', ''))}</p>"
                "<p>The confirmation email carries your management link.</p>")
        return http.html_response(200, shell("Booking confirmed", body))
    if state in ("in_progress", "unknown"):
        body = ("<h1>Booking is being reconciled</h1>"
                "<p>The calendar write has an unknown outcome. Your time is protected; "
                "do not book again. This page reflects the outcome once known.</p>")
        return http.html_response(200, shell("Booking pending", body))
    return notice("Booking failed", "The booking could not be completed. Please try again.",
                  status=502, link=("Back to booking", "/"))


def admin_shell(email):
    body = """<h1>Scheduler admin</h1>
<p>Signed in.</p>
<nav><button data-tab="overview" aria-pressed="true">Overview</button>
<button data-tab="types" aria-pressed="false">Event types</button>
<button data-tab="bookings" aria-pressed="false">Bookings</button>
<button data-tab="settings" aria-pressed="false">Settings</button></nav>
<section class="admin-section" id="overview"></section>
<section class="admin-section" id="types" hidden></section>
<section class="admin-section" id="bookings" hidden></section>
<section class="admin-section" id="settings" hidden></section>
<div id="admin-root"></div>
<p><a href="/auth/logout">Sign out</a></p>"""
    return http.html_response(200, shell("Admin", body, scripts=("admin.js",)))
