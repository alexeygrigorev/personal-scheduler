/* Invitee management page: read-only summary plus explicit cancel and
 * reschedule actions. Opening this page never mutates anything, so email
 * link scanners cannot cancel a meeting by fetching it. */
(function () {
  "use strict";
  const root = document.getElementById("manage-root");
  if (!root) return;
  const cfg = JSON.parse(root.dataset.config || "{}");
  const api = cfg.apiBase || "/api/v1";
  const token = cfg.token;

  const statusEl = document.getElementById("manage-status");
  function setStatus(kind, text) {
    statusEl.className = "status" + (kind ? " " + kind : "");
    // Failed actions announce assertively, like every other status box here.
    statusEl.setAttribute("role", kind === "error" ? "alert" : "status");
    statusEl.querySelector(".status-text").textContent = text;
  }
  // The verdict for a submitted action renders at the top of the page while
  // the buttons sit further down; on a short laptop window the two can be a
  // screen apart, so an outcome scrolls to where the visitor is looking.
  function revealStatus() {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    statusEl.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
  }

  // A pending action finishes in the background, so the promise "this page
  // will reflect the outcome" has to keep itself: poll the read-only status
  // probe and reload once when the booking changes underneath us — a landed
  // cancel flips the status, a landed reschedule bumps the revision. No
  // reload while pending (the visitor may still be reading or typing here),
  // and a failed probe just waits for the next tick.
  // The outcome reload would silently discard a change submitted meanwhile,
  // so the reschedule submit locks while any action is pending and comes
  // back if the wait gives up. The cancel row manages its own lock: its
  // give-up copy still rules out a retry.
  function lockReschedule() {
    const btn = document.querySelector('#reschedule-form button[type="submit"]');
    if (!btn || btn.dataset.pendingLock) return;
    btn.dataset.pendingLock = "1";
    btn.disabled = true;
  }
  function unlockPendingLocks() {
    document.querySelectorAll("[data-pending-lock]").forEach((b) => {
      b.disabled = false;
      delete b.dataset.pendingLock;
    });
  }

  function waitForSettle() {
    let left = 40; // ~2.5 minutes at 4s, then hand the wait back to the visitor
    const tick = async () => {
      if (left <= 0) {
        setStatus("", "The calendar is still being updated. Refresh this page in a minute to see the outcome.");
        unlockPendingLocks();
        return;
      }
      left -= 1;
      let state = null;
      try {
        const res = await fetch(`${api}/manage/${token}/status`);
        if (res.ok) state = await res.json();
      } catch (err) { /* transient; next tick retries */ }
      if (state && (state.status !== "confirmed"
                    || Number(state.revision) !== Number(cfg.revision))) {
        window.location.reload();
        return;
      }
      setTimeout(tick, 4000);
    };
    setTimeout(tick, 4000);
  }

  async function postAction(action, payload, messageFor) {
    setStatus("", "Working…");
    try {
      const res = await fetch(`${api}/manage/${token}/${action}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      // A gateway error page is HTML, not JSON; keep its noise out of the
      // status line.
      const data = await res.json().catch(() => {
        throw new Error("Something went wrong on our side. Please try again.");
      });
      if (!res.ok) {
        const code = (data.error && data.error.code) || "";
        throw new Error((messageFor && messageFor[code])
          || (data.error && data.error.message) || "Request failed");
      }
      if (data.status === "pending") {
        setStatus("", "The calendar is being updated. This page will reflect the outcome — do not retry.");
        // Hiding the armed confirm row (or any control that held focus) must
        // not dump a keyboard user back to <body>; the status sentence is
        // what to read next.
        statusEl.setAttribute("tabindex", "-1");
        statusEl.focus({ preventScroll: true });
        revealStatus();
        lockReschedule();
        waitForSettle();
        return "pending";
      }
      // A reschedule rotates the management link: the server revokes the
      // token this page was opened with, so a plain reload would land the
      // visitor on "Invalid link" right after their change succeeded. The
      // response carries the fresh link; cancel responses carry none and
      // keep this token valid, so they fall through to a plain reload.
      if (data.manage_url) { window.location.href = data.manage_url; return "done"; }
      window.location.reload();
      return "done";
    } catch (err) {
      // A raw browser error ("Failed to fetch") says nothing actionable;
      // crafted server messages already read as sentences and pass through.
      setStatus("error", err.message === "Failed to fetch"
        ? "The server could not be reached. Check your connection and try again."
        : err.message);
      revealStatus();
    }
  }

  const cancelForm = document.getElementById("cancel-form");
  if (cancelForm) {
    // Two-step inline confirm instead of a native dialog: the destructive
    // action needs a deliberate second click, without leaving the page style.
    const confirmRow = document.getElementById("cancel-confirm");
    const actionsRow = document.getElementById("cancel-actions");
    const arm = () => {
      actionsRow.hidden = true;
      confirmRow.hidden = false;
      confirmRow.querySelector("button.confirm").focus();
    };
    const disarm = () => {
      confirmRow.hidden = true;
      actionsRow.hidden = false;
      actionsRow.querySelector("button").focus();
    };
    cancelForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      if (confirmRow.hidden) { arm(); return; }
      // One POST per arm: while it flies, both confirm-row buttons lock, so
      // a double-click cannot fire a second request and "keep it" cannot
      // pretend to withdraw a cancel that is already underway.
      const confirmBtn = confirmRow.querySelector("button.confirm");
      const keepBtn = confirmRow.querySelector("button.keep");
      confirmBtn.disabled = true;
      keepBtn.disabled = true;
      postAction("cancel", {
        revision: Number(cfg.revision),
        reason: document.getElementById("cancel-reason").value,
        idempotency_key: crypto.randomUUID(),
      }).then((outcome) => {
        if (outcome !== "pending") {
          confirmBtn.disabled = false;
          keepBtn.disabled = false;
          // Disabling the focused button threw focus to <body>; hand it back
          // so a keyboard retry continues from the row it came from, with
          // the error sentence still in view.
          confirmBtn.focus({ preventScroll: true });
          return;
        }
        // The armed row below a "do not retry" status invites the exact
        // second click the copy rules out; one disabled in-flight button
        // says the same thing the status line does.
        confirmRow.hidden = true;
        actionsRow.hidden = false;
        const btn = actionsRow.querySelector("button");
        btn.disabled = true;
        btn.textContent = "Cancellation in progress…";
      });
    });
    confirmRow.querySelector("button.confirm").addEventListener("click", (ev) => {
      ev.preventDefault();
      cancelForm.requestSubmit();
    });
    confirmRow.querySelector("button.keep").addEventListener("click", (ev) => {
      ev.preventDefault();
      disarm();
    });
  }

  const reschedForm = document.getElementById("reschedule-form");
  if (reschedForm) {
    // The picker speaks the booking's display-zone wall clock (the server
    // pre-fills and labels it that way); the API wants an absolute instant.
    // Two offset passes settle DST edges: format a guess in the zone, shift,
    // then re-format to confirm the offset still holds.
    function zoneOffsetMinutes(instant, zone) {
      const dtf = new Intl.DateTimeFormat("en-US", {
        timeZone: zone, hour12: false,
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
      const parts = {};
      for (const part of dtf.formatToParts(instant)) parts[part.type] = part.value;
      const asUtc = Date.UTC(Number(parts.year), parts.month - 1, parts.day,
        parts.hour % 24, parts.minute, parts.second);
      return (asUtc - instant.getTime()) / 60000;
    }
    function wallToIso(raw, zone) {
      const naive = Date.parse(raw + ":00Z"); // wall clock, read as UTC first
      if (Number.isNaN(naive)) return null;
      let ts = naive - zoneOffsetMinutes(new Date(naive), zone) * 60000;
      ts = naive - zoneOffsetMinutes(new Date(ts), zone) * 60000;
      return new Date(ts).toISOString();
    }
    // The native input renders in the browser's locale (often 12-hour),
    // which reads oddly next to the 24-hour times on this page. The preview
    // restates the picked wall clock in this page's format and zone, so the
    // instant that will be sent is never ambiguous.
    const startInput = document.getElementById("resched-start");
    const previewEl = document.getElementById("resched-preview");
    // Wall-clock "now" in the booking's display zone, for the picker's min:
    // a start in the past can never save (the policy rejects every instant
    // before now), so the picker should not offer one.
    function wallNow(zone) {
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: zone, hour12: false,
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
      }).formatToParts(new Date());
      const get = (type) => parts.find((p) => p.type === type).value;
      const hour = String(Number(get("hour")) % 24).padStart(2, "0");
      return `${get("year")}-${get("month")}-${get("day")}T${hour}:${get("minute")}`;
    }
    const zone = cfg.timezone || "UTC";
    startInput.min = wallNow(zone);
    // The same rule as a live check, with a minute of slack for slow typing:
    // the clock moves on while the form sits open.
    function isPast(raw) {
      if (!raw) return false;
      const instant = wallToIso(raw, zone);
      return instant !== null && new Date(instant).getTime() < Date.now() - 60000;
    }
    function updatePreview() {
      if (!previewEl) return;
      const raw = startInput.value;
      const instant = raw ? wallToIso(raw, zone) : null;
      if (isPast(raw)) {
        previewEl.textContent = "That start is in the past — pick a later time.";
        previewEl.classList.add("warn");
        startInput.setAttribute("aria-invalid", "true");
        return;
      }
      previewEl.classList.remove("warn");
      startInput.removeAttribute("aria-invalid");
      if (!instant) { previewEl.textContent = ""; return; }
      const shown = new Date(instant).toLocaleString([], {
        weekday: "short", day: "numeric", month: "short",
        hour: "2-digit", minute: "2-digit",
        hour12: localStorage.getItem("sched_clock") === "12",
        timeZone: zone,
      });
      previewEl.textContent = `→ ${shown} (${zone})`;
    }
    startInput.addEventListener("input", updatePreview);
    updatePreview();
    reschedForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const raw = document.getElementById("resched-start").value;
      if (!raw) {
        setStatus("error", "Pick a start time first.");
        revealStatus();
        startInput.focus();
        return;
      }
      if (isPast(raw)) {
        setStatus("error", "That start is in the past. Pick a later time and try again.");
        revealStatus();
        startInput.focus();
        return;
      }
      const start = wallToIso(raw, cfg.timezone || "UTC") || raw;
      // Locked during the flight like the cancel row; a pending outcome
      // keeps it locked (lockReschedule owns that lock from here), an error
      // hands control back.
      const submitBtn = reschedForm.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      postAction("reschedule", {
        revision: Number(cfg.revision),
        start,
        duration: Number(document.getElementById("resched-duration").value || cfg.duration),
        idempotency_key: crypto.randomUUID(),
      }, {
        // The raw policy message says nothing actionable; "too soon" is what
        // it means for an invitee picking a new slot.
        policy_violation: "That time is too soon — the booking policy needs more notice. Pick a later start.",
      }).then((outcome) => {
        // Same as the cancel row: an error re-enables the submit and hands
        // focus back to it — a keyboard user retries from where they were,
        // not from the top of the document.
        if (!outcome) { submitBtn.disabled = false; submitBtn.focus({ preventScroll: true }); }
      });
    });
  }
})();
