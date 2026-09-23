"""Receipt page content fidelity: joining details must travel with the
receipt, in the three conference states the store can produce."""
import re

from scheduler import render

BOOKING = {
    "reference": "BK-2026-0F41",
    "title": "Career chat",
    "duration_min": 45,
    "start_iso": "2026-09-25T15:00:00+02:00",
    "end_iso": "2026-09-25T15:45:00+02:00",
    "display_tz": "Europe/Berlin",
}


def _receipt(conference):
    booking = {**BOOKING, "conference": conference}
    return render.receipt_page(operation={"state": "succeeded"}, booking=booking,
                               host_name="Alexey Grigorev",
                               ics_url="/receipt/op/ics")


def test_receipt_shows_join_link():
    resp = _receipt({"link": "https://meet.example.com/bk-0f41"})
    assert resp["statusCode"] == 200
    assert 'Joining: <a class="join-link" href="https://meet.example.com/bk-0f41">' in resp["body"]


def test_receipt_pending_conference_explains_itself():
    resp = _receipt({"status": "pending"})
    assert "Joining details are being prepared." in resp["body"]


def test_receipt_without_conference_points_at_the_invite():
    resp = _receipt({})
    assert "See your calendar invitation." in resp["body"]
    assert "join-link" not in resp["body"]


def test_receipt_pending_reloads_itself():
    # A pending receipt promises to reflect the outcome; it can only keep
    # that promise by rechecking until the operation settles.
    resp = render.receipt_page(operation={"state": "in_progress"})
    assert resp["statusCode"] == 200
    assert 'http-equiv="refresh"' in resp["body"]


def test_receipt_settled_does_not_reload():
    resp = _receipt({"link": "https://meet.example.com/bk-0f41"})
    assert 'http-equiv="refresh"' not in resp["body"]


def test_receipt_offers_print():
    # The receipt is the artifact invitees archive: printing gets a labeled
    # control instead of a hidden Ctrl+P.
    resp = _receipt({"link": "https://meet.example.com/bk-0f41"})
    assert "window.print()" in resp["body"]
    assert 'class="btn secondary"' in resp["body"]


def test_print_stylesheet_strips_the_sheet_to_the_record():
    # The print button promises a clean sheet; the stylesheet has to back
    # that by hiding chrome and action rows on paper.
    css = (render.WEB_DIR / "app.css").read_text()
    assert "@media print" in css
    block = css.split("@media print", 1)[1]
    for selector in (".site-head", ".site-foot", ".form-actions"):
        assert selector in block


def test_dark_scheme_answers_every_color_token():
    # The OS picks the palette, so a color token born in :root but missed by
    # the dark block renders its light value on dark surfaces — invisible
    # text, blinding washes. Every color token must have a dark answer, and
    # the page must declare both schemes so UA widgets follow along.
    css = (render.WEB_DIR / "app.css").read_text()
    assert "color-scheme: light dark" in css
    root = css.split(":root {", 1)[1].split("\n}", 1)[0]
    color_tokens = re.findall(r"^  (--[a-z0-9-]+): (?:#|rgba)", root, re.M)
    assert color_tokens, "no color tokens found in :root"
    dark = css.split("@media only screen and (prefers-color-scheme: dark)", 1)[1]
    dark_root = dark.split(":root {", 1)[1].split("\n  }\n}", 1)[0]
    for token in color_tokens:
        assert re.search(rf"^\s*{re.escape(token)}:", dark_root, re.M), \
            f"{token} has no dark value"
    # White labels sit on --accent-fill; the fixed light-theme fill is what
    # keeps them at 4.5:1, so the dark block must not lighten it.
    assert "--accent-fill: #4f46e5;" in root
    assert "--accent-fill: #4f46e5;" in dark_root


def test_dark_palette_stays_off_paper():
    # The print sheet is the record on white paper: if the dark token block
    # also matched print media, panels would print as near-black slabs with
    # the title lost in them. The palette swap answers screens only.
    css = (render.WEB_DIR / "app.css").read_text()
    dark = [q for q in re.findall(r"@media[^{]+", css)
            if "prefers-color-scheme: dark" in q]
    assert dark, "dark scheme block missing"
    for query in dark:
        assert "screen" in query, f"dark palette not screen-scoped: {query.strip()!r}"


def _booking_page(questions=()):
    et = {"id": "et-chat", "slug": "career-chat", "title": "Career chat",
          "description": "Bring your questions.", "duration_mode": "selectable",
          "allowed_durations": [30, 45, 60], "fixed_duration_min": 0,
          "questions": list(questions), "visibility": "listed", "version": 1}
    return render.booking_page(et, "Alexey Grigorev", "Europe/Berlin")["body"]


