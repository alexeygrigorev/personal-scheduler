"""Receipt page content fidelity: joining details must travel with the
receipt, in the three conference states the store can produce."""
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
    # The banner shouted the same alarm as an assertive alert; once its ask
    # is fulfilled it must step down to a calm, non-alert confirmation —
    # and only then: day counts and load failures are not ours to clobber.
    assert 'includes("just taken")' in pick
    assert 'setStatus("", "Time picked — confirm your details below.")' in pick
    assert 'setStatus("error"' not in pick


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
    assert 0 < taken.find('setStatus("error"', fresh_at), "the branch on fresh slots must precede both texts"
    assert "no open times left in ${monthName}" in taken
    assert "No open times left in ${monthName}" in taken
    assert "try the next month" in taken
    # with no slot to hand focus to, the verdict stays the keyboard anchor
    assert "else verdict.focus({ preventScroll: true });" in taken


