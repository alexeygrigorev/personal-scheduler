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


def test_every_offset_on_the_page_reads_one_dialect():
    # The picker labels say (UTC+05:30); the summary card used its own
    # formatter and said (UTC+5:30) — the same page speaking two dialects
    # of the same fact reads like a typo in one of them.
    js = (render.WEB_DIR / "booking.js").read_text()
    label = js.split("function zoneOffsetLabel(iso) {", 1)[1].split("\n  }", 1)[0]
    assert 'padStart(2, "0")' in label
    assert '"UTC+00:00"' in label


def test_the_offered_visitor_zone_leads_on_the_first_visit_too():    # The common zones already carry America/New_York, so a first visit (no
    # tz cookie yet) hits neither the server-side pin nor the missing-zone
    # insert — the visitor's zone sat mid-list until the next page load,
    # when the picker's promise is that their zone leads at once.
    js = (render.WEB_DIR / "booking.js").read_text()
    boot = js.split('const tzSelect = $("tz-select");', 1)[1].split(
        "tzSelect.value = state.timezone;", 1)[0]
    assert "findIndex((o) => o.value === state.timezone)" in boot
    assert "else if (own > 0)" in boot
    assert "insertBefore(tzSelect.options[own], tzSelect.firstChild)" in boot


def test_the_first_visit_move_keeps_the_offset_scan_true():
    # Moving the visitor's offered zone to the top leaves the server's pin
    # (the event default, on a first visit) in the seat the move vacated —
    # the rest would read +02:00, -07:00, -05:00... The boot must re-read
    # the remaining options in offset order.
    js = (render.WEB_DIR / "booking.js").read_text()
    boot = js.split('const tzSelect = $("tz-select");', 1)[1].split(
        "tzSelect.value = state.timezone;", 1)[0]
    assert "spokenOffset" in boot
    assert ".sort((a, b) => spokenOffset(a.textContent) - spokenOffset(b.textContent))" in boot
    assert "for (const o of rest) tzSelect.appendChild(o);" in boot


def test_a_timezone_change_keeps_an_instant_the_fresh_grid_still_offers():
    # The picker exists so a visitor can ask "what does my 11:30 look like in
    # Tokyo" — and an answer that cleared the pick, closed the form, and
    # demanded a fresh day+slot click turned that question into a trap. The
    # re-anchored load must re-validate the kept instant: still offered means
    # the pick survives (day re-pressed, end recomputed, summary speaking the
    # new dialect); gone means the plain cleared state returns.
    js = (render.WEB_DIR / "booking.js").read_text()
    verdict = js.split("state.dayToSlots = byDay;", 1)[1].split("if (!state.days.length)", 1)[0]
    assert "keepSelection && state.selectedStart" in verdict
    # The API names each slot in the requested zone's offset, so the kept
    # instant must be matched as an instant — a string compare would face
    # 11:30+02:00 against 18:30+09:00 and drop a slot the grid still offers.
    assert "new Date(s.start).getTime() === keptMs" in verdict
    kept = verdict.split("if (keptSlot) {", 1)[1].split("} else {", 1)[0]
    assert "state.selectedDay = keptDay" in kept
    assert "state.selectedStart = keptSlot.start" in kept
    assert "state.selectedEnd = keptSlot.end" in kept
    assert "updateSummary()" in kept
    dropped = verdict.split("} else {", 1)[1]
    assert 'state.selectedStart = ""' in dropped
    assert "showDetails(false)" in dropped
    assert "updateSummary()" in dropped


def test_the_tz_and_duration_switches_never_preclear_the_pick():
    # Clearing before the reload flashes "No time selected yet" at the very
    # visitor who is about to get their pick back, and closes the form the
    # kept-selection load is about to keep open. Both re-anchoring switches
    # must hand the decision to the reload's verdict instead.
    js = (render.WEB_DIR / "booking.js").read_text()
    tz = js.split('$("tz-select").addEventListener("change"', 1)[1].split("});", 1)[0]
    assert 'state.selectedStart = ""' not in tz
    assert "loadAvailability(true, true)" in tz
    duration = js.split("state.duration = Number(radio.value);", 1)[1].split("});", 1)[0]
    assert 'state.selectedStart = ""' not in duration
    assert "loadAvailability(true, true)" in duration


