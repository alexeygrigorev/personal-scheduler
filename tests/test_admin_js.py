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
    # one in the details save, one in the create flow, one in the duplicate.
    assert js.count("loadTypes().then") == 5
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
    assert js.count("loadTypes().then") == 5


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
    # Each attempt re-ranks the offenders from zero: firstBad kept from the
    # last bounce refocused a corrected field and bounced a valid save with
    # nothing left to highlight.
    assert "firstBad = null;" in preflight


def test_details_editor_hides_the_whole_fixed_length_affordance():
    # Selectable mode hides the wrap; a dash left outside it read as
    # "Fixed length —" pointing at nothing. The dash, number, and unit
    # travel together inside the hidden span.
    js = _admin_js()
    details = js.split("function openDetailsEditor", 1)[1]
    row = details.split("const pickRow", 1)[0]
    assert 'createTextNode("Fixed length")' in row
    wrap = row.split("const fixedWrap", 1)[1]
    assert 'createTextNode(" — ")' in wrap


def test_duration_chips_keep_their_glyph_square_and_target_big():
    # The global 44px input floor stretches the checkbox square; the label
    # text then baseline-aligns to the glyph box's bottom edge and every
    # chip reads stacked. The floor moves to the label, the glyph stays
    # square, and the panel scrolls clear of the sticky head.
    css = (render.WEB_DIR / "app.css").read_text()
    q_check = css.split("\n.q-check {", 1)[1].split("}", 1)[0]
    assert "min-height: 2.75rem" in q_check
    glyph = css.split("\n.q-check input {", 1)[1].split("}", 1)[0]
    assert "min-height: 0" in glyph
    number = css.split('.q-check input[type="number"] {', 1)[1].split("}", 1)[0]
    assert "min-height: 2.75rem" in number
    assert ".q-editor { scroll-margin-top: 4.75rem; }" in css


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
    # Toggle first, then the two editors, then Duplicate, then Preview: the
    # order the row reads in, and the order the handbacks assume.
    build = js.split("async function loadTypes", 1)[1]
    order = [build.index("stack.appendChild(" + name + ");")
             for name in ("toggle", "details", "edit", "dup", "preview")]
    assert order == sorted(order)


def test_new_type_creates_with_a_slug_that_steals_no_address():
    # The server writes slug pointers last-write-wins, so a create with a
    # colliding slug would silently repoint another type's public page. The
    # create uniquifies against every live slug AND alias before it flies.
    js = _admin_js()
    # The action lives in the section head, so it exists even on an empty
    # list, where it is the only way forward.
    assert 'head.appendChild(el("h2", "Event types"));' in js
    create = js.split('create.addEventListener("click"', 1)[1]
    preflight = create.split("await call(", 1)[0]
    assert "taken.add(t.slug);" in js
    assert "for (const a of t.aliases || []) taken.add(a);" in js
    assert 'let slug = "new-meeting-type";' in preflight
    assert "taken.has(slug)" in preflight
    # The reload rebuilds the table, so an editor holding unsaved edits gets
    # the same arm-to-discard chance as any other close.
    assert "if (openEditor && !openEditor.requestClose()) return;" in preflight
    assert '{ title: "New meeting type", slug }' in create
    # A fresh type is a placeholder until it is named: the keyboard lands
    # inside the new row's own details editor, in the title field.
    success = create.split("loadTypes().then", 1)[1]
    assert 'tr[data-id="${created.id}"]' in success
    assert 'button[data-editor="details"]' in success
    assert '.panel input' in success
    assert "titleField.focus()" in success
    # A failed create re-enables its button and speaks in the head note.
    assert "create.disabled = false; create.focus();" in create
    assert '"saved-note error visible"' in create


