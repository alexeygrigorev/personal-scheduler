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
    statusEl.querySelector(".status-text").textContent = text;
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
        return;
      }
      window.location.reload();
    } catch (err) {
      setStatus("error", err.message);
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
      postAction("cancel", {
        revision: Number(cfg.revision),
        reason: document.getElementById("cancel-reason").value,
        idempotency_key: crypto.randomUUID(),
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
        startInput.focus();
        return;
      }
      if (isPast(raw)) {
        setStatus("error", "That start is in the past. Pick a later time and try again.");
        startInput.focus();
        return;
      }
      const start = wallToIso(raw, cfg.timezone || "UTC") || raw;
      postAction("reschedule", {
        revision: Number(cfg.revision),
        start,
        duration: Number(document.getElementById("resched-duration").value || cfg.duration),
        idempotency_key: crypto.randomUUID(),
      }, {
        // The raw policy message says nothing actionable; "too soon" is what
        // it means for an invitee picking a new slot.
        policy_violation: "That time is too soon — the booking policy needs more notice. Pick a later start.",
      });
    });
  }
})();