def test_the_kept_selection_load_skips_the_upfront_clear():
    # The keepSelection mode leaves the summary advertising the picked
    # instant while the fresh grid is in flight — the up-front clear (and
    # its summary flash) belongs to the modes that have no pick to keep.
    js = (render.WEB_DIR / "booking.js").read_text()
    head = js.split("async function loadAvailability(", 1)[1].split("renderSkeleton", 1)[0]
    assert "keepSelection = false" in head
    assert "if (!keepSelection) {" in head
    guard = head.split("if (!keepSelection) {", 1)[1]
    assert 'state.selectedStart = ""' in guard
    assert "updateSummary()" in guard


def test_the_summary_flags_a_range_that_crosses_midnight():
    # A distant zone pushes a Berlin afternoon past midnight: for a +14
    # visitor the slot ends at 00:00 their Thursday, and a range reading
    # "23:30 - 00:00" under Wednesday's date quietly books a different day
    # than the one the card advertises. The when-chunk must carry a day flag
    # whenever the display-zone date of the end leaves the start's date.
    js = (render.WEB_DIR / "booking.js").read_text()
    summary = js.split("function updateSummary()", 1)[1].split("el.replaceChildren", 1)[0]
    assert 'toLocaleDateString("en-CA", { timeZone: state.timezone })' in summary
    assert '" (+1 day)" : ""' in summary
    assert "`${range} ·`" in summary



def test_the_picker_grows_a_search_that_reaches_every_zone():
    # Ten host picks cannot cover a visitor booking from Halifax or
    # Hyderabad. With scripting on, the select is upgraded in place: a
    # search field filters the browser's whole zone database, while the
    # select itself stays the one value store — hidden, still receiving
    # the change event the cookie, the note, and the reload listen on.
    js = (render.WEB_DIR / "booking.js").read_text()
    combo = js.split("function upgradeTimezonePicker(select) {", 1)[1].split(
        "\n  }\n", 1)[0]
    assert 'setAttribute("role", "combobox")' in combo
    assert 'setAttribute("aria-expanded", "false")' in combo
    assert 'aria-controls", "tz-listbox"' in combo
    assert 'aria-activedescendant' in combo
    assert 'Intl.supportedValuesOf("timeZone")' in combo
    assert "if (!q) return curated.slice();" in combo
    # The store contract: real option, selected value, change event.
    assert "opt.value = zone;" in combo
    assert "select.value = zone;" in combo
    assert 'select.dispatchEvent(new Event("change", { bubbles: true }));' in combo
    assert "select.hidden = true;" in combo


def test_the_timezone_search_answers_the_keyboard():
    # A picker a keyboard cannot drive is a picker half the room cannot
    # use: arrows walk and wrap, Home/End jump, Enter commits the active
    # row, Escape closes and restores the field's value, and focus opens
    # on the zone the visitor already books in.
    js = (render.WEB_DIR / "booking.js").read_text()
    combo = js.split("function upgradeTimezonePicker(select) {", 1)[1].split(
        "\n  }\n", 1)[0]
    for key in ("ArrowDown", "ArrowUp", "Home", "End", "Enter", "Escape"):
        assert f'ev.key === "{key}"' in combo
    assert "((i % shown.length) + shown.length) % shown.length" in combo
    assert "input.value = displayValue(state.timezone);" in combo
    # The resting label is not a query: focus starts the lookup fresh,
    # and only leaving without a pick restores the label.
    assert 'input.value = "";' in combo


def test_the_timezone_menu_stays_a_menu_at_full_depth():
    # Four hundred zones render as wall; sixty render as a menu. The cap
    # bounds only the paint — the tail row names what search still
    # reaches, and a query that matches nothing says so in its own words.
    js = (render.WEB_DIR / "booking.js").read_text()
    combo = js.split("function upgradeTimezonePicker(select) {", 1)[1].split(
        "\n  }\n", 1)[0]
    assert "const MAX_ROWS = 60;" in combo
    assert "more — keep typing to narrow" in combo
    assert "No timezone matches" in combo