def test_duplicate_copies_without_arming_and_aims_at_the_clone():
    # A copy destroys nothing, so unlike the disable toggle it flies without
    # an arm step. The post-reload keyboard lands on the clone's own row,
    # found by id and not by index: where the clone sorts is the server's
    # call, not the index the source row happened to sit at.
    js = _admin_js()
    assert "row.dataset.id = t.id;" in js
    dup = js.split('dup.addEventListener("click"', 1)[1]
    assert "action: \"duplicate\"" in dup
    preflight = dup.split("dup.disabled = true", 1)[0]
    assert "requestClose" in preflight
    assert "dataset.armed" not in preflight
    success = dup.split("loadTypes().then", 1)[1]
    assert 'tr[data-id="${clone.id}"] .actions-stack button' in success
    assert "dup.disabled = false; dup.focus();" in success
    # A failed copy speaks beside the actions, one note per row.
    assert '"saved-note error visible row-error"' in dup


def test_duration_cells_render_as_atomic_chips():
    # The Duration column lists pickable lengths as the landing cards'
    # pills: a chip is one atomic unit, so a narrow cell wraps a whole
    # length to the next line and no separator slash can strand at either
    # edge of a wrapped line ("30 min /" or "/ 1 hour").
    js = _admin_js()
    assert 'row.className = "duration-chips"' in js
    assert 'chip.className = "meta-chip"' in js
    cell = js.split("const durationCell =", 1)[1].split("row.appendChild", 1)[0]
    assert "durationChips(" in cell


def test_a_failed_upcoming_load_never_reads_as_an_empty_shelf():
    # The overview swallows the bookings call so one dead endpoint cannot
    # take the whole section down — but a swallowed {} used to render as
    # "Nothing booked ahead.", telling the admin to write off real bookings.
    # The failure must arrive as null and wear the tab loader's error dress,
    # with the same keyboard contract as its Retry buttons.
    js = _admin_js()
    overview = js.split("async function loadOverview", 1)[1].split("async function loadTypes", 1)[0]
    assert 'call("/bookings?status=confirmed").catch(() => null)' in overview
    # Only the bookings call is converted to null; event-types stays a
    # swallowed {} because it only degrades labels, never lies.
    assert overview.count(".catch(() => ({}))") == 1
    failed = overview.split("upcoming === null", 1)[1]
    assert 'failed.className = "empty-state error"' in failed
    assert 'el("p", "Could not load upcoming bookings.")' in failed
    assert failed.count('retry.className = "btn sm"') == 1
    assert "again.focus({ preventScroll: true })" in failed
    assert "retry.disabled = false; retry.focus();" in failed
    # The true empty keeps its quiet voice but wears the console's shared
    # dashed empty-state panel — a lone hint line under the head read as a
    # second, lesser design system beside the tabs' empty states.
    quiet = failed.split("} else if (!list.length)", 1)[1]
    assert 'emptyPanel("Nothing booked ahead.",' in quiet
    assert "Share a booking link from your event types" in quiet


def test_a_rejected_address_flags_the_slug_field_itself():
    # The server's collision verdict names an address, and the slug field
    # is where that fix happens: the save catch wears the message on the
    # slug input like the client rules do, moves the keyboard into the
    # field, and drops the note to the same "fix the highlighted fields"
    # grammar. The code rides the thrown error so only that rejection is
    # field-scoped; every other failure keeps the button verdict.
    js = _admin_js()
    call = js.split("async function call(path, options)", 1)[1]
    assert "err.code = data.error && data.error.code;" in call
    handler = js.split('save.addEventListener("click"', 1)[1]
    catch = handler.split("} catch (err)", 1)[1].split("});", 1)[0]
    assert 'err.code === "invalid_input" && /The address /.test(err.message)' in catch
    assert "flag(slugInput, slugField.querySelector(\".error\")" in catch
    assert "slugInput.focus();" in catch
    assert 'note.textContent = "Fix the highlighted fields."' in catch
    assert "Could not save —" in catch


def test_print_drops_the_console_tab_bar():
    # The section switcher is screen chrome, and its pressed-tab highlight
    # prints as a meaningless purple word on paper. The sheet keeps the
    # types and their details, not the console's navigation.
    css = (render.WEB_DIR / "app.css").read_text()
    print_block = css.split("@media print {", 1)[1].split("forced colors", 1)[0]
    assert ".tabs { display: none !important; }" in print_block

