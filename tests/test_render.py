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
