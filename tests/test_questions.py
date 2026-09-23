"""Configurable booking questions: configuration gate, invitee answer
validation, host-facing answer rendering, and the Calendly-mirroring seeds."""

from scheduler import render, store
from scheduler.models import (EventType, format_answers, seed_event_types,
                              validate_invitee, validate_questions)

DTC_QUESTIONS = next(et for et in seed_event_types() if et.id == "dtc-30").questions


def test_seed_mirrors_the_calendly_30min_page():
    """The three questions asked on calendly.com/dtc-alexey/30min, with the
    same required flags: a choice with Other, a required discussion topic,
    and an optional company name."""
    by_id = {q["id"]: q for q in DTC_QUESTIONS}
    talk = by_id["talk-about"]
    assert talk["type"] == "single_choice"
    assert talk["choices"] == ["Meet & greet / networking", "Sponsoring DataTalks.Club",
                               "Corporate training"]
    assert talk["allow_other"] is True and talk["required"] is False
    assert by_id["discuss"]["type"] == "textarea"
    assert by_id["discuss"]["required"] is True
    assert by_id["company"]["type"] == "text"
    assert by_id["company"]["required"] is False


def test_validate_questions_rejects_bad_configuration():
    errors = validate_questions([
        {"id": "a", "label": "Ok?", "type": "text", "max_length": 200},
        {"id": "a", "label": "Duplicate id", "type": "text"},
        {"id": "b", "label": "", "type": "text"},
        {"id": "c", "label": "Bad type", "type": "essay"},
        {"id": "d", "label": "One option", "type": "single_choice", "choices": ["only"]},
        {"id": "e", "label": "No cap", "type": "text", "max_length": 99999},
    ])
    text = "\n".join(errors)
    for fragment in ("question 2", "question 3", "question 4", "question 5", "question 6"):
        assert fragment in text, fragment


def test_clean_configuration_validates():
    assert validate_questions(DTC_QUESTIONS) == []


def test_single_choice_answer_must_be_listed_unless_other_allowed():
    questions = [{"id": "q1", "label": "Pick", "type": "single_choice",
                  "required": True, "max_length": 100, "choices": ["A", "B"]}]
    ok = validate_invitee("Ada", "ada@example.com", questions, {"q1": "A"})
    assert ok == {}
    locked = validate_invitee("Ada", "ada@example.com", questions, {"q1": "something else"})
    assert "q:q1" in locked
    open_ended = [{**questions[0], "allow_other": True}]
    assert validate_invitee("Ada", "ada@example.com", open_ended, {"q1": "something else"}) == {}
    missing = validate_invitee("Ada", "ada@example.com", questions, {})
    assert missing == {"q:q1": "'Pick' is required."}


def test_format_answers_labels_with_question_text_and_skips_empty():
    text = format_answers(DTC_QUESTIONS,
                          {"talk-about": "Corporate training", "discuss": "",
                           "company": "Acme"}, notes="hello")
    lines = text.split("\n")
    assert lines[0] == "Agenda: hello"
    assert "What would you like to talk about?: Corporate training" in lines
    assert "Company name: Acme" in lines
    assert not any(line.startswith("What would you like to discuss") for line in lines)


def test_booking_page_renders_a_choice_group_with_other():
    item = {"slug": "dtc", "title": "Chat", "duration_mode": "fixed",
            "fixed_duration_min": 30, "questions": DTC_QUESTIONS}
    response = render.booking_page(item, "Alexey")
    body = response["body"]
    assert 'data-choice-group="talk-about"' in body
    assert 'value="Sponsoring DataTalks.Club" data-question="talk-about"' in body
    assert 'data-other-radio' in body and 'data-other-input' in body
    assert 'data-question="discuss"' in body and "<textarea" in body
    assert 'data-question="company"' in body


def test_manage_page_shows_the_invitee_their_answers():
    booking = {"status": "confirmed", "revision": 1, "duration_min": 30,
               "start_iso": "2026-10-06T09:00:00+02:00", "end_iso": "2026-10-06T09:30:00+02:00",
               "reference": "AB12-CD34", "answers": {"talk-about": "Sponsoring DataTalks.Club",
                                                      "company": ""}}
    response = render.manage_page(booking=booking, token="tok", ics_url="/ics",
                                  durations=[30], questions=DTC_QUESTIONS)
    assert "Your answers" in response["body"]
    assert "Sponsoring DataTalks.Club" in response["body"]
    # An empty answer is not displayed as if it were given.
    assert "Acme" not in response["body"]


def test_manage_page_a_canceled_booking_offers_no_actions():
    # A canceled meeting has no join link worth clicking and no calendar
    # file worth downloading (a fresh .ics would re-add it as confirmed),
    # and the closing note must not point at actions that do not exist.
    booking = {"status": "canceled", "pending_action": "", "revision": 2,
               "duration_min": 30,
               "start_iso": "2026-10-06T09:00:00+02:00", "end_iso": "2026-10-06T09:30:00+02:00",
               "reference": "AB12-CD34", "answers": {},
               "conference": {"status": "ready", "link": "https://meet.example.com/gone"}}
    body = render.manage_page(booking=booking, token="tok", ics_url="/ics",
                              durations=[30], questions=[])["body"]
    assert "Booking canceled" in body
    assert "Joining" not in body and "meet.example.com" not in body
    assert "Add to calendar" not in body
    assert "needs the explicit action below" not in body
    assert "This booking is canceled" in body


def test_manage_page_a_pending_cancellation_is_not_rearmed():
    # While a cancellation is in flight the badge says so; an armed Cancel
    # button underneath would invite the exact second click the state rules
    # out. The card explains instead, and rescheduling stays visible (the
    # server refuses it while the cancellation is unresolved).
    booking = {"status": "confirmed", "pending_action": "cancel", "revision": 1,
               "duration_min": 30,
               "start_iso": "2026-10-06T09:00:00+02:00", "end_iso": "2026-10-06T09:30:00+02:00",
               "reference": "AB12-CD34", "answers": {},
               "conference": {"status": "ready", "link": "https://meet.example.com/x"}}
    body = render.manage_page(booking=booking, token="tok", ics_url="/ics",
                              durations=[30], questions=[])["body"]
    assert "Cancellation pending" in body
    assert 'id="cancel-form"' not in body
    assert "A cancellation is being processed" in body
    assert 'id="reschedule-form"' in body


def test_admin_rejects_an_invalid_question_configuration(table):
    store.ensure_seed()
    item = store.get_event_type("dtc-30")
    broken = [{**DTC_QUESTIONS[0], "choices": ["only one"]}]
    try:
        store.put_event_type({**item, "questions": broken})
        raised = False
    except ValueError:
        raised = True
    assert raised
