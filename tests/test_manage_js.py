"""Source-level checks for the manage page's client behavior, in the style of
the booking.js checks in test_render.py: the served script must carry the
fix, not just the markup around it."""
from scheduler import render


def test_editing_the_start_retires_the_reschedule_verdict():
    # A reschedule verdict describes the start as it was submitted; once the
    # visitor edits that field, the banner must not keep arguing with the
    # picker ("in the past" over a corrected future time). Retirement rides
    # the input event, like every field error on the booking form, and a
    # fresh submit re-verdicts. Only reschedule-owned verdicts retire this
    # way — a cancel or pending message is nobody's complaint about the
    # picker — so every raising point has to say who is speaking.
    js = (render.WEB_DIR / "manage.js").read_text()
    listener = js.split('startInput.addEventListener("input"', 1)[1]
    assert "reschedVerdict" in listener.split("updatePreview();", 1)[0]
    # Retirement restores the loaded page's standing note, not an emptied box.
    assert 'setStatus("", standingNote)' in listener
    assert 'standingNote = statusEl.querySelector(".status-text").textContent' in js
    # The ownership comparison, two validation branches, and the reschedule
    # action's error path — four places, no untagged raising point.
    assert js.count('"resched"') == 4


def test_a_reopened_pending_page_keeps_the_outcome_promise():
    # The pending note promises "this page will reflect the outcome when it
    # lands" — a promise a visitor reopening the link mid-cancellation must
    # not find hollow: the same settle watcher a submitted action arms has
    # to start on load, fed the pending flag through the page's config.
    js = (render.WEB_DIR / "manage.js").read_text()
    assert "if (cfg.pendingCancel) waitForSettle();" in js
