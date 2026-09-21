"""Visitor timezone inference. HTTP carries no standard timezone header, so
the booking page learns the browser zone through Intl and stores it in a
plain preference cookie; the server then renders the first paint and answers
API defaults in the visitor's own zone instead of flashing Europe/Berlin.

The cookie is untrusted input like everything else: only values that
ZoneInfo accepts are ever honored, everything else falls back quietly.
"""

import urllib.parse
from zoneinfo import ZoneInfo

from . import http

TZ_COOKIE = "sched_tz"
TZ_COOKIE_MAX_AGE = 365 * 24 * 3600

COMMON_ZONES = (
    "Europe/Berlin",
    "UTC",
    "Europe/London",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Asia/Dubai",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
)


def is_valid_zone(name) -> bool:
    try:
        ZoneInfo(str(name or "")[:64])
        return True
    except Exception:
        return False


def viewer_timezone(event, default: str = "UTC") -> str:
    """Explicit `tz` parameter wins when valid; otherwise the browser cookie;
    otherwise the default. Never raises, never echoes anything unvalidated."""
    candidate = (http.query(event).get("tz") or "").strip()
    if candidate and is_valid_zone(candidate):
        return candidate
    raw = http.cookie(event, TZ_COOKIE)
    if raw:
        candidate = urllib.parse.unquote(raw).strip()[:64]
        if candidate and is_valid_zone(candidate):
            return candidate
    return default if is_valid_zone(default) else "UTC"