def test_long_question_gets_a_textarea_with_a_counter():
    # A 500-character answer is unreadable in a one-line input: only the
    # tail stays visible, so long prompts must give room to review.
    html = _booking_page([{"id": "goal", "label": "What would you like to focus on?",
                           "required": True, "max_length": 500}])
    assert ('<textarea id="q-goal" data-question="goal" rows="3" '
            'maxlength="500" required '
            'aria-describedby="err-q-goal"></textarea>') in html
    assert '<span class="char-count" aria-live="polite"></span>' in html
    # Required reads the same on every control: the red star sits on the
    # label, hidden from assistive tech that already knows `required`. The
    # space before it may be a plain or non-breaking one.
    label = html.split('<label for="q-goal">')[1].split("</label>")[0]
    assert ('<span class="req" aria-hidden="true"> *</span>' in label
            or '<span class="req" aria-hidden="true">&nbsp;*</span>' in label)


def test_short_question_stays_a_single_line_input_without_counter():
    html = _booking_page([{"id": "ref", "label": "Referral code",
                           "required": False, "max_length": 60}])
    assert ('<input id="q-ref" data-question="ref" maxlength="60" '
            'aria-describedby="err-q-ref">') in html
    field = html.split('<label for="q-ref">')[1].split("</div>")[0]
    assert "char-count" not in field
    assert "req" not in field


def test_details_fields_carry_the_server_caps():
    # The server rejects names over 200 and emails over 320, and the notes
    # only travel into the calendar invite up to 2000 — cap them at the
    # keyboard instead of at submit time.
    html = _booking_page()
    assert 'id="f-name" autocomplete="name" maxlength="200" required' in html
    assert 'id="f-email" type="email" autocomplete="email" maxlength="320" required' in html
    # Every details field points at its own error slot, notes included —
    # a verdict announced only by role=alert still needs the association
    # for screen readers that list a field's description on focus.
    assert '<textarea id="f-notes" rows="3" maxlength="2000" aria-describedby="err-notes">' in html
    assert ('<span class="char-count" aria-live="polite"></span>'
            '<span class="error" id="err-notes"') in html


def test_picking_a_fresh_slot_retires_the_taken_verdict():
    # The taken-slot verdict asks for one pick; once that pick lands, the
    # message must not stay above the times and read as if the new slot
    # were suspect too. The removal lives in the slot's own click handler,
    # not in the re-render, so day switches keep the guidance until a
    # choice is actually made.
    js = (render.WEB_DIR / "booking.js").read_text()
    pick = js.split("state.selectedEnd = slot.end;", 1)[1].split("});", 1)[0]
    assert 'getElementById("times-verdict")' in pick
    assert ".remove()" in pick
    # The banners shouted the same alarm as assertive alerts; once their ask
    # is fulfilled they must step down to a calm, non-alert confirmation —
    # and only then: day counts, load failures, and a reconciling booking's
    # do-not-rebook warning are not ours to clobber. Both stale alarms go —
    # the taken verdict's ask is fulfilled by the pick, and a "could not
    # confirm" error describes the previous submission, not the fresh one.
    assert 'includes("just taken")' in pick
    assert 'startsWith("Could not confirm")' in pick
    assert 'setStatus("", "Time picked — confirm your details below.")' in pick
    assert 'setStatus("error"' not in pick


def test_confirming_without_a_pick_never_reaches_the_server():
    # A 409 or a day switch can leave the revealed form with no picked slot,
    # and Confirm used to POST the empty start anyway — a round trip that
    # could only come back as a vague server 400 about the start time. The
    # pre-flight speaks the same one-sentence grammar as the taken banner,
    # branches the ask on what the page can actually offer (this day's
    # slots, another day with open times, or the next month when none do),
    # and hands the keyboard to the pick that still exists.
    js = (render.WEB_DIR / "booking.js").read_text()
    guard = js.split("async function submitBooking", 1)[1].split("const payload", 1)[0]
    assert "if (!state.selectedStart)" in guard
    assert "Pick a time below." in guard
    assert "Pick a day with open times in the calendar." in guard
    assert "No open times left in ${monthName}" in guard
    # the verdict reuses the banner's exact sentence — one shared string,
    # so the two can never drift into two different problems
    assert guard.count("No time is picked yet — your details are kept. ${ask}") == 1
    assert 'verdict.textContent = say;' in guard
    # the guard sits before the payload build: a start-less submit never
    # reaches the fetch
    body = js.split("async function submitBooking", 1)[1]
    assert body.index("No time is picked yet") < body.index("fetch(")
    # and a fresh pick retires this banner too, not just the taken one
    pick = js.split("state.selectedEnd = slot.end;", 1)[1].split("});", 1)[0]
    assert 'includes("No time is picked yet")' in pick


