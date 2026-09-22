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

  async function postAction(action, payload) {
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
      if (!res.ok) throw new Error((data.error && data.error.message) || "Request failed");
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
    reschedForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const raw = document.getElementById("resched-start").value;
      const start = wallToIso(raw, cfg.timezone || "UTC") || raw;
      postAction("reschedule", {
        revision: Number(cfg.revision),
        start,
        duration: Number(document.getElementById("resched-duration").value || cfg.duration),
        idempotency_key: crypto.randomUUID(),
      });
    });
  }
})();
