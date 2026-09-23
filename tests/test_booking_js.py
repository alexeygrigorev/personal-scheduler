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


def test_time_pick_scrolls_only_when_the_form_is_off_screen():
    # The pick scroll exists for the phone, where the details form is a tall
    # block below the fold. On a desktop the sticky panel never left the
    # viewport, and scrolling to its heading anyway yanks the calendar out
    # from under the visitor's cursor — which then hovers a random slot as
    # if the app had highlighted it. The anchor's box must gate the scroll:
    # fully on screen means no jump.
    js = (render.WEB_DIR / "booking.js").read_text()
    guard = js.split("const detailsAnchor", 1)[1].split("scrollIntoView", 1)[0]
    assert "getBoundingClientRect()" in guard
    assert "alreadyInView" in guard
    call = js.split("if (!alreadyInView)", 1)[1].split("}", 1)[0]
    assert 'block: "start"' in call


def test_the_taken_retry_focus_lands_visibly():
    # The retry parks focus on the first fresh slot, but a focus() riding a
    # click response never matches :focus-visible — the landing would be
    # invisible to the sighted keyboard user the scroll just carried away.
    # The hint class borrows the keyboard ring for exactly this landing, and
    # the empty-month fallback keeps its own ink on the verdict.
    js = (render.WEB_DIR / "booking.js").read_text()
    landing = js.split("if (fresh) {", 1)[1].split("else verdict.focus", 1)[0]
    assert 'fresh.classList.add("focus-hint")' in landing
    assert "fresh.focus({ preventScroll: true })" in landing
    css = (render.WEB_DIR / "app.css").read_text()
    assert ".times button.focus-hint:focus" in css
    # The two programmatic landings that used to mute the ring entirely.
    assert ".times-verdict:focus, .times-verdict:focus-visible { outline: 2px solid currentColor" in css
    assert ".status:focus, .status:focus-visible { outline: 2px solid currentColor" in css
    assert "outline: none; }" not in css.split(".times-verdict", 1)[1].split(".details-panel-inner", 1)[0]


def test_the_taken_retry_focus_lands_visibly():
    # The retry parks focus on the first fresh slot, but a focus() riding a
    # click response never matches :focus-visible — the landing would be
    # invisible to the sighted keyboard user the scroll just carried away.
    # The hint class borrows the keyboard ring for exactly this landing, and
    # the empty-month fallback keeps its own ink on the verdict.
    js = (render.WEB_DIR / "booking.js").read_text()
    landing = js.split("if (fresh) {", 1)[1].split("else verdict.focus", 1)[0]
    assert 'fresh.classList.add("focus-hint")' in landing
    assert "fresh.focus({ preventScroll: true })" in landing
    css = (render.WEB_DIR / "app.css").read_text()
    assert ".times button.focus-hint:focus" in css
    # The two programmatic landings that used to mute the ring entirely.
    assert ".times-verdict:focus, .times-verdict:focus-visible { outline: 2px solid currentColor" in css
    assert ".status:focus, .status:focus-visible { outline: 2px solid currentColor" in css
    assert "outline: none; }" not in css.split(".times-verdict", 1)[1].split(".details-panel-inner", 1)[0]


def test_the_browser_zone_joins_the_picker_at_the_top_with_an_offset():
    # A first visit carries no tz cookie, so the server render cannot pin the
    # visitor's zone — booking.js must. Appending it last buried the zone the
    # visitor already picked with their whole life behind nine host picks.
    js = (render.WEB_DIR / "booking.js").read_text()
    boot = js.split('const tzSelect = $("tz-select");', 1)[1].split(
        "tzSelect.value = state.timezone;", 1)[0]
    assert "insertBefore(opt, tzSelect.firstChild)" in boot
    assert "shortOffset" in boot
    assert 'padStart(2, "0")' in boot


def test_the_browser_zone_joins_the_picker_at_the_top_with_an_offset():
    # A first visit carries no tz cookie, so the server render cannot pin the
    # visitor's zone — booking.js must. Appending it last buried the zone the
    # visitor already picked with their whole life behind nine host picks.
    js = (render.WEB_DIR / "booking.js").read_text()
    boot = js.split('const tzSelect = $("tz-select");', 1)[1].split(
        "tzSelect.value = state.timezone;", 1)[0]
    assert "insertBefore(opt, tzSelect.firstChild)" in boot
    assert "shortOffset" in boot
    assert 'padStart(2, "0")' in boot
