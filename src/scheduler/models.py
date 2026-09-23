"""Domain models and seed data. The spec is the behavioral contract; storage
details (DynamoDB single-table layout) live in store.py."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

ALLOWED_FLEXIBLE_DURATIONS = (30, 60, 90, 120, 150, 180)
MAX_DURATION = 180

VISIBILITIES = ("listed", "unlisted", "disabled")
DURATION_MODES = ("fixed", "selectable")
BOOKING_STATUSES = ("pending_confirmation", "confirmed", "canceled", "failed")
OPERATION_KINDS = ("create", "reschedule", "cancel")
OPERATION_STATES = ("not_attempted", "in_progress", "unknown", "succeeded", "failed")
LOCATION_MODES = ("fixed_text", "fixed_url", "auto_meet", "provided_later")
QUESTION_TYPES = ("text", "textarea", "single_choice")
MAX_QUESTION_ANSWER = 5000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class EventType:
    id: str
    slug: str
    title: str
    description: str = ""
    duration_mode: str = "fixed"
    fixed_duration_min: int = 30
    allowed_durations: list = field(default_factory=list)
    visibility: str = "listed"
    schedule_id: str = "default"
    pre_buffer_min: int = 0
    post_buffer_min: int = 0
    grid_min: int | None = None
    notice_hours: float | None = None
    horizon_days: int | None = None
    daily_count_limit: int | None = None
    daily_minutes_limit: int | None = None
    location_mode: str = "fixed_text"
    location_text: str = ""
    require_agenda: bool = False
    questions: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    version: int = 1
    position: int = 0

    def validate(self) -> list[str]:
        errors = []
        if self.visibility not in VISIBILITIES:
            errors.append("invalid visibility")
        if self.duration_mode not in DURATION_MODES:
            errors.append("invalid duration_mode")
        if self.duration_mode == "fixed":
            if self.fixed_duration_min <= 0 or self.fixed_duration_min > MAX_DURATION:
                errors.append("unsupported fixed duration")
        elif not self.allowed_durations:
            errors.append("selectable type needs allowed_durations")
        else:
            for duration in self.allowed_durations:
                if duration not in ALLOWED_FLEXIBLE_DURATIONS:
                    errors.append(f"unsupported duration {duration}")
        if not self.slug or "/" in self.slug or " " in self.slug:
            errors.append("invalid slug")
        if self.location_mode not in LOCATION_MODES:
            errors.append("invalid location_mode")
        errors.extend(validate_questions(self.questions))
        return errors

    def durations_offered(self) -> list[int]:
        if self.duration_mode == "fixed":
            return [self.fixed_duration_min]
        return sorted(self.allowed_durations)

    def is_bookable(self) -> bool:
        return self.visibility in ("listed", "unlisted")

    def snapshot(self) -> dict:
        return {
            "event_type_id": self.id,
            "version": self.version,
            "title": self.title,
            "duration_mode": self.duration_mode,
            "pre_buffer_min": self.pre_buffer_min,
            "post_buffer_min": self.post_buffer_min,
        }

    def to_item(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "duration_mode": self.duration_mode,
            "fixed_duration_min": self.fixed_duration_min,
            "allowed_durations": list(self.allowed_durations),
            "visibility": self.visibility,
            "schedule_id": self.schedule_id,
            "pre_buffer_min": self.pre_buffer_min,
            "post_buffer_min": self.post_buffer_min,
            "grid_min": self.grid_min,
            "notice_hours": self.notice_hours,
            "horizon_days": self.horizon_days,
            "daily_count_limit": self.daily_count_limit,
            "daily_minutes_limit": self.daily_minutes_limit,
            "location_mode": self.location_mode,
            "location_text": self.location_text,
            "require_agenda": self.require_agenda,
            "questions": list(self.questions),
            "aliases": list(self.aliases),
            "version": self.version,
            "position": self.position,
        }

    @staticmethod
    def from_item(item: dict) -> "EventType":
        data = {k: item.get(k) for k in EventType.__dataclass_fields__ if k in item}
        data.setdefault("allowed_durations", [])
        data.setdefault("questions", [])
        data.setdefault("aliases", [])
        return EventType(**data)


def seed_event_types() -> list[EventType]:
    return [
        EventType(
            id="community-30", slug="community",
            title="AI Shipping Labs — Community Call",
            description="Community call with Alexey.",
            duration_mode="fixed", fixed_duration_min=30,
            visibility="unlisted", schedule_id="default", position=1,
        ),
        EventType(
            id="dtc-30", slug="dtc",
            title="DataTalks.Club — General Chat",
            description="General chat.",
            duration_mode="fixed", fixed_duration_min=30,
            visibility="listed", schedule_id="default", position=2,
            # Mirrors the questions the host asks on the Calendly 30-minute
            # page this link replaces; editable in the admin console.
            questions=[
                {"id": "talk-about", "label": "What would you like to talk about?",
                 "type": "single_choice", "required": False, "max_length": 500,
                 "choices": ["Meet & greet / networking", "Sponsoring DataTalks.Club",
                             "Corporate training"],
                 "allow_other": True},
                {"id": "discuss", "label": "What would you like to discuss?",
                 "type": "textarea", "required": True, "max_length": 2000},
                {"id": "company", "label": "Company name",
                 "type": "text", "required": False, "max_length": 200},
            ],
        ),
        EventType(
            id="general-60", slug="60min",
            title="Meeting with Alexey",
            description="One-hour meeting.",
            duration_mode="fixed", fixed_duration_min=60,
            visibility="listed", schedule_id="default", position=3,
        ),
        EventType(
            id="flexible", slug="extended",
            title="Extended Meeting / Podcast",
            description="Longer sessions including podcast recordings.",
            duration_mode="selectable",
            allowed_durations=[30, 60, 90, 120, 150, 180],
            visibility="unlisted", schedule_id="default",
            require_agenda=True, position=4,
        ),
    ]


def seed_schedule() -> dict:
    # Empty weekly windows: the host explicitly configures working hours
    # before any availability is published. Nothing is bookable until then.
    return {
        "id": "default",
        "name": "Default",
        "timezone": "Europe/Berlin",
        "weekly_windows": {},
        "overrides": {},
        "version": 1,
    }


def validate_duration(event_type: EventType, duration_min) -> tuple[bool, str]:
    """Server-side duration gate: arbitrary, zero, negative, and >180 values
    are rejected; fixed types accept only their duration."""
    try:
        duration = int(duration_min)
    except (TypeError, ValueError):
        return False, "unsupported duration"
    if duration <= 0 or duration > MAX_DURATION:
        return False, "unsupported duration"
    if event_type.duration_mode == "fixed":
        if duration != event_type.fixed_duration_min:
            return False, "unsupported duration"
    elif duration not in ALLOWED_FLEXIBLE_DURATIONS or duration not in event_type.allowed_durations:
        return False, "unsupported duration"
    return True, ""


def validate_questions(questions) -> list[str]:
    """Config-side gate for invitee questions: a bad config must fail the
    admin save, not surface as a broken booking form."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    for position, question in enumerate(questions or [], start=1):
        where = f"question {position}"
        if not isinstance(question, dict):
            errors.append(f"{where}: malformed")
            continue
        qtype = question.get("type", "text")
        if qtype not in QUESTION_TYPES:
            errors.append(f"{where}: unknown type '{qtype}'")
        label = str(question.get("label", "")).strip()
        if not label:
            errors.append(f"{where}: label is required")
        qid = str(question.get("id", "")).strip()
        if not qid or qid in seen_ids:
            errors.append(f"{where}: duplicate or missing id")
        seen_ids.add(qid)
        try:
            limit = int(question.get("max_length", 2000))
        except (TypeError, ValueError):
            limit = 0
        if not 1 <= limit <= MAX_QUESTION_ANSWER:
            errors.append(f"{where}: max_length must be 1–{MAX_QUESTION_ANSWER}")
        if qtype == "single_choice":
            choices = [str(c).strip() for c in question.get("choices", []) if str(c).strip()]
            if len(choices) < 2:
                errors.append(f"{where}: a single-choice question needs at least two options")
    return errors


