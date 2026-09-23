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
    # One handback in the disable/enable toggle, one in the questions save.
    assert js.count("loadTypes().then") == 2
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
    # fails.
    assert "'.actions-stack button[aria-expanded]'" in teardown
    assert js.count("loadTypes().then") == 2


def test_question_move_buttons_name_the_question_they_move():
    # "Move question 3 up" gives a screen-reader host a number with nothing
    # to anchor it to. The names carry the question's own label, updated as
    # the label is typed (typing never rerenders the card), and an unnamed
    # question falls back to the number its visible Q-chip shows.
    js = _admin_js()
    card = js.split("const moves = el(\"div\");", 1)[1].split("moves.append(up, down, remove);", 1)[0]
    assert "Move \u201c${qName()} up" not in card  # template quotes stay balanced
    assert "qName" in card
    assert 'up.setAttribute("aria-label", `\u201c' not in card  # names come from syncMoves
    sync = card.split("const syncMoves = () => {", 1)[1].split("};", 1)[0]
    assert '`Move \u201c${qName()}\u201d up`' in sync
    assert '`Move \u201c${qName()}\u201d down`' in sync
    assert '`Remove \u201c${qName()}\u201d`' in sync
    assert 'item.label.trim() || `question ${index + 1}`' in card
    # Live rename: the label input's existing listener updates item.label
    # first, so the names follow the text as typed.
    assert "labelInput.addEventListener(\"input\", syncMoves);" in card


def test_question_move_buttons_name_the_question_they_move():
    # "Move question 3 up" gives a screen-reader host a number with nothing
    # to anchor it to. The names carry the question's own label, updated as
    # the label is typed (typing never rerenders the card), and an unnamed
    # question falls back to the number its visible Q-chip shows.
    js = _admin_js()
    card = js.split("const moves = el(\"div\");", 1)[1].split("moves.append(up, down, remove);", 1)[0]
    assert "Move \u201c${qName()} up" not in card  # template quotes stay balanced
    assert "qName" in card
    assert 'up.setAttribute("aria-label", `\u201c' not in card  # names come from syncMoves
    sync = card.split("const syncMoves = () => {", 1)[1].split("};", 1)[0]
    assert '`Move \u201c${qName()}\u201d up`' in sync
    assert '`Move \u201c${qName()}\u201d down`' in sync
    assert '`Remove \u201c${qName()}\u201d`' in sync
    assert 'item.label.trim() || `question ${index + 1}`' in card
    # Live rename: the label input's existing listener updates item.label
    # first, so the names follow the text as typed.
    assert "labelInput.addEventListener(\"input\", syncMoves);" in card
