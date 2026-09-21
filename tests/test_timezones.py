"""Visitor timezone inference: the browser sends its zone, the server honors
it for first paint and API defaults, and untrusted values fall back quietly."""

import json

import public_handler
from scheduler.timezones import is_valid_zone, viewer_timezone
from tests.test_handlers import call, live  # noqa: F401  (fixture reuse)

import pytest


def event(query=None, cookies=()):
    return {"queryStringParameters": query or {}, "cookies": list(cookies)}


def test_only_real_zones_are_honored():
    assert is_valid_zone("America/New_York") is True
    assert is_valid_zone("Not/AZone") is False
    assert is_valid_zone('"><script>alert(1)</script>') is False
    assert is_valid_zone(None) is False


def test_explicit_param_beats_cookie_beats_default():
    assert viewer_timezone(event({"tz": "Asia/Tokyo"}, ["sched_tz=UTC"])) == "Asia/Tokyo"
    assert viewer_timezone(event({}, ["sched_tz=America/New_York"])) == "America/New_York"
    assert viewer_timezone(event({"tz": "Bogus"}, ["sched_tz=AlsoBogus"])) == "UTC"
    assert viewer_timezone(event()) == "UTC"


def test_booking_page_preselects_the_browser_zone(live):
    page = call(public_handler.lambda_handler, "/dtc",
                cookies=["sched_tz=America/New_York"]).body
    assert '<option value="America/New_York" selected>' in page
    assert '"defaultTimezone": "America/New_York"' in page


def test_booking_page_falls_back_quietly_on_garbage_cookie(live):
    page = call(public_handler.lambda_handler, "/dtc",
                cookies=['sched_tz="><script>alert(1)</script>']).body
    assert "<script>alert(1)</script>" not in page
    assert '<option value="UTC" selected>' in page


def test_availability_defaults_to_the_cookie_zone(live):
    res = call(public_handler.lambda_handler, "/api/v1/availability", query={
        "type": "dtc", "duration": "30", "from": "2026-10-06", "to": "2026-10-06"},
        cookies=["sched_tz=America/New_York"])
    assert res.statusCode == 200
    assert json.loads(res.body)["timezone"] == "America/New_York"


def test_explicit_availability_param_beats_the_cookie(live):
    res = call(public_handler.lambda_handler, "/api/v1/availability", query={
        "type": "dtc", "duration": "30", "from": "2026-10-06", "to": "2026-10-06",
        "tz": "Asia/Tokyo"}, cookies=["sched_tz=America/New_York"])
    assert json.loads(res.body)["timezone"] == "Asia/Tokyo"