def validate_invitee(name, email, questions, answers, require_agenda=False, notes=""):
    errors: dict[str, str] = {}
    if not (name or "").strip():
        errors["name"] = "Name is required."
    elif len(name.strip()) > 200:
        errors["name"] = "Name is too long."
    email = (email or "").strip()
    if not email:
        errors["email"] = "Email is required."
    elif "@" not in email or "." not in email or len(email) > 320:
        errors["email"] = "Enter a valid email address."
    if require_agenda and not (notes or "").strip():
        errors["agenda"] = "Purpose / agenda is required for this meeting type."
    for question in questions or []:
        qid = question.get("id", "")
        value = str((answers or {}).get(qid, "") or "").strip()
        label = question.get("label", qid)
        if question.get("required") and not value:
            errors[f"q:{qid}"] = f"'{label}' is required."
            continue
        if not value:
            continue
        if len(value) > int(question.get("max_length", 2000)):
            errors[f"q:{qid}"] = "Answer is too long."
            continue
        if question.get("type", "text") == "single_choice":
            choices = [str(c) for c in question.get("choices", [])]
            if value not in choices and not question.get("allow_other"):
                errors[f"q:{qid}"] = "Pick one of the listed options."
    return errors


def format_answers(questions, answers, notes="") -> str:
    """Invitee-provided context for the host notice and the calendar event
    description (spec 9.2: only appropriate invitee context, never private
    notes or tokens). Unlabeled answers fall back to their question id."""
    lines = []
    notes_text = (notes or "").strip()
    if notes_text:
        lines.append(f"Agenda: {notes_text}")
    labels = {str(q.get("id", "")): str(q.get("label", "") or q.get("id", ""))
              for q in questions or []}
    for key, value in (answers or {}).items():
        text = str(value or "").strip()
        if not text:
            continue
        label = labels.get(str(key), str(key))
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


def canonical_payload(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
