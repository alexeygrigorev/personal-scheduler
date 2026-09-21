"""Availability engine acceptance coverage (spec sections 4, 6, 16.1)."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from scheduler import availability as av
from scheduler.models import EventType, seed_event_types, validate_duration

BERLIN = ZoneInfo("Europe/Berlin")
DAY = date(2026, 10, 6)  # a Tuesday


def host(**overrides):
    base = {"default_grid_min": 30, "default_notice_hours": 0,
            "default_horizon_days": 60, "global_daily_count_limit": None,
            "global_daily_minutes_limit": None}
    base.update(overrides)
    return base


def schedule(windows, **overrides):
    base = {"id": "default", "timezone": "Europe/Berlin",
            "weekly_windows": windows, "overrides": {}}
    base.update(overrides)
    return base


def fixed(duration, **overrides):
    et = {"id": "t", "duration_mode": "fixed", "fixed_duration_min": duration,
          "allowed_durations": [], "pre_buffer_min": 0, "post_buffer_min": 0,
          "grid_min": None, "notice_hours": None, "horizon_days": None,
          "daily_count_limit": None, "daily_minutes_limit": None}
    et.update(overrides)
    return et


def berlin(day, hhmm):
    hours, minutes = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hours, minutes, tzinfo=BERLIN)


NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def starts_for(duration, **kwargs):
    params = {"schedule": schedule({"1": [["09:00", "12:00"]]}),
              "event_type": fixed(duration), "host": host(), "duration_min": duration,
              "busy": [], "now_utc": NOW, "from_date": DAY, "to_date": DAY}
    params.update(kwargs)
    return av.generate_slots(**params)["slots"]


def test_long_duration_table_matches_spec_section_6_4():
    """A07: single 09:00-12:00 window, 30-minute grid, no buffers."""
    expected = {30: ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"],
                60: ["09:00", "09:30", "10:00", "10:30", "11:00"],
                90: ["09:00", "09:30", "10:00", "10:30"],
                120: ["09:00", "09:30", "10:00"],
                150: ["09:00", "09:30"],
                180: ["09:00"]}
    for duration, wants in expected.items():
        got = [s.start.astimezone(BERLIN).strftime("%H:%M") for s in starts_for(duration)]
        assert got == wants, duration


def test_split_windows_never_support_a_three_hour_booking():
    """A08: 09:00-10:30 plus 11:00-12:30 cannot host 180 minutes."""
    slots = starts_for(180, schedule=schedule({"1": [["09:00", "10:30"], ["11:00", "12:30"]]}))
    assert slots == []


def test_busy_event_partway_through_rejects_the_long_start():
    """A09: a 30-minute-free start is not a bookable three-hour session."""
    busy = [(berlin(DAY, "10:00"), berlin(DAY, "10:30"))]
    assert starts_for(180, busy=busy) == []
    assert len(starts_for(30, busy=busy)) == 5  # 10:00 start itself is gone


def test_back_to_back_meetings_are_allowed_without_buffers():
    """A10: [11:00, ...) may start exactly when another meeting ends."""
    protected = [(berlin(DAY, "10:00"), berlin(DAY, "10:30"))]
    got = [s.start.astimezone(BERLIN).strftime("%H:%M")
           for s in starts_for(30, local_protected=protected)]
    assert "10:30" in got and "09:30" in got


def test_buffers_are_exclusive_and_add_up():
    """A11: 15-minute post-buffer plus 15-minute pre-buffer needs a 30 gap."""
    existing = [(berlin(DAY, "09:00"), berlin(DAY, "10:15"))]  # 09:00-10:00 + 15 post
    got = [s.start.astimezone(BERLIN).strftime("%H:%M")
           for s in starts_for(30, local_protected=existing,
                               event_type=fixed(30, pre_buffer_min=15))]
    assert "10:15" not in got
    assert got[0] == "10:30"


def test_protected_interval_must_fit_inside_one_window():
    """A12: buffers cannot spill over a lunch break."""
    slots = starts_for(150, event_type=fixed(150, post_buffer_min=30),
                       schedule=schedule({"1": [["09:00", "12:00"], ["14:00", "17:00"]]}))
    got = [s.start.astimezone(BERLIN).strftime("%H:%M") for s in slots]
    assert got == ["09:00", "14:00"]  # 09:30 would push protection past 12:00


def test_override_replaces_weekly_hours_and_closure_wins():
    """A13: date-specific hours apply; a global closure still takes precedence."""
    sched = schedule({"1": [["09:00", "17:00"]]},
                     overrides={DAY.isoformat(): [["14:00", "16:00"]]})
    got = [s.start.astimezone(BERLIN).strftime("%H:%M") for s in starts_for(30, schedule=sched)]
    assert got == ["14:00", "14:30", "15:00", "15:30"]
    closed = [{"kind": "fullday", "full_day_date": DAY.isoformat()}]
    assert starts_for(30, schedule=sched, blocks=closed) == []


def test_overnight_busy_blocks_every_real_overlap():
    """A14: a multi-day busy event blocks intersecting host time."""
    busy = [(datetime(2026, 10, 5, 22, 0, tzinfo=timezone.utc),
             datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc))]  # until 10:00 Berlin
    got = [s.start.astimezone(BERLIN).strftime("%H:%M") for s in starts_for(30, busy=busy)]
    assert got[0] == "10:00"


def test_incomplete_busy_fails_closed():
    """C04: never offer uncertain slots as free."""
    try:
        av.generate_slots(schedule=schedule({"1": [["09:00", "12:00"]]}),
                          event_type=fixed(30), host=host(), duration_min=30,
                          busy=[], now_utc=NOW, incomplete_busy=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected fail-closed RuntimeError")


def test_expired_offer_is_rejected_against_server_time():
    """A17: notice is enforced at submission time, not page-open time."""
    slot = starts_for(30, host=host(default_notice_hours=0))[0].start
    later = slot - timedelta(hours=11)
    params = {"schedule": schedule({"1": [["09:00", "12:00"]]}),
              "event_type": fixed(30), "host": host(default_notice_hours=12),
              "duration_min": 30, "busy": [], "now_utc": later,
              "from_date": DAY, "to_date": DAY}
    assert all(s.start >= later + timedelta(hours=12)
               for s in av.generate_slots(**params)["slots"])
    assert slot not in [s.start for s in av.generate_slots(**params)["slots"]]


def test_global_limits_apply_across_event_types():
    """A18: minutes booked through one link count against every other link."""
    other = {"id": "other", "event_type_id": "dtc-30", "status": "confirmed",
             "start_iso": berlin(DAY, "09:00").isoformat(), "duration_min": 60}
    limited = host(global_daily_minutes_limit=60)
    assert starts_for(30, host=limited, existing_bookings=[other]) == []
    assert starts_for(30, existing_bookings=[other]) != []


def test_spring_forward_gap_has_no_slots_and_fall_back_repeats():
    """A19: nonexistent local times are absent; repeated ones carry offsets."""
    gap_day = date(2026, 3, 29)
    sched = schedule({"6": [["00:00", "05:00"]]})
    got = [s.start.astimezone(BERLIN).strftime("%H:%M")
           for s in starts_for(30, schedule=sched, from_date=gap_day, to_date=gap_day,
                               now_utc=datetime(2026, 3, 28, 12, tzinfo=timezone.utc))]
    assert "02:00" not in got and "02:30" not in got
    assert "01:30" in got and "03:00" in got

    fold_day = date(2026, 10, 25)
    sched = schedule({"6": [["00:00", "05:00"]]})
    slots = starts_for(30, schedule=sched, from_date=fold_day, to_date=fold_day,
                       now_utc=datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
    walls = [s.start.astimezone(BERLIN).strftime("%H:%M%z") for s in slots]
    assert walls.count("02:00+0200") == 1 and walls.count("02:00+0100") == 1


def test_viewer_range_includes_every_intersecting_host_date():
    start, end = av.expand_viewer_range(date(2026, 10, 6), date(2026, 10, 6),
                                        "America/Los_Angeles", "Europe/Berlin")
    assert start <= date(2026, 10, 6) <= end
    assert (end - start).days >= 2


def test_owner_diagnostics_name_the_reason():
    """Spec section 5: the host view distinguishes why a time is unavailable."""
    busy = [(berlin(DAY, "09:00"), berlin(DAY, "12:00"))]
    reasons = av.why_unavailable(
        moment=berlin(DAY, "09:00"), duration_min=30,
        schedule=schedule({"1": [["09:00", "12:00"]]}), event_type=fixed(30),
        host=host(), busy=busy, local_protected=[], blocks=[], now_utc=NOW)
    assert "conflict" in reasons
    reasons = av.why_unavailable(
        moment=berlin(DAY, "09:00"), duration_min=180,
        schedule=schedule({"1": [["09:00", "10:30"], ["11:00", "12:30"]]}),
        event_type=fixed(180), host=host(), busy=[], local_protected=[],
        blocks=[], now_utc=NOW)
    assert "insufficient-uninterrupted-time" in reasons


def test_duration_gate_rejects_manipulated_values():
    """A06: arbitrary, zero, negative, and over-long durations fail server-side."""
    flexible = next(t for t in seed_event_types() if t.id == "flexible")
    for bad in (45, 181, 0, -30, "abc", None):
        assert validate_duration(flexible, bad) == (False, "unsupported duration")
    for good in (30, 60, 90, 120, 150, 180):
        assert validate_duration(flexible, good) == (True, "")
    fixed30 = EventType(id="x", slug="x", title="x", duration_mode="fixed",
                        fixed_duration_min=30)
    assert validate_duration(fixed30, 60) == (False, "unsupported duration")


def test_reschedule_excludes_only_its_own_event():
    """B12: subtracting the whole old interval would erase a sibling conflict."""
    busy = [(berlin(DAY, "09:00"), berlin(DAY, "12:00"))]  # own 09:00-09:30 + other 09:00-12:00
    own = (berlin(DAY, "09:00"), berlin(DAY, "09:30"))
    remaining = av.subtract_own_event(busy, own)
    assert remaining == [(berlin(DAY, "09:30"), berlin(DAY, "12:00"))]
