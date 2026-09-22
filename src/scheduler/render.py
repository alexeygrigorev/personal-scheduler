"""Server-rendered pages. Dynamic scheduling state loads through the JSON
API; these shells carry public configuration only — never busy intervals,
provider ids, tokens, or credentials."""

import html as html_lib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


_ICON_PATHS = {
    "calendar": ('<rect x="3" y="4" width="18" height="17" rx="2"/>'
                 '<line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/>'
                 '<line x1="3" y1="10" x2="21" y2="10"/>'),
    "clock": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>',
    "video": ('<rect x="1" y="5" width="14" height="14" rx="2"/>'
              '<polygon points="23 7 16 12 23 17 23 7"/>'),
    "chat": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "spark": ('<path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3'
              'L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3z"/>'),
    "check": ('<path d="M22 11.1V12a10 10 0 1 1-5.9-9.1"/>'
              '<polyline points="22 4 12 14 9 11"/>'),
    "x": ('<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/>'
          '<line x1="9" y1="9" x2="15" y2="15"/>'),
    "alert": ('<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9'
              'a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/>'
              '<line x1="12" y1="17" x2="12.01" y2="17"/>'),
    "info": ('<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/>'
             '<line x1="12" y1="8" x2="12.01" y2="8"/>'),
    "left": '<polyline points="15 18 9 12 15 6"/>',
    "right": '<polyline points="9 18 15 12 9 6"/>',
    "download": ('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                 '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'),
}


def icon(name, cls=""):
    return (f'<svg class="{cls}" width="18" height="18" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{_ICON_PATHS[name]}</svg>')


def _initials(name):
    parts = [p for p in str(name or "").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "S"


def shell(title, body, *, scripts=(), brand_name=None, head_side="", main_class="",
          tz_note=None):
    mark = _esc(_initials(brand_name)) if brand_name else icon("calendar")
    side = f'<div class="site-head-side">{head_side}</div>' if head_side else ""
    tags = "\n".join(f'<script src="/assets/{name}" defer></script>' for name in scripts)
    favicon = ("<link rel=\"icon\" href=\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
               "viewBox='0 0 24 24' fill='none' stroke='%234f46e5' stroke-width='2' stroke-linecap='round' "
               "stroke-linejoin='round'%3E%3Crect x='3' y='4' width='18' height='17' rx='2'/%3E"
               "%3Cline x1='16' y1='2' x2='16' y2='6'/%3E%3Cline x1='8' y1='2' x2='8' y2='6'/%3E"
               "%3Cline x1='3' y1='10' x2='21' y2='10'/%3E%3C/svg%3E\">")
    foot_note = f'<span id="tz-note">{_esc(tz_note)}</span>' if tz_note else ""
    main_cls = f"wrap {main_class}" if main_class else "wrap"
    return (f"<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<meta name=\"color-scheme\" content=\"light\">\n{favicon}\n"
            f"<title>{_esc(title)}</title>\n"
            f"<link rel=\"stylesheet\" href=\"/assets/app.css\">\n{tags}\n</head>\n"
            f"<body>\n<a class=\"skip\" href=\"#main\">Skip to content</a>\n"
            f"<header class=\"site-head\"><div class=\"wrap\">"
            f"<a class=\"brand\" href=\"/\"><span class=\"brand-mark\">{mark}</span>"
            f"<span class=\"brand-name\">{_esc(brand_name or 'Scheduler')}</span></a>{side}"
            f"</div></header>\n"
            f"<main class=\"{main_cls}\" id=\"main\">\n{body}\n</main>\n"
            f"<footer class=\"site-foot\"><div class=\"wrap\">"
            f"<span>Powered by personal-scheduler</span>{foot_note}</div></footer>\n"
            f"</body>\n</html>")


def notice(title, message, *, status=400, link=None, brand_name=None):
    link_html = ""
    if link:
        label, href = link
        link_html = (f"<div class=\"form-actions\"><a class=\"btn secondary\" "
                     f"href=\"{_esc(href)}\">{_esc(label)}</a></div>")
    body = (f"<div class=\"centerpiece panel\">"
            f"<span class=\"status-orb failed\">{icon('x', 'ic')}</span>"
            f"<h1>{_esc(title)}</h1><p>{_esc(message)}</p>{link_html}</div>")
    # The brand carries over when the caller knows it: a visitor whose manage
    # link expired must still see the host they booked with, not a foreign
    # "Scheduler" chrome no other page in the flow uses.
    return http.html_response(status, shell(title, body, main_class="centered",
                                            brand_name=brand_name))


def duration_label(minutes):
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes / 60
    return f"{int(hours)} hour" if hours == 1 else f"{hours:g} hours"


_LANDING_ICONS = ("video", "chat", "spark", "clock")


def landing_page(host_name, intro, types):
    if types:
        cards = []
        for i, item in enumerate(types):
            if item["duration_mode"] == "fixed":
                duration = duration_label(item["fixed_duration_min"])
            else:
                offered = sorted(item.get("allowed_durations", []))
                duration = f"{duration_label(offered[0])} – {duration_label(offered[-1])}" if offered else ""
            cards.append(
                f"<article class=\"card\">"
                f"<span class=\"card-icon\">{icon(_LANDING_ICONS[i % len(_LANDING_ICONS)], 'ic')}</span>"
                f"<h2>{_esc(item['title'])}</h2>"
                f"<p class=\"desc\">{_esc(item.get('description', ''))}</p>"
                f"<span class=\"meta-chip\">{icon('clock', 'ic')}{_esc(duration)}</span>"
                f"<a class=\"btn\" href=\"/{_esc(item['slug'])}\" "
                f"aria-label=\"Book {_esc(item['title'])}\">Book</a></article>")
        body = (f"<div class=\"hero\">"
                f"<span class=\"avatar\" aria-hidden=\"true\">{_esc(_initials(host_name))}</span>"
                f"<div><h1>{_esc(host_name)}</h1>"
                f"<p class=\"intro\">{_esc(intro)}</p></div></div>"
                f"<div class=\"cards\">" + "".join(cards) + "</div>")
    else:
        body = (f"<div class=\"hero\">"
                f"<span class=\"avatar\" aria-hidden=\"true\">{_esc(_initials(host_name))}</span>"
                f"<div><h1>{_esc(host_name)}</h1></div></div>"
                f"<div class=\"empty-state\"><h2>Nothing bookable right now</h2>"
                f"<p>There are no meeting types listed at the moment. "
                f"If you have a direct link, it still works.</p></div>")
    return http.html_response(200, shell(host_name, body, brand_name=host_name,
                                         main_class="landing"))


def _status_box(status_id, initial_text, extra_cls=""):
    cls = f"status {extra_cls}" if extra_cls else "status"
    return (f"<div class=\"{cls}\" id=\"{status_id}\" role=\"status\">"
            f"{icon('info', 'ic s-info')}{icon('alert', 'ic s-error')}{icon('check', 'ic s-ok')}"
            f"<span class=\"status-text\">{_esc(initial_text)}</span></div>")


def booking_page(item, host_name, viewer_tz="UTC"):
    selectable = item["duration_mode"] == "selectable"
    if not selectable:
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
    if selectable:
        duration_block = (f"<fieldset><legend>Duration</legend>"
                          f"<div class=\"segmented\">{''.join(options)}</div></fieldset>")
    else:
        duration_block = ""
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
    if selectable:
        duration_meta = ""
    else:
        duration_meta = (f"<div class=\"row\">{icon('clock', 'ic')}"
                         f"<span>Duration: {_esc(duration_text)}</span></div>")
    body = f"""<div id="booking-root" data-config='{json.dumps(cfg)}'>
<div class="page-intro"><h1>{_esc(item['title'])}</h1>
<p class="sub">Hosted by {_esc(host_name)} · {_esc(duration_text)}</p></div>
<div class="booking-grid">
<section class="info-panel" aria-label="Meeting information">
<div class="host-row">
<span class="avatar" aria-hidden="true">{_esc(_initials(host_name))}</span>
<span><span class="host-name">{_esc(host_name)}</span><br>
<span class="host-tag">Host</span></span>
</div>
<p class="info-desc">{_esc(item.get('description', ''))}</p>
<div class="info-meta">
<div class="row">{icon('video', 'ic')}<span>Online meeting</span></div>
{duration_meta}
<div class="row">{icon('calendar', 'ic')}<span>Confirmed instantly on the host's calendar</span></div>
</div>
{duration_block}
</section>
<section class="calendar-panel" aria-label="Choose a date and time">
<div class="toolbar">
<label class="grow">Timezone <select id="tz-select">
{''.join(zone_options)}
</select></label>
<button type="button" class="btn ghost sm" id="clock-toggle">Use 12-hour clock</button>
</div>
{_status_box('booking-status', 'Choose a duration, date, and time to begin.')}
<div class="month-nav">
<button type="button" class="icon-btn" id="prev-month" aria-label="Previous month">{icon('left', 'ic')}</button>
<span class="month-label" id="month-label"></span>
<button type="button" class="icon-btn" id="next-month" aria-label="Next month">{icon('right', 'ic')}</button>
<button type="button" class="btn secondary sm" id="next-day">Next available day</button>
</div>
<div class="day-grid" id="day-grid" role="group" aria-label="Days with availability"></div>
<div class="times-head" id="times-head"><h2>Available times</h2></div>
<ul class="times" id="time-list" aria-label="Available start times"></ul>
</section>
<section class="details-panel" aria-label="Your details">
<div class="panel details-panel-inner">
<div class="placeholder" id="details-placeholder">
{icon('calendar', 'ic')}
<p>No time selected yet.<br>Pick a day and a time to continue.</p>
<div class="info-meta">
<div class="row">{icon('clock', 'ic')}<span>{_esc(duration_text)}</span></div>
<div class="row">{icon('video', 'ic')}<span>Online meeting</span></div>
</div>
</div>
<div id="details-form-wrap" hidden>
<div class="summary-card">{icon('clock', 'ic')}<span id="selection-summary" aria-live="polite">No time selected yet.</span></div>
<form id="details-form" novalidate>
<div class="field"><label for="f-name">Name</label>
<input id="f-name" autocomplete="name" required><span class="error" id="err-name" role="alert"></span></div>
<div class="field"><label for="f-email">Email</label>
<input id="f-email" type="email" autocomplete="email" required><span class="error" id="err-email" role="alert"></span></div>
<div class="field"><label for="f-notes">Purpose / agenda</label>
<textarea id="f-notes" rows="3"></textarea><span class="error" id="err-notes" role="alert"></span></div>
{''.join(questions)}
<p class="form-note">Availability is confirmed on submission — a selected slot is not held while you type.</p>
<div class="form-actions">
<button type="submit" class="btn" id="confirm-btn">Confirm booking</button>
<button type="button" class="btn secondary" id="back-to-times">Back</button>
</div>
</form>
</div>
</div>
</section>
</div>
</div>"""
    return http.html_response(200, shell(f"{item['title']} · {host_name}", body,
                                         scripts=("booking.js",), brand_name=host_name,
                                         tz_note=f"Times shown in {valid_viewer_tz}"))


def _human_when(booking):
    start = booking.get("start_iso", "")
    end = booking.get("end_iso", "")
    tz = booking.get("display_tz", "UTC")
    try:
        # Stored instants are UTC; the label names display_tz, so the wall
        # clock must actually be in that zone or the label lies by the offset.
        zone = ZoneInfo(tz) if is_valid_zone(tz) else ZoneInfo("UTC")
        s = datetime.fromisoformat(start).astimezone(zone)
        e = datetime.fromisoformat(end).astimezone(zone)
        return f"{s:%A, %d %B %Y}, {s:%H:%M} – {e:%H:%M} ({_esc(tz)})"
    except (TypeError, ValueError):
        return f"{_esc(start)} – {_esc(end)} ({_esc(tz)})"


def manage_page(*, booking, token, ics_url, durations, event_title="", host_name=""):
    status = booking.get("status", "")
    pending = booking.get("pending_action", "")
    headline = {"confirmed": "Booking confirmed", "canceled": "Booking canceled"}.get(status, "Booking")
    if pending == "cancel":
        headline += " — cancellation pending"
    what_bits = []
    if event_title:
        what_bits.append(event_title)
    if host_name:
        what = duration_label(booking.get("duration_min") or 30) + " with " + host_name
        what_bits.append(what)
    duration_options = "".join(
        f"<option value=\"{m}\"{' selected' if m == booking.get('duration_min') else ''}>"
        f"{_esc(duration_label(m))}</option>" for m in durations)
    reschedule = ""
    display_tz = booking.get("display_tz", "UTC")
    tz_ok = is_valid_zone(display_tz)
    if status == "confirmed":
        start_value = ""
        try:
            start_dt = datetime.fromisoformat(booking["start_iso"])
            # Pre-fill the picker in the booking's display zone; raw UTC here
            # would silently shift the meeting when saved untouched.
            if tz_ok:
                start_dt = start_dt.astimezone(ZoneInfo(display_tz))
            start_value = start_dt.strftime("%Y-%m-%dT%H:%M")
        except (TypeError, ValueError, KeyError):
            start_value = ""
        tz_label = display_tz if tz_ok else "UTC"
        reschedule = f"""<div class="panel action-card">
<h2>{icon('calendar', 'ic')}Reschedule</h2>
<!-- novalidate: min/required are enforced in manage.js so the failure speaks
     through the styled status line, not a native bubble. -->
<form id="reschedule-form" novalidate>
<div class="field"><label for="resched-start">New start</label>
<input id="resched-start" type="datetime-local" value="{_esc(start_value)}" required>
<span class="hint">Pick any exact time; shown in {_esc(tz_label)}.</span>
<span class="hint" id="resched-preview" aria-live="polite"></span></div>
<div class="field"><label for="resched-duration">Duration</label>
<select id="resched-duration">{duration_options}</select></div>
<div class="form-actions"><button type="submit" class="btn">Reschedule</button></div>
</form>
</div>"""
    cancel = ""
    if status == "confirmed":
        cancel = f"""<div class="panel action-card danger-card">
<h2>{icon('x', 'ic')}Cancel</h2>
<form id="cancel-form">
<div class="field"><label for="cancel-reason">Reason (optional)</label>
<input id="cancel-reason" maxlength="500"></div>
<div class="form-actions" id="cancel-actions">
<button type="submit" class="btn danger" id="cancel-btn">Cancel booking</button>
</div>
<div class="form-actions" id="cancel-confirm" hidden>
<span class="confirm-text">Really cancel this meeting?</span>
<button type="button" class="btn danger confirm">Yes, cancel it</button>
<button type="button" class="btn secondary keep">Keep booking</button>
</div>
</form>
</div>"""
    joining = (booking.get("conference", {}) or {}).get("link", "") or "See your calendar invitation."
    if (booking.get("conference", {}) or {}).get("status") == "pending":
        joining = "Joining details are being prepared."
    joining_html = (f"<a class=\"join-link\" href=\"{_esc(joining)}\">{_esc(joining)}</a>"
                    if str(joining).startswith("http") else _esc(joining))
    cfg = {"token": token, "apiBase": "/api/v1",
           "revision": booking.get("revision", 0), "duration": booking.get("duration_min", 30),
           "timezone": display_tz if tz_ok else "UTC"}
    body = f"""<div id="manage-root" data-config='{json.dumps(cfg)}'>
<div class="narrow">
<div class="panel manage-panel">
<h1>{_esc(headline)}</h1>
{'<p class="manage-sub">' + _esc(" · ".join(what_bits)) + "</p>" if what_bits else ""}
<div class="manage-summary">
<div class="row-line"><span class="label">Reference</span><span class="ref-chip">{_esc(booking.get('reference', ''))}</span></div>
<div class="row-line"><span class="label">When</span><span>{_human_when(booking)}</span></div>
<div class="row-line"><span class="label">Joining</span><span>{joining_html}</span></div>
</div>
<p><a class="btn secondary sm" href="{_esc(ics_url)}">{icon('download', 'ic')} Add to calendar (.ics)</a></p>
{_status_box('manage-status', 'Opening this page changes nothing. Canceling or rescheduling needs the explicit action below.')}
</div>
{reschedule}
{cancel}
</div>
</div>"""
    return http.html_response(200, shell(headline, body, scripts=("manage.js",),
                                         brand_name=host_name or None,
                                         tz_note=f"Times shown in {display_tz if tz_ok else 'UTC'}"))


def receipt_page(*, operation, booking=None, host_name="", ics_url=""):
    state = operation.get("state", "")
    if state == "succeeded" and booking:
        # The operation receipt names its instants start/end while the store
        # row calls them start_iso/end_iso; accept both so the label can
        # never render as a bare dash pair.
        when = {**booking,
                "start_iso": booking.get("start_iso") or booking.get("start", ""),
                "end_iso": booking.get("end_iso") or booking.get("end", "")}
        what_bits = []
        if booking.get("title"):
            what_bits.append(str(booking["title"]) +
                             (f" with {host_name}" if host_name else ""))
        if booking.get("duration_min"):
            what_bits.append(duration_label(int(booking["duration_min"])))
        what_line = (f"<p class=\"detail-line\">{_esc(' · '.join(what_bits))}</p>"
                     if what_bits else "")
        body_inner = (f"<p class=\"detail-line\">{_human_when(when)}</p>"
                      f"<p>The confirmation email carries your management link.</p>")
        # The management link itself cannot be rebuilt here (tokens are only
        # ever stored hashed), but the calendar file and the way back can.
        actions = (f"<div class=\"form-actions\">"
                   f"<a class=\"btn\" href=\"{_esc(ics_url)}\">{icon('download', 'ic')} "
                   f"Add to calendar (.ics)</a>"
                   f"<a class=\"btn secondary\" href=\"/\">Book another time</a></div>"
                   if ics_url else "")
        body = (f"<div class=\"centerpiece panel\">"
                f"<span class=\"status-orb ok\">{icon('check', 'ic')}</span>"
                f"<h1>Booking confirmed</h1>"
                f"<p>Reference <span class=\"ref-chip\">{_esc(booking.get('reference', ''))}</span></p>"
                f"{what_line}"
                f"{body_inner}"
                f"{actions}</div>")
        return http.html_response(200, shell("Booking confirmed", body,
                                             main_class="centered",
                                             brand_name=host_name or None,
                                             tz_note=f"Times shown in "
                                                     f"{booking.get('display_tz', 'UTC')}"))
    if state in ("in_progress", "unknown"):
        body = (f"<div class=\"centerpiece panel\">"
                f"<span class=\"status-orb pending\">{icon('clock', 'ic')}</span>"
                f"<h1>Booking is being reconciled</h1>"
                f"<p>The calendar write has an unknown outcome. Your time is protected; "
                f"do not book again. This page reflects the outcome once known.</p></div>")
        return http.html_response(200, shell("Booking pending", body,
                                             main_class="centered"))
    return notice("Booking failed", "The booking could not be completed. Please try again.",
                  status=502, link=("Back to booking", "/"))


def admin_shell(email):
    side = (f"<span class=\"whoami\">Signed in as <strong>{_esc(email)}</strong></span>"
            f"<a href=\"/auth/logout\">Sign out</a>")
    body = f"""<div class="admin-head"><h1>Scheduler admin</h1></div>
<nav class="tabs" aria-label="Admin sections"><button data-tab="overview" aria-pressed="true">Overview</button>
<button data-tab="types" aria-pressed="false">Event types</button>
<button data-tab="bookings" aria-pressed="false">Bookings</button>
<button data-tab="settings" aria-pressed="false">Settings</button></nav>
<section class="admin-section" id="overview"></section>
<section class="admin-section" id="types" hidden></section>
<section class="admin-section" id="bookings" hidden></section>
<section class="admin-section" id="settings" hidden></section>
<div id="admin-root" data-config='{json.dumps({"zones": list(COMMON_ZONES)})}'></div>"""
    return http.html_response(200, shell("Admin", body, scripts=("admin.js",),
                                         head_side=side,
                                         tz_note="Times shown in the Settings timezone"))
