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
            'maxlength="500" required></textarea>') in html
    assert '<span class="char-count" aria-live="polite"></span>' in html


def test_short_question_stays_a_single_line_input_without_counter():
    html = _booking_page([{"id": "ref", "label": "Referral code",
                           "required": False, "max_length": 60}])
    assert '<input id="q-ref" data-question="ref" maxlength="60">' in html
    field = html.split('<label for="q-ref">')[1].split("</div>")[0]
    assert "char-count" not in field


def test_details_fields_carry_the_server_caps():
    # The server rejects names over 200 and emails over 320, and the notes
    # only travel into the calendar invite up to 2000 — cap them at the
    # keyboard instead of at submit time.
    html = _booking_page()
    assert 'id="f-name" autocomplete="name" maxlength="200" required' in html
    assert 'id="f-email" type="email" autocomplete="email" maxlength="320" required' in html
    assert '<textarea id="f-notes" rows="3" maxlength="2000">' in html
    assert ('<span class="char-count" aria-live="polite"></span>'
            '<span class="error" id="err-notes"') in html
