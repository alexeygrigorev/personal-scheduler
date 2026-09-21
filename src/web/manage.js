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
    statusEl.textContent = text;
  }

  async function postAction(action, payload) {
    setStatus("", "Working…");
    try {
      const res = await fetch(`${api}/manage/${token}/${action}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
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
    cancelForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      if (!window.confirm("Cancel this booking?")) return;
      postAction("cancel", {
        revision: Number(cfg.revision),
        reason: document.getElementById("cancel-reason").value,
        idempotency_key: crypto.randomUUID(),
      });
    });
  }

  const reschedForm = document.getElementById("reschedule-form");
  if (reschedForm) {
    reschedForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      postAction("reschedule", {
        revision: Number(cfg.revision),
        start: document.getElementById("resched-start").value,
        duration: Number(document.getElementById("resched-duration").value || cfg.duration),
        idempotency_key: crypto.randomUUID(),
      });
    });
  }
})();
