"""Source-level checks for the booking page's day-pick behavior, in the style
of the booking.js checks in test_render.py: the served script must carry the
fix, not just the markup around it."""
from scheduler import render


def test_picking_a_fresh_day_retires_the_taken_alarm():
    # After a taken-slot 409 the verdict above the times and the banner above
    # the grid both beg for a new pick. The slot-pick handler already retires
    # them; a day pick replaces the whole list they were written about, so it
    # must retire them too — a verdict praising a fresh day as "just taken"
    # reads like the day is suspect. Day counts and load failures describe
    # live reality, so only the two alarm wordings may be stepped down.
    js = (render.WEB_DIR / "booking.js").read_text()
    pick = js.split("state.selectedDay = key;", 1)[1].split("renderDays(state.days);", 1)[0]
    assert 'getElementById("times-verdict")' in pick
    assert ".remove()" in pick
    assert 'includes("just taken")' in pick
    assert 'startsWith("Could not confirm")' in pick
    assert 'setStatus("", "Day picked — now choose a time.")' in pick
