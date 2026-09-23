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
    // Callers close the sentence with their own period; a message that
    // already ends in one would print doubled.
    const raw = err.message === "Failed to fetch"
      ? "the server could not be reached"
      : (err.message || "something went wrong");
    return raw.replace(/[.!?]+$/, "");
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
  // instead of surfacing the browser's raw parse error. A failed response
  // with no readable body still names its status, matching the parseable
  // path's "answered with an error (503)".
  async function readJson(res) {
    try {
      return await res.json();
    } catch (err) {
      if (!res.ok) throw new Error(`the server answered with an error (${res.status})`);
      throw new Error("the server sent a response we could not read");
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
    // The times section used to sit out the skeleton entirely and materialize
    // at once when the fetch landed, shoving everything below it down by the
    // height of the list. The head stays up and shimmer chips hold the space.
    timesHead.hidden = false;
    const hint = $("times-day");
    if (hint) hint.textContent = "";
    const grid = $("day-grid");
    grid.innerHTML = "";
    // The skeleton must occupy exactly the rows the real grid will — the
    // static weekday header, the month's leading pads, one chip per day. A
    // partial skeleton is worse than none: load completion would still shove
    // the times section down by whatever height it failed to reserve.
    for (const label of ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]) {
      const head = document.createElement("span");
      head.className = "dow";
      head.textContent = label;
      head.setAttribute("aria-hidden", "true");
      grid.appendChild(head);
    }
    const first = new Date(viewerMonthRange().from + "T12:00:00Z");
    const offset = (first.getUTCDay() + 6) % 7; // Monday-first columns
    for (let i = 0; i < offset; i++) {
      const pad = document.createElement("span");
      pad.className = "pad";
      pad.setAttribute("aria-hidden", "true");
      grid.appendChild(pad);
    }
    const daysInMonth = new Date(
      Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
    for (let i = 0; i < daysInMonth; i++) {
      const block = document.createElement("span");
      block.className = "skeleton-row";
      block.setAttribute("aria-hidden", "true");
      grid.appendChild(block);
    }
    const list = $("time-list");
    list.innerHTML = "";
    for (let i = 0; i < 12; i++) {
      const block = document.createElement("li");
      block.className = "skeleton-row";
      block.setAttribute("aria-hidden", "true");
      list.appendChild(block);
    }
    // Same reasoning as renderEmptyMonth: only renderDays names the month,
    // and the arrows should not loom over a blank label while loading.
    renderMonthLabel();
  }

  function renderMonthLabel() {
    $("month-label").textContent = state.month.toLocaleDateString([], { month: "long", year: "numeric", timeZone: "UTC" });
  }

  function renderEmptyMonth(message, retry = false) {
    timesHead.hidden = true;
    // Only renderDays would otherwise name the month, so an empty month (or a
    // failed availability load) left the nav arrows around a blank label.
    renderMonthLabel();
    const grid = $("day-grid");
    grid.innerHTML = "";
    // renderSkeleton parks shimmer chips in the times list; an empty or
    // failed month must clear them, or the shimmer outlives the load.
    const list = $("time-list");
    list.innerHTML = "";
    // The status box above already says there is nothing bookable; this line
    // only tells the visitor what to do next, so it stays plain and quiet.
    const cell = document.createElement("div");
    cell.className = "empty-cell";
    if (retry) {
      // A failure with no action to press invites a manual reload, so the
      // Retry sits where the reading is. The banner above already carries
      // the explanation; the cell repeats it only when a caller wants a
      // quieter wording here.
      cell.className = "empty-cell with-action";
      if (message) cell.textContent = message;
      const retryBtn = document.createElement("button");
      retryBtn.type = "button";
      retryBtn.className = "btn secondary sm";
      retryBtn.textContent = "Retry";
      retryBtn.addEventListener("click", () => {
        loadAvailability().then(focusAfterJump);
      });
      cell.appendChild(retryBtn);
    } else {
      cell.textContent = message || "Try another month, or use “Next available day”.";
      // Focusable so a retry that lands here can anchor the keyboard user on
      // the guidance instead of dropping them to <body>.
      cell.tabIndex = -1;
    }
    grid.appendChild(cell);
    renderTimes([]);
  }

  // Two quick month flips fire two loads; whichever response arrives last
  // must win, not whichever was asked for last but answered first. Each
  // load tags itself, and only the newest tag may touch the grid.
  let loadSeq = 0;

  // Whichever way a month jump or a retry lands, keyboard focus stays in
  // the grid: the fresh Retry button on another failure, the picked day (or
  // the first bookable one) on success, the empty-month guidance when
  // success finds nothing bookable. Without the handback the keyboard lands
  // on <body>, a spectator to the state it just asked for.
  function focusAfterJump() {
    const again = document.querySelector(".empty-cell.with-action button");
    const day = document.querySelector('#day-grid button[aria-pressed="true"]')
      || document.querySelector("#day-grid button[data-day]");
    const target = again || day || document.querySelector("#day-grid .empty-cell");
    if (target) target.focus({ preventScroll: true });
  }

  function setNavLoading(busy) {
    $("next-month").disabled = busy;
    $("next-day").disabled = busy;
    if (busy) $("prev-month").disabled = true;
    else syncMonthNav();
  }

  async function loadAvailability(keepForm = false, keepSelection = false) {
    const seq = ++loadSeq;
    if (!keepForm) showDetails(false);
    if (!keepSelection) {
      state.selectedStart = "";
      // keepForm (a taken-slot retry) leaves the form on screen; its summary
      // card must stop advertising the slot that just failed.
      updateSummary();
    }
    renderSkeleton();
    // The taken-slot verdict lives in the times head; a fresh load must not
    // resurface it when the head un-hides with new times.
    const staleVerdict = document.getElementById("times-verdict");
    if (staleVerdict) staleVerdict.remove();
    setStatus("", "Loading available times…");
    setNavLoading(true);
    $("day-grid").setAttribute("aria-busy", "true");
    const { from, to } = viewerMonthRange();
    const params = new URLSearchParams({
      type: cfg.slug, duration: String(state.duration), from, to, tz: state.timezone,
    });
    let data;
    try {
      const res = await fetch(`${api}/availability?${params}`);
      data = await readJson(res);
      // A silent API body must not push a bare code into the sentence; the
      // number stays, phrased as something a person can read aloud.
      if (!res.ok) throw new Error((data.error && data.error.message) || `the server answered with an error (${res.status})`);
    } catch (err) {
      if (seq !== loadSeq) return; // a newer load owns the grid now
      setNavLoading(false);
      $("day-grid").removeAttribute("aria-busy");
      setStatus("error", `Could not load times — ${why(err)}.`);
      renderEmptyMonth("", true); // the banner above already carries the explanation
      return;
    }
    if (seq !== loadSeq) return; // a newer load owns the grid now
    setNavLoading(false);
    $("day-grid").removeAttribute("aria-busy");
    const byDay = {};
    for (const slot of data.slots || []) {
      const day = new Date(slot.start).toLocaleDateString("en-CA", { timeZone: state.timezone });
      (byDay[day] = byDay[day] || []).push(slot);
    }
    state.days = Object.keys(byDay).sort();
    state.dayToSlots = byDay;
    // A re-anchored load's verdict on a kept instant: still offered means the
    // pick survives — the grid just presses the slot's new wall-clock face
    // and the summary speaks the new dialect. Gone means the plain cleared
    // state returns; a summary advertising a slot the grid no longer lists
    // would read as a booking the page lost. The API names each slot in the
    // requested zone's offset, so the kept instant is matched as an instant,
    // not as the string it wore under the previous zone.
    if (keepSelection && state.selectedStart) {
      const keptMs = new Date(state.selectedStart).getTime();
      const keptDay = new Date(state.selectedStart).toLocaleDateString("en-CA", { timeZone: state.timezone });
      const keptSlot = (byDay[keptDay] || []).find((s) => new Date(s.start).getTime() === keptMs);
      if (keptSlot) {
        state.selectedDay = keptDay;
        state.selectedStart = keptSlot.start;
        state.selectedEnd = keptSlot.end;
        updateSummary();
      } else {
        state.selectedStart = "";
        showDetails(false);
        updateSummary();
      }
    }
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
          // The taken-slot alarm speaks for one submission: its verdict sits
          // above the times and its banner echoes the same ask. A day pick
          // replaces the list both were written for, so — like a fresh slot
          // pick — they retire instead of reading as if the new day were
          // suspect too. Day counts and load failures are not ours to clobber.
          const verdict = document.getElementById("times-verdict");
          if (verdict) verdict.remove();
          const statusText = document.querySelector("#booking-status .status-text");
          if (statusText &&
              (statusText.textContent.includes("just taken") ||
               statusText.textContent.startsWith("Could not confirm") ||
               statusText.textContent.includes("No time is picked yet"))) {
            setStatus("", "Day picked — now choose a time.");
          }
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
        // Inert either way (disabled buttons take no focus), but the DOM
        // should not advertise twenty-odd tab stops the roving grid below
        // will never manage.
        btn.tabIndex = -1;
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
        // The taken-slot verdict asked for exactly one thing: a fresh pick.
        // Once a pick lands, the message must not outlive the choice it was
        // about and read as if the new slot were suspect too.
        const verdict = document.getElementById("times-verdict");
        if (verdict) verdict.remove();
        // Banners above the grid speak for one attempt: the taken-slot
        // verdict's ask is fulfilled by the pick itself, and a "could not
        // confirm" error describes the previous submission — left up, both
        // read as if the new slot were suspect too. Once a pick lands they
        // step down to a calm confirmation. Other statuses (day counts,
        // load failures, a reconciling booking's do-not-rebook warning)
        // are still live and stay.
        const statusText = document.querySelector("#booking-status .status-text");
        if (statusText &&
            (statusText.textContent.includes("just taken") ||
             statusText.textContent.startsWith("Could not confirm") ||
             statusText.textContent.includes("No time is picked yet"))) {
          setStatus("", "Time picked — confirm your details below.");
        }
        renderTimes(slots, { restoreFocus: true });
        updateSummary();
        showDetails(true);
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        // "start", not "nearest": on a phone the form is a tall block below
        // the times, and nearest lands on its bottom edge with the summary
        // and first fields off-screen. The heading, not the form body, is
        // the anchor — the body's edge parked the heading under the sticky
        // site head, clipping it; the heading carries the [id] scroll margin,
        // so it stops below the bar with the summary and first fields still
        // on screen. When the heading is already fully on screen — desktop,
        // where the sticky panel never left — scrolling anyway only yanks
        // the calendar out from under the visitor and parks their cursor
        // over a random slot, so the scroll keeps quiet there.
        const detailsAnchor = document.getElementById("details-head") || formWrap;
        const box = detailsAnchor.getBoundingClientRect();
        const alreadyInView = box.top >= 0 && box.bottom <= window.innerHeight;
        if (!alreadyInView) {
          detailsAnchor.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
        }
      });
      li.appendChild(btn);
      list.appendChild(li);
    }
    // One tab stop for the whole list — a busy day is otherwise a dozen Tab
    // presses before the form. The selected slot anchors; else the first.
    const buttons = [...list.querySelectorAll("button")];
    const anchor =
      buttons.find((b) => b.getAttribute("aria-pressed") === "true") || buttons[0];
    for (const b of buttons) b.tabIndex = b === anchor ? 0 : -1;
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
    // Offset must describe the selected zone, not the browser's own zone,
    // and read like the picker's labels: two-digit hours (+05:30), so a
    // half-hour zone doesn't switch dialects between select and summary.
    try {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: state.timezone, timeZoneName: "shortOffset",
      }).formatToParts(new Date(iso));
      const named = parts.find((p) => p.type === "timeZoneName");
      if (named) {
        const m = named.value.replace(/^GMT/, "UTC").match(/^UTC([+-])(\d{1,2})(?::(\d{2}))?$/);
        if (m) return `UTC${m[1]}${m[2].padStart(2, "0")}:${m[3] ?? "00"}`;
        return "UTC+00:00";
      }
    } catch (err) { /* older engine: numeric fallback below */ }
    const offset = -new Date(iso).getTimezoneOffset() / 60;
    const sign = offset < 0 ? "-" : "+";
    const hh = String(Math.floor(Math.abs(offset))).padStart(2, "0");
    const mm = String(Math.round((Math.abs(offset) % 1) * 60)).padStart(2, "0");
    return `UTC${sign}${hh}:${mm}`;
  }

  function updateSummary() {
    const el = $("selection-summary");
    if (state.selectedStart) {
      const day = new Date(state.selectedStart).toLocaleDateString([], {
        weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: state.timezone,
      });
      // The range and the duration travel as one unit each — their joins
      // are no-break spaces, and the separator rides inside so a break
      // lands between chunks, never inside "30 min". The zone chunk wraps
      // at its own slashes and underscores (zero-width breaks), since the
      // card's anywhere rule would otherwise split it after any letter.
      // The offset label is its own no-wrap unit: a break after "UTC-"
      // strands "07:00)" at a line's head.
      const part = (className, textContent) =>
        Object.assign(document.createElement("span"), { className, textContent });
      const nb = (s) => s.replaceAll(" ", "\u00A0");
      // A distant zone can push the slot's end past midnight — a +14 visitor
      // booking a Berlin afternoon ends at 00:00 their Thursday. Without a
      // flag, "23:30 – 00:00" under Wednesday's date quietly books a visit
      // on a different day than the card advertises. The flag wraps as its
      // own unit: glued to the no-break range it forms one chunk wider than
      // a 320px card, and the browser's emergency break lands after the dash.
      const dayKey = (iso) => new Date(iso).toLocaleDateString("en-CA", { timeZone: state.timezone });
      const range = `${fmtTime(state.selectedStart)} – ${fmtTime(state.selectedEnd)}`;
      const when = dayKey(state.selectedEnd) !== dayKey(state.selectedStart)
        ? `${nb(range)} ${nb("(+1 day) ·")}`
        : `${nb(`${range} ·`)}`;
      const line = part("sum-line", "");
      const zone = part("sum-zone", "");
      zone.append(
        document.createTextNode(`${state.timezone.replace(/([/_])/g, "$1\u200B")} (`),
        part("sum-off", zoneOffsetLabel(state.selectedStart)),
        document.createTextNode(")"),
      );
      line.append(
        part("sum-when", `${when} `),
        part("sum-dur", `${nb(`${fmtDuration(state.duration)} ·`)} `),
        zone,
      );
      el.replaceChildren(part("sum-line strong", day), line);
    } else {
      el.textContent = "No time selected yet.";
    }
  }

  function fieldError(id, message) {
    const el = $(id);
    if (el) el.textContent = message || "";
  }

  // Error spans follow the input id: f-name → err-name, q-<id> → err-q-<id>.
  // Radio groups share one verdict span named after the question, since the
  // individual buttons carry no id of their own.
  function errIdFor(input) {
    if (input.type === "radio" || input.hasAttribute("data-other-input")) {
      return "err-q-" + input.dataset.question;
    }
    return "err-" + input.id.replace(/^f-/, "");
  }

  // Instant feedback for the obvious cases so an empty submit does not burn
  // a round-trip; the server stays the authority and overwrites these.
  function validateDetails() {
    let firstInvalid = null;
    document.querySelectorAll("#details-form [required]").forEach((input) => {
      if (input.type === "radio") {
        // One verdict per choice group: the question turns red and the
        // shared error span speaks. Ringing every unchecked radio reads
        // as four errors where the visitor made one.
        const field = input.closest(".choice-field");
        const group = document.querySelectorAll(
          `#details-form input[type="radio"][data-question="${input.dataset.question}"]`);
        if ([...group].some((r) => r.checked)) {
          if (field) field.removeAttribute("aria-invalid");
          fieldError(errIdFor(input), "");
          return;
        }
        fieldError(errIdFor(input), "This field is required.");
        if (field) field.setAttribute("aria-invalid", "true");
        firstInvalid = firstInvalid || input;
        return;
      }
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
    // "Other" collects the typed text: a checked Other radio with an empty
    // field is an unfinished answer, so it asks for the words.
    document.querySelectorAll("#details-form [data-other-radio]:checked").forEach((radio) => {
      const otherInput = radio.closest(".choice-field").querySelector("[data-other-input]");
      const message = otherInput && otherInput.value.trim() ? "" : "Please specify your answer.";
      fieldError(errIdFor(radio), message);
      if (otherInput) {
        if (message) otherInput.setAttribute("aria-invalid", "true");
        else otherInput.removeAttribute("aria-invalid");
      }
      if (message) firstInvalid = firstInvalid || otherInput;
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
    if (!state.selectedStart) {
      // A 409 or a day switch can leave the revealed form without a pick;
      // POSTing anyway only earns a vague server 400 about the start. The
      // ask points at the pick that still exists — this day's slots, another
      // day with open times, or the next month when none do — and the
      // keyboard lands where picking continues.
      const fresh = document.querySelector("#time-list button");
      const monthName = state.month.toLocaleDateString([], { month: "long", timeZone: "UTC" });
      const ask = fresh ? "Pick a time below."
        : state.days.length ? "Pick a day with open times in the calendar."
        : `No open times left in ${monthName} — try the next month.`;
      const say = `No time is picked yet — your details are kept. ${ask}`;
      setStatus("error", say);
      let verdict = document.getElementById("times-verdict");
      if (!verdict) {
        verdict = document.createElement("p");
        verdict.id = "times-verdict";
        verdict.className = "times-verdict";
        verdict.tabIndex = -1;
        timesHead.appendChild(verdict);
      }
      timesHead.hidden = false;
      verdict.textContent = say;
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      timesHead.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
      if (fresh) fresh.focus({ preventScroll: true });
      else verdict.focus({ preventScroll: true });
      return;
    }
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
    document.querySelectorAll("#details-form [data-question]").forEach((el) => {
      if (el.type === "radio") {
        // Named choices speak for themselves; the empty-valued Other radio
        // never carries an answer.
        if (el.checked && el.value) payload.answers[el.dataset.question] = el.value;
      } else if (el.hasAttribute("data-other-input")) {
        // The Other field only counts while its radio is picked — a stale
        // half-typed answer must not win over a named choice.
        const radio = el.closest(".choice-field").querySelector("[data-other-radio]");
        if (radio && radio.checked && el.value.trim()) {
          payload.answers[el.dataset.question] = el.value.trim();
        }
      } else {
        payload.answers[el.dataset.question] = el.value;
      }
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
        // The instruction has to match what the reload actually found: when
        // the month came back empty, "pick a new time below" is a promise
        // nothing below can keep — the next-month arrow in the head is the
        // way out that still exists, so the words point there instead.
        const fresh = document.querySelector("#time-list button");
        const monthName = state.month.toLocaleDateString([], { month: "long", timeZone: "UTC" });
        // Set after the refresh so the reload's status text can't overwrite it.
        // Both banners read as one sentence — on a tall screen the visitor
        // sees them stacked, and two wordings of the same ask read like two
        // different problems — so the sentence is built once and spoken
        // twice, matching what the reload actually found.
        const ask = fresh ? "Pick a new time below."
          : `No open times left in ${monthName} — try the next month.`;
        setStatus("error", `That time was just taken — your details are kept. ${ask}`);
        // The scroll lands on the times list and always leaves the status
        // banner above the day grid off-screen; the verdict must also sit
        // where the visitor is now looking, right above the fresh slots.
        let verdict = document.getElementById("times-verdict");
        if (!verdict) {
          verdict = document.createElement("p");
          verdict.id = "times-verdict";
          verdict.className = "times-verdict";
          verdict.tabIndex = -1;
          timesHead.appendChild(verdict);
        }
        // When the reload finds no bookable times the whole head is hidden,
        // which would park the verdict invisible and unfocusable — the exact
        // moment the visitor most needs it. The head stays up; its hint
        // already says the day has no open times.
        timesHead.hidden = false;
        verdict.textContent = `That time was just taken — your details are kept. ${ask}`;
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        timesHead.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
        // Focus stayed on Confirm, which the scroll just carried off-screen on
        // a phone; the first open slot is where picking continues. With no
        // fresh slot to take focus, the verdict is the thing to read next.
        if (fresh) {
          // A focus() riding a click response never matches :focus-visible,
          // so the slot would take focus invisibly; the hint class borrows
          // the keyboard ring for this landing. It only ever styles while
          // focused, so it can safely outlive the pick it announced.
          fresh.classList.add("focus-hint");
          fresh.focus({ preventScroll: true });
        }
        else verdict.focus({ preventScroll: true });
        return;
      }
      if (!res.ok) {
        const fields = (data.error && data.error.fields) || {};
        const rows = [
          ["f-name", "err-name", "name"],
          ["f-email", "err-email", "email"],
          ["f-notes", "err-notes", "agenda"],
          // Autofill and programmatic input bypass maxlength; the server's
          // per-question verdicts must still land on their own field. Radio
          // buttons have no id, so their group span is named by question.
          ...[...document.querySelectorAll("#details-form [data-question]")].map((el) => [
            el.id,
            (el.type === "radio" || el.hasAttribute("data-other-input"))
              ? "err-q-" + el.dataset.question : "err-" + el.id,
            "q:" + el.dataset.question]),
        ];
        for (const [inputId, errId, key] of rows) {
          const message = fields[key];
          fieldError(errId, message);
          if (!message) continue;
          const input = inputId ? $(inputId) : null;
          if (input) {
            input.setAttribute("aria-invalid", "true");
            continue;
          }
          // Radio buttons (and Other's field) carry no id: a server verdict
          // on the question wears the same one-verdict grammar as client
          // validation — the group turns, not every radio.
          const el = document.querySelector(
            `#details-form input[data-question="${key.slice(2)}"]`);
          const field = el && el.closest(".choice-field");
          if (field) field.setAttribute("aria-invalid", "true");
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
      // Disabling the Confirm button threw focus to <body>, and the verdict
      // renders at the top of a long form. Manage scrolls its status box to
      // the reader on a failure; booking adds the keyboard anchor, so the
      // next Tab starts at the verdict instead of the document top.
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const box = $("booking-status");
      box.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
      box.focus({ preventScroll: true });
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
      const next = state.days.find((d) => d > state.selectedDay);
      if (next) {
        state.selectedDay = next;
        renderDays(state.days);
        renderTimes(state.dayToSlots[next] || []);
        // Land keyboard users on the day they just jumped to; the day cell
        // announces itself ("Bookable: …") and Tabs straight into its times.
        const picked = $("day-grid").querySelector('button[aria-pressed="true"]');
        if (picked) picked.focus({ preventScroll: true });
        return;
      }
      // Already on this month's last open day: "next" is the next month,
      // whose first open day loadAvailability() selects on arrival and the
      // handback anchors the keyboard on.
      const m = new Date(state.month);
      m.setUTCMonth(m.getUTCMonth() + 1);
      state.month = m;
      loadAvailability().then(focusAfterJump);
    } else {
      const m = new Date(state.month);
      m.setUTCMonth(m.getUTCMonth() + 1);
      state.month = m;
      loadAvailability().then(focusAfterJump);
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
    // Claimed keys are consumed even when there is nowhere to go — an edge
    // arrow must not fall through to page scroll mid-walk.
    ev.preventDefault();
    if (!next) return;
    btn.tabIndex = -1;
    next.tabIndex = 0;
    // preventScroll skips the browser's reveal; scroll the focus target
    // into view with the smallest scroll, or keyboard focus lands unseen.
    next.focus({ preventScroll: true });
    next.scrollIntoView({ block: "nearest" });
  });

  // The times list makes the same bargain as the day grid: one tab stop for
  // the whole list. It is a single column, so every arrow steps one slot and
  // Home/End jump to the ends.
  $("time-list").addEventListener("keydown", (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    const slots = [...ev.currentTarget.querySelectorAll("button")];
    const deltas = { ArrowUp: -1, ArrowDown: 1, ArrowLeft: -1, ArrowRight: 1 };
    let next;
    if (ev.key in deltas) {
      next = slots[slots.indexOf(btn) + deltas[ev.key]];
    } else if (ev.key === "Home" || ev.key === "End") {
      next = ev.key === "Home" ? slots[0] : slots[slots.length - 1];
    } else {
      return;
    }
    // Claimed keys are consumed even when there is nowhere to go — an edge
    // arrow must not fall through to page scroll mid-walk.
    ev.preventDefault();
    if (!next) return;
    btn.tabIndex = -1;
    next.tabIndex = 0;
    // preventScroll skips the browser's reveal; scroll the focus target
    // into view with the smallest scroll, or keyboard focus lands unseen.
    next.focus({ preventScroll: true });
    next.scrollIntoView({ block: "nearest" });
  });

  // Duration selector comes before date and time choices; changing it
  // recomputes the times under the new footprint, and the picked start
  // survives only when the re-anchored grid still offers it (the same
  // grammar as the timezone switch) — the day itself is re-validated by
  // the reload, so exploring a duration never throws the visitor back to
  // the top of the month they were halfway down.
  document.querySelectorAll('input[name="duration"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      state.duration = Number(radio.value);
      loadAvailability(true, true);
    });
  });
  $("tz-select").addEventListener("change", (ev) => {
    // A timezone change re-anchors the wall clocks, so the picked instant is
    // re-validated against the fresh grid: when the new zone still offers
    // it, the pick survives and only the summary's dialect changes — the
    // question a visitor asks the picker is "what does my slot look like
    // here", not "make me pick again". An instant that vanished (taken, or
    // the grid came back empty) falls back to the cleared state.
    state.timezone = ev.target.value;
    rememberTimezone();
    syncTzNote();
    loadAvailability(true, true);
  });
  // Absolute month index, not a "2026-10"-style key: string keys compare
  // lexicographically, so October–December sorted before September and the
  // back arrow locked in the last quarter of every year.
  function monthIndex(d) {
    return d.getUTCFullYear() * 12 + d.getUTCMonth();
  }

  function syncMonthNav() {
    const current = monthIndex(new Date(Date.UTC(new Date().getFullYear(), new Date().getMonth(), 1)));
    $("prev-month").disabled = monthIndex(state.month) <= current;
  }

  $("clock-toggle").addEventListener("click", () => {
    state.hour12 = !state.hour12;
    localStorage.setItem("sched_clock", state.hour12 ? "12" : "24");
    $("clock-toggle").textContent = state.hour12 ? "Use 24-hour clock" : "Use 12-hour clock";
    renderTimes(state.selectedDay ? state.dayToSlots[state.selectedDay] || [] : []);
    updateSummary();
  });
  $("prev-month").addEventListener("click", () => {
    const m = new Date(state.month); m.setUTCMonth(m.getUTCMonth() - 1); state.month = m; loadAvailability().then(focusAfterJump);
  });
  $("next-month").addEventListener("click", () => {
    const m = new Date(state.month); m.setUTCMonth(m.getUTCMonth() + 1); state.month = m; loadAvailability().then(focusAfterJump);
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
    if (input.matches("[data-other-input]")) {
      input.removeAttribute("aria-invalid");
      fieldError(errIdFor(input), "");
    }
    if (!input.matches("[required]")) return;
    input.removeAttribute("aria-invalid");
    fieldError(errIdFor(input), "");
  });
  // A choice group lives by the picked answer: the Other field exists only
  // while Other is picked — revealed up front it reads as a mystery required
  // input and invites stale half-answers. Choosing "Other" hands focus
  // straight to the text field so the answer starts where the visitor's
  // intent already is. Typing there means Other — the radio checks itself,
  // so an answer is never silently dropped just because the visitor skipped
  // the radio.
  // Whether picking Other also hands off focus is a question of intent.
  // Pointer picks and a deliberate Space are aim; Chrome additionally fires
  // a trusted click for arrow-key navigation, so riding click would strand
  // the walk mid-group. Mark the aiming gestures and hand off from change —
  // which opens the field — so focus lands after the reveal, never on a
  // still-hidden input.
  let pickedOther = null;
  let pickedOtherAt = 0;
  document.getElementById("details-form").addEventListener("pointerdown", (ev) => {
    const radio = (ev.target.matches?.("input[type=radio][data-other-radio]") && ev.target)
      || ev.target.closest?.("label")?.querySelector("[data-other-radio]");
    if (radio) { pickedOther = radio; pickedOtherAt = ev.timeStamp; }
  }, true);
  document.getElementById("details-form").addEventListener("keydown", (ev) => {
    if ((ev.key === " " || ev.key === "Spacebar")
        && ev.target.matches("input[type=radio][data-other-radio]")) {
      pickedOther = ev.target; pickedOtherAt = ev.timeStamp;
    }
  }, true);
  // The reveal follows the group's state, whatever drove it: a radio click,
  // a load with an answer already checked, or filler text landing in the
  // input without any click at all — fillers fire input, or change alone.
  const syncOther = (field) => {
    field.classList.toggle("other-open",
      !!field.querySelector("[data-other-radio]:checked"));
  };
  document.getElementById("details-form").addEventListener("change", (ev) => {
    const field = ev.target.closest(".choice-field");
    if (!field) return;
    field.removeAttribute("aria-invalid");
    if (ev.target.matches("[data-other-input]")) {
      if (ev.target.value.trim() !== "") {
        const radio = field.querySelector("[data-other-radio]");
        if (radio) radio.checked = true;
      }
      syncOther(field);
      return;
    }
    if (!ev.target.matches("input[type=radio][data-question]")) return;
    syncOther(field);
    if (ev.target !== pickedOther || ev.timeStamp - pickedOtherAt > 750) return;
    pickedOther = null;
    const otherInput = field.querySelector("[data-other-input]");
    if (otherInput) otherInput.focus();
  });
  document.querySelectorAll(".choice-field").forEach(syncOther);
  document.getElementById("details-form").addEventListener("input", (ev) => {
    if (!ev.target.matches("[data-other-input]")) return;
    const field = ev.target.closest(".choice-field");
    if (ev.target.value.trim() !== "") {
      const radio = field.querySelector("[data-other-radio]");
      if (radio) radio.checked = true;
    }
    syncOther(field);
  });

  // The picker arrives as a plain <select> — the no-JS answer and the one
  // store of the chosen value. Ten host picks cannot cover a visitor
  // booking from Halifax or Hyderabad, so with scripting on the select is
  // upgraded in place: a search field filters the browser's whole zone
  // database while the hidden select still receives every change event,
  // leaving the cookie, the note, and the availability reload untouched.
  function upgradeTimezonePicker(select) {
    if (select.hidden) return; // the upgrade already ran
    const label = select.closest("label");
    const combo = document.createElement("span");
    combo.className = "tz-combo";
    const input = document.createElement("input");
    input.type = "text";
    input.id = "tz-input";
    input.className = "tz-input";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", "tz-listbox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-describedby", "tz-note");
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "Search timezones…";
    const caret = document.createElement("span");
    caret.className = "tz-caret";
    caret.setAttribute("aria-hidden", "true");
    caret.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
      + 'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
      + 'stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
    const list = document.createElement("ul");
    list.id = "tz-listbox";
    list.className = "tz-list";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Timezones");
    list.hidden = true;
    combo.append(input, caret, list);
    select.after(combo);
    select.hidden = true;
    if (label) label.htmlFor = input.id;

    const curated = [...select.options].map((o) => o.value);
    let zones = curated;
    try {
      // The browser's database is the authority on what exists here; the
      // host's picks with the visitor's own zone lead the empty view and
      // search reaches everything behind them.
      zones = curated.concat(Intl.supportedValuesOf("timeZone")
        .filter((z) => !curated.includes(z)));
    } catch (err) { /* older engine: the curated list still searches */ }

    const offCache = new Map();
    function zoneOffset(zone) {
      let off = offCache.get(zone);
      if (off !== undefined) return off;
      try {
        const name = new Intl.DateTimeFormat("en", {
          timeZone: zone, timeZoneName: "shortOffset",
        }).formatToParts().find((p) => p.type === "timeZoneName").value;
        // One dialect with the picker labels: (UTC+05:30), never GMT+5:30.
        const m = name.match(/^GMT([+-])(\d{1,2})(?::(\d{2}))?$/);
        off = m ? `UTC${m[1]}${m[2].padStart(2, "0")}:${m[3] ?? "00"}` : "UTC+00:00";
      } catch (err) { off = ""; }
      offCache.set(zone, off);
      return off;
    }
    function offsetMinutes(off) {
      const m = off.match(/^UTC([+-])(\d{2}):(\d{2})$/);
      return m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) : 0;
    }
    function displayValue(zone) {
      const off = zoneOffset(zone);
      return off ? `${zone} (${off})` : zone;
    }

    // Four hundred zones render as wall; sixty render as a menu. The cap
    // only bounds the paint — search keeps reaching everything behind it,
    // and the tail row says so instead of pretending the list ended.
    const MAX_ROWS = 60;
    const norm = (s) => s.toLowerCase().replace(/_/g, " ");
    let shown = [];
    let activeIndex = -1;

    // A browser's zone database can still carry pre-1994 names — this is
    // how Asia/Calcutta lists without Asia/Kolkata — so a visitor typing
    // the modern city would be told nothing matches. Alias tokens ride in
    // the search text only; the value stays the name the browser vouches
    // for, which the server's zoneinfo resolves the same way.
    const ZONE_ALIASES = {
      "Asia/Calcutta": "kolkata",
      "Asia/Bombay": "mumbai",
      "Asia/Madras": "chennai",
      "Asia/Dacca": "dhaka",
      "Asia/Saigon": "ho chi minh",
      "Asia/Rangoon": "yangon",
      "Asia/Katmandu": "kathmandu",
      "Asia/Chungking": "chongqing",
      "Asia/Macao": "macau",
      "Europe/Kiev": "kyiv",
      "America/Godthab": "nuuk",
    };

    function search(query) {
      const q = norm(query.trim());
      if (!q) return curated.slice();
      const hits = zones.filter((z) => {
        const off = zoneOffset(z);
        const hay = norm(`${z} ${ZONE_ALIASES[z] || ""} ${off} ${(off || "").slice(3)}`);
        return hay.includes(q);
      });
      // Near zones surface first: current-offset order, name as tiebreak.
      return hits.sort((a, b) =>
        offsetMinutes(zoneOffset(a)) - offsetMinutes(zoneOffset(b))
        || (a < b ? -1 : a > b ? 1 : 0));
    }

    function renderList() {
      const rows = search(input.value);
      shown = rows.slice(0, MAX_ROWS);
      list.textContent = "";
      shown.forEach((zone, i) => {
        const li = document.createElement("li");
        li.id = `tz-opt-${i}`;
        li.setAttribute("role", "option");
        li.dataset.zone = zone;
        li.className = "tz-row";
        const name = document.createElement("span");
        name.className = "tz-zone";
        name.textContent = zone;
        const off = document.createElement("span");
        off.className = "tz-off";
        off.textContent = zoneOffset(zone);
        li.append(name, off);
        if (zone === select.value) li.setAttribute("aria-selected", "true");
        list.append(li);
      });
      if (rows.length > MAX_ROWS) {
        const more = document.createElement("li");
        more.className = "tz-more";
        more.setAttribute("role", "presentation");
        more.textContent = `${rows.length - MAX_ROWS} more — keep typing to narrow`;
        list.append(more);
      }
      if (!rows.length) {
        const none = document.createElement("li");
        none.className = "tz-empty";
        none.setAttribute("role", "presentation");
        none.textContent = `No timezone matches “${input.value.trim()}”`;
        list.append(none);
      }
    }

    function setActive(i) {
      const row = list.querySelector(".tz-row.is-active");
      if (row) row.classList.remove("is-active");
      if (!shown.length) {
        activeIndex = -1;
        input.removeAttribute("aria-activedescendant");
        return;
      }
      activeIndex = ((i % shown.length) + shown.length) % shown.length;
      const el = document.getElementById(`tz-opt-${activeIndex}`);
      if (!el) return;
      el.classList.add("is-active");
      input.setAttribute("aria-activedescendant", el.id);
      el.scrollIntoView({ block: "nearest" });
    }

    function expand() {
      renderList();
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }
    function collapse() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      activeIndex = -1;
      input.value = displayValue(state.timezone);
      resting = true;
    }

    function choose(zone) {
      let opt = [...select.options].find((o) => o.value === zone);
      if (!opt) {
        // The select stays the one value store, so a zone the server never
        // listed joins it before the change event — every listener reading
        // the select keeps seeing a real, selected option.
        opt = document.createElement("option");
        opt.value = zone;
        opt.textContent = displayValue(zone);
        select.append(opt);
      }
      select.value = zone;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      collapse();
    }

    input.value = displayValue(state.timezone);
    // True while the field holds the resting label rather than a query —
    // the state Escape and clicking away leave behind. The next character
    // the visitor types starts a fresh search instead of appending to the
    // label, which could never match anything.
    let resting = false;
    // The field's resting text is the display label, and a label is not a
    // query — searching it would answer "no matches". So opening starts a
    // fresh lookup over the full list; leaving without a pick restores the
    // label in collapse().
    function freshLookup() {
      input.value = "";
      resting = false;
      expand();
      const at = shown.indexOf(state.timezone);
      setActive(at >= 0 ? at : 0);
    }
    input.addEventListener("focus", freshLookup);
    // Escape and a mouse pick leave the field focused with the list shut,
    // and a second focus() never fires — so a click must reopen it.
    input.addEventListener("click", () => {
      if (list.hidden) freshLookup();
    });
    input.addEventListener("input", () => {
      if (resting) {
        resting = false;
        const label = displayValue(state.timezone);
        if (input.value.startsWith(label)) input.value = input.value.slice(label.length);
      }
      expand();
      setActive(0);
    });
    input.addEventListener("keydown", (ev) => {
      if (resting && ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        input.value = "";
        resting = false;
      }
      const open = !list.hidden;
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        if (!open) {
          expand();
          const at = shown.indexOf(state.timezone);
          setActive(at >= 0 ? at : 0);
        } else setActive(activeIndex + 1);
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        if (!open) {
          expand();
          const at = shown.indexOf(state.timezone);
          setActive(at >= 0 ? at : shown.length - 1);
        } else setActive(activeIndex - 1);
      } else if (ev.key === "Home" && open) {
        ev.preventDefault();
        setActive(0);
      } else if (ev.key === "End" && open) {
        ev.preventDefault();
        setActive(shown.length - 1);
      } else if (ev.key === "Enter") {
        if (open && activeIndex >= 0 && shown[activeIndex]) {
          ev.preventDefault();
          choose(shown[activeIndex]);
        }
      } else if (ev.key === "Escape" && open) {
        ev.preventDefault();
        collapse();
      }
    });
    list.addEventListener("mousedown", (ev) => {
      const row = ev.target.closest('[role="option"]');
      if (!row) return;
      ev.preventDefault(); // the pick never steals the field's focus
      choose(row.dataset.zone);
    });
    input.addEventListener("blur", () => {
      if (!list.hidden) collapse();
    });
  }

  const now = new Date();
  state.month = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  rememberTimezone();
  const tzSelect = $("tz-select");
  const spokenOffset = (t) => {
    const m = String(t).match(/\(UTC([+-])(\d{2}):(\d{2})\)/);
    return m ? Number(m[1] + (Number(m[2]) * 60 + Number(m[3]))) : 0;
  };
  const own = [...tzSelect.options].findIndex((o) => o.value === state.timezone);
  if (own === -1) {
    // The browser-reported zone is the visitor's own zone; it leads the list,
    // matching the server render that pins the cookie zone first. A legacy
    // alias (Asia/Calcutta) can arrive here, and its label is computed in the
    // browser because the browser is the one that vouches for the zone.
    const opt = document.createElement("option");
    opt.value = state.timezone;
    let label = state.timezone;
    try {
      const name = new Intl.DateTimeFormat("en", {
        timeZone: state.timezone, timeZoneName: "shortOffset",
      }).formatToParts().find((p) => p.type === "timeZoneName").value;
      // Intl reads "GMT+5:30"; the server-rendered labels pad to +05:30
      const m = name.match(/^GMT([+-])(\d{1,2})(?::(\d{2}))?$/);
      const off = m ? `${m[1]}${m[2].padStart(2, "0")}:${m[3] ?? "00"}` : "+00:00";
      label = `${state.timezone} (UTC${off})`;
    } catch (err) { /* unformattable zone: the bare name still selects */ }
    opt.textContent = label;
    tzSelect.insertBefore(opt, tzSelect.firstChild);
  } else if (own > 0) {
    // An offered zone is selected but, on a first visit, nothing pinned it
    // server-side yet — the cookie is only written now. Moving it up keeps
    // the picker's promise (the visitor's zone leads) on the first look
    // instead of from the next page on.
    tzSelect.insertBefore(tzSelect.options[own], tzSelect.firstChild);
    // The server's pin (its default, on a first visit) keeps the seat the
    // move vacated, so the rest would read +02:00, -07:00, -05:00... Re-read
    // them in offset order — the scan for the daylight neighbor stays true.
    const rest = [...tzSelect.options].slice(1)
      .sort((a, b) => spokenOffset(a.textContent) - spokenOffset(b.textContent));
    for (const o of rest) tzSelect.appendChild(o);
  }
  tzSelect.value = state.timezone;
  upgradeTimezonePicker(tzSelect);
  $("clock-toggle").textContent = state.hour12 ? "Use 24-hour clock" : "Use 12-hour clock";
  syncTzNote();
  updateSummary();
  syncMonthNav();
  loadAvailability();
})();