def test_taken_slot_verdict_promises_only_what_remains():
    # A 409 reload that still has slots may say "pick a new time below";
    # one that comes back empty must not — nothing is below, and an
    # instruction nobody can follow is the loudest thing on the page. The
    # empty branch names the exhausted month and the next-month arrow
    # (still in the visible head) as the way out instead.
    js = (render.WEB_DIR / "booking.js").read_text()
    taken = js.split('data.error.code === "slot_unavailable"', 1)[1].split("if (!res.ok)", 1)[0]
    fresh_at = taken.find('querySelector("#time-list button")')
    assert fresh_at != -1
    # the ask is branched on what the reload found, before either banner
    # speaks it
    assert 0 < taken.find('const ask = fresh', fresh_at)
    assert "Pick a new time below." in taken
    assert "No open times left in ${monthName}" in taken
    assert "try the next month" in taken
    # both banners speak the one sentence; neither owns a private wording
    assert taken.count("That time was just taken — your details are kept. ${ask}") == 2
    # with no slot to hand focus to, the verdict stays the keyboard anchor
    assert "else verdict.focus({ preventScroll: true });" in taken




def test_taken_banner_speaks_once_in_one_voice():
    # On a tall screen the status banner and the times verdict stack in one
    # view; two wordings of the same ask read like two different problems.
    # Whatever the reload found — fresh slots or an exhausted month — every
    # sentence the status banner can speak, the verdict must speak in
    # exactly the same words.
    js = (render.WEB_DIR / "booking.js").read_text()
    taken = js.split('data.error.code === "slot_unavailable"', 1)[1]
    taken = taken.split("if (!res.ok)", 1)[0]
    texts = []
    for line in taken.splitlines():
        if "That time was just taken" not in line:
            continue
        tail = line.split("That time was just taken", 1)[1]
        quote = '"' if '"' in tail else "`"
        texts.append(tail.split(quote, 1)[0])
    assert len(texts) >= 2 and len(texts) % 2 == 0
    # The block speaks the banner's sentences first, then the verdict's —
    # arm for arm, in the same order. Half against half must match exactly.
    half = len(texts) // 2
    assert texts[:half] == texts[half:]


def test_autofilled_other_text_opens_the_reveal():
    # Browsers autofill — and visitors paste — into the Other input without
    # clicking its radio; the reveal must follow the group's state through
    # the input event, and a filler that fires only change must not strand
    # a checked Other with its answer invisible. Every driver goes through
    # one sync, so the class can never disagree with the radio.
    js = (render.WEB_DIR / "booking.js").read_text()
    input_listener = js.split('details-form").addEventListener("input"', 1)[1]
    assert "syncOther(field)" in input_listener
    assert 'if (radio) radio.checked = true;' in input_listener
    change_listener = js.split('details-form").addEventListener("change"', 1)[1]
    assert "data-other-input" in change_listener.split('input[type=radio]', 1)[0]
    assert js.count('classList.toggle("other-open"') == 1
def test_month_jumps_hand_the_keyboard_to_the_grid():
    # Arrow navigation and the next-day month jump disable the control that
    # was clicked and rebuild the grid on arrival; without a handback a
    # keyboard visitor lands on <body> exactly where they should continue.
    js = (render.WEB_DIR / "booking.js").read_text()
    # retry path, two month arrows, both next-day jump branches
    assert js.count("loadAvailability().then(focusAfterJump)") == 5
    handback = js.split("function focusAfterJump()", 1)[1].split("\n  }", 1)[0]
    # the landing spot prefers the Retry button, then the picked (or first
    # bookable) day, then the empty-month guidance
    assert handback.index(".empty-cell.with-action button") < handback.index('button[aria-pressed="true"]')
    assert handback.index('button[aria-pressed="true"]') < handback.index("#day-grid .empty-cell")
    assert "focus({ preventScroll: true })" in handback

def test_month_jumps_hand_the_keyboard_to_the_grid():
    # Arrow navigation and the next-day month jump disable the control that
    # was clicked and rebuild the grid on arrival; without a handback a
    # keyboard visitor lands on <body> exactly where they should continue.
    js = (render.WEB_DIR / "booking.js").read_text()
    # retry path, two month arrows, both next-day jump branches
    assert js.count("loadAvailability().then(focusAfterJump)") == 5
    handback = js.split("function focusAfterJump()", 1)[1].split("\n  }", 1)[0]
    # the landing spot prefers the Retry button, then the picked (or first
    # bookable) day, then the empty-month guidance
    assert handback.index(".empty-cell.with-action button") < handback.index('button[aria-pressed="true"]')
    assert handback.index('button[aria-pressed="true"]') < handback.index("#day-grid .empty-cell")
    assert "focus({ preventScroll: true })" in handback
