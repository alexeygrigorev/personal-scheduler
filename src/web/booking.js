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

  const state = {
    duration: cfg.defaultDuration,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    hour12: false,
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

  function setStatus(kind, text) {
    statusEl.className = "status" + (kind ? " " + kind : "");
    statusEl.textContent = text;
    statusEl.setAttribute("role", kind === "error" ? "alert" : "status");
  }

  function fmtTime(iso) {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: state.hour12, timeZone: state.timezone });
  }
  function fmtDay(iso) {
    return new Date(iso).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", timeZone: state.timezone });
  }

  function viewerMonthRange() {
    const m = state.month;
    const from = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth(), 1));
    const to = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 0));
    const pad = (d) => d.toISOString().slice(0, 10);
    return { from: pad(from), to: pad(to) };
  }

  async function loadAvailability() {
    setStatus("", "Loading available times…");
    state.selectedStart = "";
    const { from, to } = viewerMonthRange();
    const params = new URLSearchParams({
      type: cfg.slug, duration: String(state.duration), from, to, tz: state.timezone,
    });
    let data;
    try {
      const res = await fetch(`${api}/availability?${params}`);
      data = await res.json();
      if (!res.ok) throw new Error((data.error && data.error.message) || "Unavailable");
    } catch (err) {
      setStatus("error", `Could not load times: ${err.message}.`);
      renderDays([]);
      renderTimes([]);
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
      setStatus("", "No bookable times in this month. Try the next available day.");
    } else {
      setStatus("", `${state.days.length} day(s) with availability. Times shown in ${state.timezone}.`);
      if (!state.selectedDay || !byDay[state.selectedDay]) state.selectedDay = state.days[0];
    }
    renderDays(state.days);
    renderTimes(state.selectedDay ? byDay[state.selectedDay] || [] : []);
  }

  function renderDays(days) {
    const grid = $("day-grid");
    grid.innerHTML = "";
    const { from, to } = viewerMonthRange();
    const cursor = new Date(from + "T12:00:00Z");
    const end = new Date(to + "T12:00:00Z");
    const available = new Set(days);
    while (cursor <= end) {
      const key = cursor.toISOString().slice(0, 10);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = String(cursor.getUTCDate());
      const label = new Date(key + "T12:00:00Z").toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" });
      if (available.has(key)) {
        btn.setAttribute("aria-pressed", key === state.selectedDay ? "true" : "false");
        btn.setAttribute("aria-label", `Bookable: ${label}`);
        btn.addEventListener("click", () => {
          state.selectedDay = key;
          state.selectedStart = "";
          renderDays(state.days);
          renderTimes(state.dayToSlots[key] || []);
          updateSummary();
        });
      } else {
        btn.disabled = true;
        btn.setAttribute("aria-label", `No availability: ${label}`);
      }
      grid.appendChild(btn);
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    $("month-label").textContent = state.month.toLocaleDateString([], { month: "long", year: "numeric", timeZone: "UTC" });
  }

  function renderTimes(slots) {
    const list = $("time-list");
    list.innerHTML = "";
    for (const slot of slots) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = `${fmtTime(slot.start)} – ${fmtTime(slot.end)}`;
      btn.setAttribute("aria-pressed", slot.start === state.selectedStart ? "true" : "false");
      btn.addEventListener("click", () => {
        state.selectedStart = slot.start;
        state.selectedEnd = slot.end;
        renderTimes(slots);
        updateSummary();
        formWrap.hidden = false;
        formWrap.scrollIntoView({ block: "nearest" });
      });
      li.appendChild(btn);
      list.appendChild(li);
    }
  }

  function updateSummary() {
    const el = $("selection-summary");
    if (state.selectedStart) {
      const offset = -new Date(state.selectedStart).getTimezoneOffset() / 60;
      el.textContent = `${fmtDay(state.selectedStart)}, ${fmtTime(state.selectedStart)} – ${fmtTime(state.selectedEnd)} (${state.timezone}, UTC${offset >= 0 ? "+" : ""}${offset}) · ${state.duration} min`;
    } else {
      el.textContent = "No time selected yet.";
    }
  }

  function fieldError(id, message) {
    const el = $(id);
    if (el) el.textContent = message || "";
  }

  async function submitBooking(ev) {
    ev.preventDefault();
    ["err-name", "err-email", "err-notes"].forEach((id) => fieldError(id, ""));
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
      const data = await res.json();
      if (res.status === 409 && data.error && data.error.code === "slot_unavailable") {
        btn.disabled = false;
        btn.textContent = "Confirm booking";
        setStatus("error", "That time was just taken. Your details are kept — pick a new time below.");
        await loadAvailability(); // refreshed alternatives, form entries preserved
        return;
      }
      if (!res.ok) {
        const fields = (data.error && data.error.fields) || {};
        fieldError("err-name", fields.name);
        fieldError("err-email", fields.email);
        fieldError("err-notes", fields.agenda);
        throw new Error((data.error && data.error.message) || "Booking failed");
      }
      if (data.status === "pending") {
        setStatus("", "Your request is being reconciled with the calendar. This page will update — do not book again.");
        pollOperation(data.operation_id);
        return;
      }
      window.location.href = data.manage_url;
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "Confirm booking";
      setStatus("error", err.message);
    }
  }

  async function pollOperation(operationId) {
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const res = await fetch(`${api}/operations/${operationId}`);
      const data = await res.json();
      if (data.state === "succeeded" && data.booking) {
        window.location.href = data.receipt_url || `${api}/operations/${operationId}`;
        return;
      }
      if (data.state === "failed") {
        setStatus("error", "The booking could not be completed. Please try again.");
        document.getElementById("confirm-btn").disabled = false;
        return;
      }
    }
    setStatus("error", "Still reconciling. Check your email for the confirmation.");
  }

  function nextAvailableDay() {
    if (state.days.length) {
      const next = state.days.find((d) => d > state.selectedDay) || state.days[0];
      state.selectedDay = next;
      renderDays(state.days);
      renderTimes(state.dayToSlots[next] || []);
    } else {
      const m = new Date(state.month);
      m.setUTCMonth(m.getUTCMonth() + 1);
      state.month = m;
      loadAvailability();
    }
  }

  // Duration selector comes before date and time choices; changing it
  // recomputes days and starts and clears a now-invalid selection.
  document.querySelectorAll('input[name="duration"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      state.duration = Number(radio.value);
      state.selectedDay = "";
      state.selectedStart = "";
      formWrap.hidden = true;
      updateSummary();
      loadAvailability();
    });
  });
  $("tz-select").addEventListener("change", (ev) => {
    // A timezone change never silently keeps an absolute instant: the
    // selection clears and the visitor reviews the re-rendered times.
    state.timezone = ev.target.value;
    state.selectedStart = "";
    formWrap.hidden = true;
    updateSummary();
    loadAvailability();
  });
  $("clock-toggle").addEventListener("click", () => {
    state.hour12 = !state.hour12;
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
  $("back-to-times").addEventListener("click", () => { formWrap.hidden = true; });

  const now = new Date();
  state.month = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const tzSelect = $("tz-select");
  if (![...tzSelect.options].some((o) => o.value === state.timezone)) {
    const opt = document.createElement("option");
    opt.value = state.timezone; opt.textContent = state.timezone;
    tzSelect.appendChild(opt);
  }
  tzSelect.value = state.timezone;
  updateSummary();
  loadAvailability();
})();
