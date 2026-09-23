"""Source-level checks for the admin console's client behavior, in the style
of the manage.js checks in test_manage_js.py: the served script must carry
the fix, not just the markup around it."""
from scheduler import render


def _admin_js():
    return (render.WEB_DIR / "admin.js").read_text()


def test_successful_pause_toggles_keep_the_keyboard_on_the_fresh_toggle():
    # Success rebuilds the whole overview section, so the pressed button
    # stops existing; without a handback a keyboard admin lands on <body>
    # right after the state they asked for. The fresh toggle is the same
    # control with the flipped label, so it is where they continue.
    js = _admin_js()
    handler = js.split('btn.addEventListener("click"', 1)[1]
    assert "loadOverview().then" in handler.split("} catch (err)", 1)[0]
    assert 'document.querySelector("#overview .panel-head .btn")' in handler
    # A failed refetch leaves the old button in place: re-enable and hold.
    assert "btn.disabled = false; btn.focus();" in handler


def test_successful_type_toggles_keep_the_keyboard_in_their_row():
    js = _admin_js()
    # One handback in the disable/enable toggle, one in the questions save,
    # one in the details save.
    assert js.count("loadTypes().then") == 3
    toggle = js.split('toggle.addEventListener("click"', 1)[1]
    # The row index is captured before the flight: the reload rebuilds every
    # row, so the landing spot must be computed from the table being replaced.
    assert "[...tbody.children].indexOf(row)" in toggle.split("loadTypes().then", 1)[0]
    success = toggle.split("loadTypes().then", 1)[1]
    assert 'document.querySelectorAll("#types tbody tr")[rowIndex]' in toggle
    assert "toggle.disabled = false; toggle.focus();" in success


def test_successful_cancels_keep_the_keyboard_in_their_row():
    js = _admin_js()
    assert js.count("loadBookings().then") == 1
    cancel = js.split('cancel.addEventListener("click"', 1)[1]
    assert "[...tbody.children].indexOf(row)" in cancel.split("loadBookings().then", 1)[0]
    success = cancel.split("loadBookings().then", 1)[1]
    assert 'document.querySelectorAll("#bookings tbody tr")[rowIndex]' in cancel
    assert "cancel.disabled = false; cancel.focus();" in success


def test_saved_questions_reopen_on_the_row_they_belong_to():
    js = _admin_js()
    editor = js.split("function openQuestionsEditor", 1)[1]
    assert "[...hostRow.parentNode.children].indexOf(hostRow)" in editor
    teardown = editor.split("save.addEventListener", 1)[1]
    # The editor's own Questions button is now collapsed; the keyboard lands
    # there, whether the trailing reload succeeds or (old table still up)
    # fails. Two editors share the row now, so the handback aims at the
    # questions button by name: a bare [aria-expanded] would match whichever
    # expandable button comes first in the stack.
    assert "'.actions-stack button[data-editor=" + chr(34) + "questions" + chr(34) + "]'" in teardown
    assert js.count("loadTypes().then") == 3


def test_question_move_buttons_name_the_question_they_move():
    # "Move question 3 up" gives a screen-reader host a number with nothing
    # to anchor it to. The names carry the question's own label, updated as
    # the label is typed (typing never rerenders the card), and an unnamed
    # question falls back to the number its visible Q-chip shows.
    js = _admin_js()
    card = js.split("const moves = el(" + chr(34) + "div" + chr(34) + ");", 1)[1]
    card = card.split("moves.append(up, down, remove);", 1)[0]
    assert "Move “${qName()} up" not in card  # template quotes stay balanced
    assert "qName" in card
    sync = card.split("const syncMoves = () => {", 1)[1].split("};", 1)[0]
    assert chr(96) + "Move “${qName()}” up" + chr(96) in sync
    assert chr(96) + "Move “${qName()}” down" + chr(96) in sync
    assert chr(96) + "Remove “${qName()}”" + chr(96) in sync
    assert "item.label.trim() || `question ${index + 1}`" in card
    # Live rename: the label input's existing listener updates item.label
    # first, so the names follow the text as typed.
    assert "labelInput.addEventListener(" + chr(34) + "input" + chr(34) + ", syncMoves);" in card


def test_details_editor_saves_only_the_fields_it_owns():
    # The server merges whatever a PUT carries, so a careless payload would
    # save this editor's stale copy of questions or visibility over a newer
    # one written by the other editors. The details editor sends its own
    # fields plus the expected version, never theirs.
    js = _admin_js()
    editor = js.split("function openDetailsEditor", 1)[1]
    save = editor.split("save.addEventListener", 1)[1]
    payload = save.split("const payload = {", 1)[1].split("};", 1)[0]
    for key in ("title: ", "description, ", "slug: ", "duration_mode: ", "expected_version"):
        assert key in payload
    assert "questions" not in payload
    assert "visibility" not in payload
    # The length rules follow the chosen mode: a fixed number, or the ticked
    # set — never both, never neither.
    assert "payload.fixed_duration_min = draft.fixed" in save
    assert "payload.allowed_durations = " in save


def test_details_editor_flags_bad_fields_before_the_save_flies():
    # The same rules the server enforces, run client-side so the failing
    # field names itself and focus lands on the first offender.
    js = _admin_js()
    editor = js.split("function openDetailsEditor", 1)[1]
    save = editor.split("save.addEventListener", 1)[1]
    preflight = save.split("try {", 1)[0]
    assert "Give the type a name." in preflight
    assert "no spaces or slashes" in preflight
    assert "between 1 and 180 minutes" in preflight
    assert "Tick at least one length." in preflight
    assert "Fix the highlighted fields." in preflight
    assert "firstBad.focus()" in preflight
    # A rejected save never leaves the button disabled.
    assert "save.disabled = false;" in preflight
    # Every failing field is flagged, not only the first: the helper owns
    # the firstBad bookkeeping, so a second bad field can't be silenced by
    # a short-circuit on the first.
    assert "if (!firstBad) firstBad = input;" in editor
    assert "firstBad = firstBad || flag(" not in editor


def test_details_editor_hands_the_keyboard_to_its_own_button():
    # Two expandable buttons share each row now. Each editor's post-save
    # handback aims at its own button by name, and the visibility toggle
    # stays first in the stack so its own handback stays unambiguous.
    js = _admin_js()
    assert "details.dataset.editor = " + chr(34) + "details" + chr(34) in js
    assert "edit.dataset.editor = " + chr(34) + "questions" + chr(34) in js
    details = js.split("function openDetailsEditor", 1)[1]
    assert "'.actions-stack button[data-editor=" + chr(34) + "details" + chr(34) + "]'" in details
    questions = js.split("function openQuestionsEditor", 1)[1]
    assert "'.actions-stack button[data-editor=" + chr(34) + "questions" + chr(34) + "]'" in questions
    # Toggle first, then the two editors, then Preview: the order the row
    # reads in, and the order the handbacks assume.
    build = js.split("async function loadTypes", 1)[1]
    order = [build.index("stack.appendChild(" + name + ");")
             for name in ("toggle", "details", "edit", "preview")]
    assert order == sorted(order)
