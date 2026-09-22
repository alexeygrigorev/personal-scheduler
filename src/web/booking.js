/* Public booking flow. Duration first, then date and time, then details.
 * A selected slot is not reserved while the form is filled in; availability
 * is confirmed on submission and a conflict returns the visitor to fresh
 * alternatives with their entries preserved. */
(function () {
  "use strict";
  const root = document.getElementById("booking-root");
  if (!root) return;
  const cfg = JSON.parse(root.dataset.config || "{}");
  const api = cfg.apiBase || "/api/v1";

  // A raw browser error ("Failed to fetch") says nothing actionable; error
  // surfaces embed this fragment after their own context prefix.
  function why(err) {
    return err.message === "Failed to fetch"
      ? "the server could not be reached"
      : (err.message || "something went wrong");
  }

  // First visit: the server had no zone cookie yet and rendered its default,
  // so the browser's own zone wins; an explicit cookie (this visit or an
  // earlier one) always beats Intl detection.
  function browserZone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch (err) {
      return "";
    }
  }

  function hasTzCookie() {
    return /(?:^|;\s*)sched_tz=/.test(document.cookie || "");
  }

  const state = {
    duration: cfg.defaultDuration,
    timezone: (!hasTzCookie() && browserZone()) || cfg.defaultTimezone
      || browserZone() || "UTC",
    hour12: localStorage.getItem("sched_clock") === "12",
    month: null, // Date at first of visible month
    days: [],
    dayToSlots: {},
    selectedDay: "",
    selectedStart: "",
    idempotencyKey: crypto.randomUUID(),
  };

  const $ = (id) => document.getElementById(id);
  const statusEl = $("booking-status");
  const formWrap = $("details-form-wrap");
  const placeholder = $("details-placeholder");
  const timesHead = $("times-head");

  function setStatus(kind, text) {
    statusEl.className = "status" + (kind ? " " + kind : "");
    statusEl.setAttribute("role", kind === "error" ? "alert" : "status");
    statusEl.querySelector(".status-text").textContent = text;
  }

  // A gateway timeout or 5xx page arrives as HTML, not JSON; translate that
  // instead of surfacing the browser's raw parse error.
  async function readJson(res) {
    try {
      return await res.json();
    } catch (err) {
      throw new Error("Something went wrong on our side. Please try again.");
    }
  }

  // The footer line is the page's single timezone notice; keeping it glued
  // to the selector means the two can never disagree.
  function syncTzNote() {
    const note = document.getElementById("tz-note");
    if (note) note.textContent = `Times shown in ${state.timezone}`;
  }

  function showDetails(show) {
    formWrap.hidden = !show;
    placeholder.hidden = show;
  }

  function fmtTime(iso) {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: state.hour12, timeZone: state.timezone });
  }

  function viewerMonthRange() {
    const m = state.month;
    const from = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth(), 1));
    const to = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 0));
    const pad = (d) => d.toISOString().slice(0, 10);
    return { from: pad(from), to: pad(to) };
  }

  function renderSkeleton() {
    timesHead.hidden = true;
    const grid = $("day-grid");
    grid.innerHTML = "";
    for (let i = 0; i < 28; i++) {
      const block = document.createElement("div");
      block.className = "skeleton-row";
      block.setAttribute("aria-hidden", "true");
      grid.appendChild(block);
    }
    renderTimes([]);
  }

  function renderMonthLabel() {
    $("month-label").textContent = state.month.toLocaleDateString([], { month: "long", year: "numeric", timeZone: "UTC" });
  }

  function renderEmptyMonth(message) {
    timesHead.hidden = true;
    // Only renderDays would otherwise name the month, so an empty month (or a
    // failed availability load) left the nav arrows around a blank label.
    renderMonthLabel();
    const grid = $("day-grid");
    grid.innerHTML = "";
    // The status box above already says there is nothing bookable; this line
    // only tells the visitor what to do next, so it stays plain and quiet.
    const cell = document.createElement("div");
    cell.className = "empty-cell";
    cell.textContent = message || "Try another month, or use “Next available day”.";
    grid.appendChild(cell);
    renderTimes([]);
  }

  async function loadAvailability(keepForm = false) {
    if (!keepForm) showDetails(false);
    state.selectedStart = "";
    renderSkeleton();
    setStatus("", "Loading available times…");
    const { from, to } = viewerMonthRange();
    const params = new URLSearchParams({
      type: cfg.slug, duration: String(state.duration), from, to, tz: state.timezone,
    });
    let data;
    try {
      const res = await fetch(`${api}/availability?${params}`);
      data = await readJson(res);
      if (!res.ok) throw new Error((data.error && data.error.message) || "Unavailable");
    } catch (err) {
      setStatus("error", `Could not load times — ${why(err)}.`);
      renderEmptyMonth("Availability could not be loaded.");
      return;
    }
    const byDay = {};
    for (const slot of data.slots || []) {
      const day = new Date(slot.start).toLocaleDateString("en-CA", { timeZone: state.timezone });
      (byDay[day] = byDay[day] || []).push(slot);
    }
    state.days = Object.keys(byDay).sort();
    state.dayToSlots = byDay;
    if (!state.days.length) {
      setStatus("", "No bookable times in this month.");
      renderEmptyMonth();
      syncMonthNav();
      return;
    }
    setStatus("", `${state.days.length} day${state.days.length === 1 ? "" : "s"} with open times in ${state.month.toLocaleDateString([], { month: "long", timeZone: "UTC" })}.`);
    timesHead.hidden = false;
    if (!state.selectedDay || !byDay[state.selectedDay]) state.selectedDay = state.days[0];
    renderDays(state.days);
    renderTimes(state.selectedDay ? byDay[state.selectedDay] || [] : []);
    syncMonthNav();
  }

  function renderDays(days) {
    const grid = $("day-grid");
    grid.innerHTML = "";
    for (const label of ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]) {
      const head = document.createElement("span");
      head.className = "dow";
      head.textContent = label;
      head.setAttribute("aria-hidden", "true");
      grid.appendChild(head);
    }
    const { from, to } = viewerMonthRange();
    const first = new Date(from + "T12:00:00Z");
    const offset = (first.getUTCDay() + 6) % 7; // Monday-first columns
    for (let i = 0; i < offset; i++) {
      const pad = document.createElement("span");
      pad.className = "pad";
      pad.setAttribute("aria-hidden", "true");
      grid.appendChild(pad);
    }
    const cursor = new Date(from + "T12:00:00Z");
    const end = new Date(to + "T12:00:00Z");
    const available = new Set(days);
    const todayKey = new Date().toLocaleDateString("en-CA", { timeZone: state.timezone });
    while (cursor <= end) {
      const key = cursor.toISOString().slice(0, 10);
      const isToday = key === todayKey;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = String(cursor.getUTCDate());
      if (isToday) {
        btn.classList.add("today");
        btn.title = "Today";
        btn.setAttribute("aria-current", "date");
      }
      const label = new Date(key + "T12:00:00Z").toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" })
        + (isToday ? " (today)" : "");
      if (available.has(key)) {
        btn.dataset.day = key;
        btn.setAttribute("aria-pressed", key === state.selectedDay ? "true" : "false");
        btn.setAttribute("aria-label", `Bookable: ${label}`);
        btn.addEventListener("click", () => {
          state.selectedDay = key;
          state.selectedStart = "";
          showDetails(false);
          renderDays(state.days);
          renderTimes(state.dayToSlots[key] || []);
          updateSummary();
          // The grid was rebuilt, so the clicked cell is gone from the DOM;
          // focus its replacement to keep keyboard users anchored.
          const picked = $("day-grid").querySelector('button[aria-pressed="true"]');
          if (picked) picked.focus({ preventScroll: true });
        });
      } else {
        btn.disabled = true;
        btn.setAttribute("aria-label", `No availability: ${label}`);
      }
      grid.appendChild(btn);
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    renderMonthLabel();
    syncDayGridFocus(grid);
  }

  // One tab stop for the whole grid — a month is otherwise 30+ tab presses.
  // Arrow keys walk bookable days, skipping disabled cells in the direction
  // of travel, and stop at the month edge rather than jumping months.
  function syncDayGridFocus(grid) {
    const cells = [...grid.querySelectorAll("button[data-day]")];
    const anchor =
      cells.find((b) => b.getAttribute("aria-pressed") === "true") ||
      cells.find((b) => b.classList.contains("today")) ||
      cells[0];
    for (const b of cells) b.tabIndex = b === anchor ? 0 : -1;
  }

  function adjacentDay(grid, btn, delta) {
    const dir = Math.sign(delta);
    const byDay = new Map();
    grid.querySelectorAll("button[data-day]").forEach((b) => byDay.set(b.dataset.day, b));
    const cur = new Date(btn.dataset.day + "T12:00:00Z");
    const month = cur.getUTCMonth();
    for (let i = 0; i < Math.abs(delta); i++) {
      cur.setUTCDate(cur.getUTCDate() + dir);
      if (cur.getUTCMonth() !== month) return null;
    }
    while (cur.getUTCMonth() === month) {
      const cand = byDay.get(cur.toISOString().slice(0, 10));
      if (cand) return cand;
      cur.setUTCDate(cur.getUTCDate() + dir);
    }
    return null;
  }

  function renderTimes(slots, { restoreFocus = false } = {}) {
    const list = $("time-list");
    list.innerHTML = "";
    // The list itself never names the day it shows, so the header does:
    // after "Next available day" or a month jump the chosen day may not be
    // anywhere on screen.
    const dayEl = $("times-day");
    if (dayEl) {
      dayEl.textContent = state.selectedDay
        ? new Date(state.selectedDay + "T12:00:00Z").toLocaleDateString(
            [], { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" })
          + ` · ${slots.length} open time${slots.length === 1 ? "" : "s"}`
        : "";
    }
    for (const slot of slots) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = fmtTime(slot.start);
      btn.setAttribute("aria-pressed", slot.start === state.selectedStart ? "true" : "false");
      btn.setAttribute("aria-label", `${fmtTime(slot.start)} – ${fmtTime(slot.end)}`);
      btn.addEventListener("click", () => {
        state.selectedStart = slot.start;
        state.selectedEnd = slot.end;
        renderTimes(slots, { restoreFocus: true });
        updateSummary();
        showDetails(true);
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        // "start", not "nearest": on a phone the form is a tall block below
        // the times, and nearest lands on its bottom edge with the summary
        // and first fields off-screen. On desktop the sticky panel is already
        // in view, so this is a no-op there.
        formWrap.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
      });
      li.appendChild(btn);
      list.appendChild(li);
    }
    // Re-rendering replaces the clicked button, which would drop a keyboard
    // user's focus to <body>; put it on the control they were using.
    if (restoreFocus) {
      const selected = list.querySelector('button[aria-pressed="true"]');
      if (selected) selected.focus({ preventScroll: true });
    }
  }

  function fmtDuration(mins) {
    if (mins < 60) return `${mins} min`;
    const hours = mins / 60;
    return hours === 1 ? "1 hour" : `${hours} hours`;
  }

  function zoneOffsetLabel(iso) {
    // Offset must describe the selected zone, not the browser's own zone.
    try {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: state.timezone, timeZoneName: "shortOffset",
      }).formatToParts(new Date(iso));
      const named = parts.find((p) => p.type === "timeZoneName");
      if (named) return named.value.replace(/^GMT/, "UTC");
    } catch (err) { /* older engine: numeric fallback below */ }
    const offset = -new Date(iso).getTimezoneOffset() / 60;
    return `UTC${offset >= 0 ? "+" : ""}${offset}`;
  }

  function updateSummary() {
    const el = $("selection-summary");
    if (state.selectedStart) {
      const day = new Date(state.selectedStart).toLocaleDateString([], {
        weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: state.timezone,
      });
      el.replaceChildren(
        Object.assign(document.createElement("span"), {
          className: "sum-line strong", textContent: day,
        }),
        Object.assign(document.createElement("span"), {
          className: "sum-line", textContent:
            `${fmtTime(state.selectedStart)} – ${fmtTime(state.selectedEnd)} · ${fmtDuration(state.duration)} · ${state.timezone} (${zoneOffsetLabel(state.selectedStart)})`,
        }),
      );
    } else {
      el.textContent = "No time selected yet.";
    }
  }

  function fieldError(id, message) {
    const el = $(id);
    if (el) el.textContent = message || "";
  }

  // Error spans follow the input id: f-name → err-name, q-<id> → err-q-<id>.
  function errIdFor(input) {
    return "err-" + input.id.replace(/^f-/, "");
  }

  // Instant feedback for the obvious cases so an empty submit does not burn
  // a round-trip; the server stays the authority and overwrites these.
  function validateDetails() {
    let firstInvalid = null;
    document.querySelectorAll("#details-form [required]").forEach((input) => {
      const value = input.value.trim();
      let message = "";
      if (!value) {
        message = input.type === "email" ? "Please enter your email address." : "This field is required.";
      } else if (input.type === "email" && !/^\S+@\S+\.\S+$/.test(value)) {
        message = "That does not look like an email address.";
      }
      fieldError(errIdFor(input), message);
      if (message) {
        input.setAttribute("aria-invalid", "true");
        firstInvalid = firstInvalid || input;
      } else {
        input.removeAttribute("aria-invalid");
      }
    });
    if (firstInvalid) firstInvalid.focus();
    return !firstInvalid;
  }

  // maxlength silently swallows keystrokes at the cap; surface the last
  // stretch so the limit arrives while there is still room to edit.
  function updateCharCount(input) {
    const field = input.closest(".field");
    const counter = field && field.querySelector(".char-count");
    if (!counter) return;
    const max = parseInt(input.getAttribute("maxlength"), 10) || 0;
    const len = input.value.length;
    if (!max || len < max * 0.8) { counter.textContent = ""; return; }
    counter.textContent = len + " / " + max;
    counter.classList.toggle("warn", len >= max);
  }

  function rememberTimezone() {
    // Preference only, not a credential: readable by script, scoped to this
    // site, so the next page render already knows the visitor's zone.
    document.cookie = "sched_tz=" + encodeURIComponent(state.timezone) +
      "; Path=/; Max-Age=31536000; SameSite=Lax";
  }

  function restoreConfirmButton() {
    const btn = $("confirm-btn");
    btn.disabled = false;
    btn.textContent = "Confirm booking";
  }

  async function submitBooking(ev) {
    ev.preventDefault();
    if (!validateDetails()) return;
    const payload = {
      type: cfg.slug,
      duration: state.duration,
      start: state.selectedStart,
      name: $("f-name").value,
      email: $("f-email").value,
      notes: $("f-notes").value,
      answers: {},
      tz: state.timezone,
      idempotency_key: state.idempotencyKey,
    };
    document.querySelectorAll("[data-question]").forEach((input) => {
      payload.answers[input.dataset.question] = input.value;
    });
    const btn = $("confirm-btn");
    btn.disabled = true;
    btn.textContent = "Confirming…";
    setStatus("", "Confirming your booking…");
    try {
      const res = await fetch(`${api}/bookings`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await readJson(res);
      if (res.status === 409 && data.error && data.error.code === "slot_unavailable") {
        restoreConfirmButton();
        await loadAvailability(true); // refreshed alternatives, form entries preserved
        // Set after the refresh so the reload's status text can't overwrite it.
        setStatus("error", "That time was just taken. Your details are kept — pick a new time below.");
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        document.getElementById("time-list").scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
        return;
      }
      if (!res.ok) {
        const fields = (data.error && data.error.fields) || {};
        const rows = [
          ["f-name", "err-name", "name"],
          ["f-email", "err-email", "email"],
          ["f-notes", "err-notes", "agenda"],
          // Autofill and programmatic input bypass maxlength; the server's
          // per-question verdicts must still land on their own field.
          ...[...document.querySelectorAll("#details-form [data-question]")].map(
            (el) => [el.id, "err-" + el.id, "q:" + el.dataset.question]),
        ];
        for (const [inputId, errId, key] of rows) {
          const message = fields[key];
          fieldError(errId, message);
          const input = $(inputId);
          if (message && input) input.setAttribute("aria-invalid", "true");
        }
        throw new Error((data.error && data.error.message) || "Booking failed");
      }
      if (data.status === "pending") {
        setStatus("", "Your request is being reconciled with the calendar. This page will update — do not book again.");
        pollOperation(data.operation_id);
        return;
      }
      window.location.href = data.manage_url;
    } catch (err) {
      restoreConfirmButton();
      setStatus("error", `Could not confirm the booking — ${why(err)}.`);
    }
  }

  async function pollOperation(operationId) {
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      let data = null;
      try {
        const res = await fetch(`${api}/operations/${operationId}`);
        data = await res.json();
      } catch (err) {
        // A dropped or non-JSON probe must not orphan a settled booking;
        // skip it and let the remaining ticks carry on.
        continue;
      }
      if (data.state === "succeeded" && data.booking) {
        window.location.href = data.receipt_url || `${api}/operations/${operationId}`;
        return;
      }
      if (data.state === "failed") {
        setStatus("error", "The booking could not be completed. Please try again.");
        restoreConfirmButton();
        return;
      }
    }
    // Giving up must not strand the visitor behind a dead "Confirming…"
    // button: resubmitting is safe (the idempotency key resolves to the same
    // operation, never a second booking).
    setStatus("error", "Still reconciling — check your email for the confirmation, or try again.");
    restoreConfirmButton();
  }

  function nextAvailableDay() {
    if (state.days.length) {
      const next = state.days.find((d) => d > state.selectedDay) || state.days[0];
      state.selectedDay = next;
      renderDays(state.days);
      renderTimes(state.dayToSlots[next] || []);
      // Land keyboard users on the day they just jumped to; the day cell
      // announces itself ("Bookable: …") and Tabs straight into its times.
      const picked = $("day-grid").querySelector('button[aria-pressed="true"]');
      if (picked) picked.focus({ preventScroll: true });
    } else {
      const m = new Date(state.month);
      m.setUTCMonth(m.getUTCMonth() + 1);
      state.month = m;
      loadAvailability();
    }
  }

  // The roving tabindex: arrows and Home/End move focus between bookable
  // days; picking still happens on click or Enter.
  $("day-grid").addEventListener("keydown", (ev) => {
    const btn = ev.target.closest("button[data-day]");
    if (!btn) return;
    const grid = ev.currentTarget;
    const deltas = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    let next;
    if (ev.key in deltas) {
      next = adjacentDay(grid, btn, deltas[ev.key]);
    } else if (ev.key === "Home" || ev.key === "End") {
      const cells = grid.querySelectorAll("button[data-day]");
      next = ev.key === "Home" ? cells[0] : cells[cells.length - 1];
    } else {
      return;
    }
    if (!next) return;
    ev.preventDefault();
    btn.tabIndex = -1;
    next.tabIndex = 0;
    next.focus({ preventScroll: true });
  });

  // Duration selector comes before date and time choices; changing it
  // recomputes days and starts and clears a now-invalid selection.
  document.querySelectorAll('input[name="duration"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      state.duration = Number(radio.value);
      state.selectedDay = "";
      state.selectedStart = "";
      showDetails(false);
      updateSummary();
      loadAvailability();
    });
  });
  $("tz-select").addEventListener("change", (ev) => {
    // A timezone change never silently keeps an absolute instant: the
    // selection clears and the visitor reviews the re-rendered times.
    state.timezone = ev.target.value;
    rememberTimezone();
    syncTzNote();
    state.selectedStart = "";
    showDetails(false);
    updateSummary();
    loadAvailability();
  });
  function monthKey(d) {
    return `${d.getUTCFullYear()}-${d.getUTCMonth()}`;
  }

  function syncMonthNav() {
    const current = monthKey(new Date(Date.UTC(new Date().getFullYear(), new Date().getMonth(), 1)));
    $("prev-month").disabled = monthKey(state.month) <= current;
  }

  $("clock-toggle").addEventListener("click", () => {
    state.hour12 = !state.hour12;
    localStorage.setItem("sched_clock", state.hour12 ? "12" : "24");
    $("clock-toggle").textContent = state.hour12 ? "Use 24-hour clock" : "Use 12-hour clock";
    renderTimes(state.selectedDay ? state.dayToSlots[state.selectedDay] || [] : []);
    updateSummary();
  });
  $("prev-month").addEventListener("click", () => {
    const m = new Date(state.month); m.setUTCMonth(m.getUTCMonth() - 1); state.month = m; loadAvailability();
  });
  $("next-month").addEventListener("click", () => {
    const m = new Date(state.month); m.setUTCMonth(m.getUTCMonth() + 1); state.month = m; loadAvailability();
  });
  $("next-day").addEventListener("click", nextAvailableDay);
  $("details-form").addEventListener("submit", submitBooking);
  $("back-to-times").addEventListener("click", () => {
    showDetails(false);
    // Hiding the form would drop a keyboard user's focus to <body>; anchor
    // it on the time they had picked, where they continue from anyway.
    const picked = document.querySelector('#time-list button[aria-pressed="true"]');
    if (picked) picked.focus({ preventScroll: true });
  });
  // Typing clears the field's error right away, not just on the next submit.
  document.getElementById("details-form").addEventListener("input", (ev) => {
    const input = ev.target;
    updateCharCount(input);
    if (!input.matches("[required]")) return;
    input.removeAttribute("aria-invalid");
    fieldError(errIdFor(input), "");
  });

  const now = new Date();
  state.month = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  rememberTimezone();
  const tzSelect = $("tz-select");
  if (![...tzSelect.options].some((o) => o.value === state.timezone)) {
    const opt = document.createElement("option");
    opt.value = state.timezone; opt.textContent = state.timezone;
    tzSelect.appendChild(opt);
  }
  tzSelect.value = state.timezone;
  $("clock-toggle").textContent = state.hour12 ? "Use 24-hour clock" : "Use 12-hour clock";
  syncTzNote();
  updateSummary();
  syncMonthNav();
  loadAvailability();
})();