def test_the_timezone_popover_wears_the_shared_tokens():
    # The popover is new surface, not a new dialect: surface, line, and
    # shadow come from the tokens (so dark mode is inherited, not
    # re-painted), rows keep the 2.75rem control height, and the field
    # clears the caret it now carries.
    css = (render.WEB_DIR / "app.css").read_text()
    block = css.split(".tz-list {", 1)[1].split("}", 1)[0]
    assert "var(--surface)" in block
    assert "var(--shadow-pop)" in block
    assert "var(--r-ctrl)" in block
    assert "var(--line-strong)" in block
    assert ".tz-row {" in css
    row = css.split(".tz-row {", 1)[1].split("}", 1)[0]
    assert "min-height: 2.75rem" in row
    assert "padding-right: 2.5rem" in css
    # a typed field never sits below the 16px iOS-zoom floor
    assert "font-size: var(--fs-body)" in css
    assert ".tz-row {" in css and "font-size: var(--fs-secondary)" in css
    assert '.tz-input[aria-expanded="true"] + .tz-caret' in css


def test_the_picker_grows_a_search_that_reaches_every_zone():
    # Ten host picks cannot cover a visitor booking from Halifax or
    # Hyderabad. With scripting on, the select is upgraded in place: a
    # search field filters the browser's whole zone database, while the
    # select itself stays the one value store — hidden, still receiving
    # the change event the cookie, the note, and the reload listen on.
    js = (render.WEB_DIR / "booking.js").read_text()
    combo = js.split("function upgradeTimezonePicker(select) {", 1)[1].split(
        "\n  }\n", 1)[0]
    assert 'setAttribute("role", "combobox")' in combo
    assert 'setAttribute("aria-expanded", "false")' in combo
    assert 'aria-controls", "tz-listbox"' in combo
    assert 'aria-activedescendant' in combo
    assert 'Intl.supportedValuesOf("timeZone")' in combo
    assert "if (!q) return curated.slice();" in combo
    # The store contract: real option, selected value, change event.
    assert "opt.value = zone;" in combo
    assert "select.value = zone;" in combo
    assert 'select.dispatchEvent(new Event("change", { bubbles: true }));' in combo
    assert "select.hidden = true;" in combo


def test_the_timezone_search_answers_the_keyboard():
    # A picker a keyboard cannot drive is a picker half the room cannot
    # use: arrows walk and wrap, Home/End jump, Enter commits the active
    # row, Escape closes and restores the field's value, and focus opens
    # on the zone the visitor already books in.
    js = (render.WEB_DIR / "booking.js").read_text()
    combo = js.split("function upgradeTimezonePicker(select) {", 1)[1].split(
        "\n  }\n", 1)[0]
    for key in ("ArrowDown", "ArrowUp", "Home", "End", "Enter", "Escape"):
        assert f'ev.key === "{key}"' in combo
    assert "((i % shown.length) + shown.length) % shown.length" in combo
    assert "input.value = displayValue(state.timezone);" in combo
    # The resting label is not a query: focus starts the lookup fresh,
    # and only leaving without a pick restores the label.
    assert 'input.value = "";' in combo


def test_the_timezone_menu_stays_a_menu_at_full_depth():
    # Four hundred zones render as wall; sixty render as a menu. The cap
    # bounds only the paint — the tail row names what search still
    # reaches, and a query that matches nothing says so in its own words.
    js = (render.WEB_DIR / "booking.js").read_text()
    combo = js.split("function upgradeTimezonePicker(select) {", 1)[1].split(
        "\n  }\n", 1)[0]
    assert "const MAX_ROWS = 60;" in combo
    assert "more — keep typing to narrow" in combo
    assert "No timezone matches" in combo


def test_the_timezone_popover_wears_the_shared_tokens():
    # The popover is new surface, not a new dialect: surface, line, and
    # shadow come from the tokens (so dark mode is inherited, not
    # re-painted), rows keep the 2.75rem control height, and the field
    # clears the caret it now carries.
    css = (render.WEB_DIR / "app.css").read_text()
    block = css.split(".tz-list {", 1)[1].split("}", 1)[0]
    assert "var(--surface)" in block
    assert "var(--shadow-pop)" in block
    assert "var(--r-ctrl)" in block
    assert "var(--line-strong)" in block
    assert ".tz-row {" in css
    row = css.split(".tz-row {", 1)[1].split("}", 1)[0]
    assert "min-height: 2.75rem" in row
    assert "padding-right: 2.5rem" in css
    # a typed field never sits below the 16px iOS-zoom floor
    assert "font-size: var(--fs-body)" in css
    assert ".tz-row {" in css and "font-size: var(--fs-secondary)" in css
    assert '.tz-input[aria-expanded="true"] + .tz-caret' in css
