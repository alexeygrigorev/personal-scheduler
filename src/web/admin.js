/* Host console: every mutation carries the expected version so a stale
 * browser cannot overwrite newer configuration. */
(function () {
  "use strict";
  const root = document.getElementById("admin-root");
  if (!root) return;
  const base = "/admin/api";

  async function call(path, options) {
    const res = await fetch(base + path, {
      headers: { "content-type": "application/json" },
      ...(options || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data.error && data.error.message) || `Request failed (${res.status})`);
    return data;
  }

  function el(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function show(section) {
    document.querySelectorAll(".admin-section").forEach((s) => { s.hidden = s.id !== section; });
    document.querySelectorAll("[data-tab]").forEach((b) => {
      b.setAttribute("aria-pressed", b.dataset.tab === section ? "true" : "false");
    });
    loaders[section]();
  }

  async function loadOverview() {
    const data = await call("/overview");
    const box = document.getElementById("overview");
    box.innerHTML = "";
    const health = data.health || {};
    box.appendChild(el("p", `Upcoming bookings: ${data.upcoming_count}`));
    box.appendChild(el("p", `Pending operations: ${data.pending_operations}`));
    box.appendChild(el("p", `Calendar: ${health.calendar || "unknown"} (last check ${health.last_check || "never"})`));
    box.appendChild(el("p", `Dapier: ${health.dapier || "unknown"}`));
    box.appendChild(el("p", `New bookings: ${data.paused ? "PAUSED" : "open"}`));
    const btn = el("button", data.paused ? "Resume new bookings" : "Pause new bookings");
    btn.className = "btn secondary";
    btn.addEventListener("click", async () => {
      await call("/settings", { method: "PUT", body: JSON.stringify({ pause_new_bookings: !data.paused }) });
      loadOverview();
    });
    box.appendChild(btn);
  }

  async function loadTypes() {
    const data = await call("/event-types");
    const box = document.getElementById("types");
    box.innerHTML = "";
    const table = el("table");
    table.className = "admin";
    const head = el("tr");
    ["Title", "Slug", "Duration", "Visibility", ""].forEach((h) => head.appendChild(el("th", h)));
    table.appendChild(head);
    for (const t of data.event_types || []) {
      const row = el("tr");
      row.appendChild(el("td", t.title));
      row.appendChild(el("td", t.slug));
      row.appendChild(el("td", t.duration_mode === "fixed" ? `${t.fixed_duration_min} min` : (t.allowed_durations || []).join("/")));
      row.appendChild(el("td", t.visibility));
      const actions = el("td");
      const toggle = el("button", t.visibility === "disabled" ? "Enable" : "Disable");
      toggle.className = "btn secondary";
      toggle.addEventListener("click", async () => {
        await call(`/event-types/${t.id}`, {
          method: "PUT",
          body: JSON.stringify({
            visibility: t.visibility === "disabled" ? "listed" : "disabled",
            expected_version: t.version,
          }),
        });
        loadTypes();
      });
      actions.appendChild(toggle);
      const preview = el("a", "Preview");
      preview.href = `/${t.slug}`;
      preview.style.marginLeft = "0.5rem";
      actions.appendChild(preview);
      row.appendChild(actions);
      table.appendChild(row);
    }
    box.appendChild(table);
  }

  async function loadBookings() {
    const data = await call("/bookings?status=confirmed");
    const box = document.getElementById("bookings");
    box.innerHTML = "";
    const table = el("table");
    table.className = "admin";
    const head = el("tr");
    ["When", "Type", "Invitee", "Status", ""].forEach((h) => head.appendChild(el("th", h)));
    table.appendChild(head);
    for (const b of data.bookings || []) {
      const row = el("tr");
      row.appendChild(el("td", b.start_iso));
      row.appendChild(el("td", b.event_type_id));
      row.appendChild(el("td", `${b.invitee_name} <${b.invitee_email}>`));
      row.appendChild(el("td", b.status));
      const actions = el("td");
      const cancel = el("button", "Cancel");
      cancel.className = "btn secondary";
      cancel.addEventListener("click", async () => {
        if (!window.confirm(`Cancel ${b.reference}?`)) return;
        await call(`/bookings/${b.id}/cancel`, {
          method: "POST",
          body: JSON.stringify({ revision: b.revision, idempotency_key: crypto.randomUUID() }),
        });
        loadBookings();
      });
      actions.appendChild(cancel);
      row.appendChild(actions);
      table.appendChild(row);
    }
    box.appendChild(table);
  }

  async function loadSettings() {
    const data = await call("/settings");
    const box = document.getElementById("settings");
    box.innerHTML = "";
    const host = data.host || {};
    const form = el("form");
    const fields = ["display_name", "timezone", "public_base_url", "contact_fallback", "host_notification_email"];
    for (const name of fields) {
      const label = el("label", name);
      const input = el("input");
      input.name = name;
      input.value = host[name] || "";
      input.style.display = "block";
      label.appendChild(input);
      form.appendChild(label);
    }
    const save = el("button", "Save settings");
    save.className = "btn";
    form.appendChild(save);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const payload = {};
      fields.forEach((n) => { payload[n] = form.elements[n].value; });
      await call("/settings", { method: "PUT", body: JSON.stringify(payload) });
      alert("Saved");
    });
    box.appendChild(form);
  }

  const loaders = { overview: loadOverview, types: loadTypes, bookings: loadBookings, settings: loadSettings };
  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.addEventListener("click", () => show(b.dataset.tab));
  });
  show("overview");
})();
