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

  // A raw browser error ("Failed to fetch") says nothing actionable; every
  // error surface embeds this fragment after its own context prefix.
  function why(err) {
    return err.message === "Failed to fetch"
      ? "the server could not be reached"
      : (err.message || "something went wrong");
  }

  function badge(kind, text) {
    const b = el("span", text);
    b.className = "badge" + (kind ? " " + kind : "");
    return b;
  }

  // The schedule's zone (Settings) governs every time shown here, and the
  // 12/24-hour choice follows the same localStorage key the booking page
  // persists, so the console reads like the pages visitors see.
  let settingsCache = null;
  async function hostSettings() {
    if (!settingsCache) {
      try { settingsCache = await call("/settings"); }
      catch (err) { settingsCache = {}; }
      const tz = (settingsCache.host || {}).timezone || "";
      const note = document.getElementById("tz-note");
      if (tz && note) note.textContent = `Times shown in ${tz} — the zone from Settings`;
    }
    return settingsCache || {};
  }

  function fmtWhen(iso) {
    if (!iso) return "";
    // Stored instants always carry a UTC offset; a zoneless value would be
    // read as browser-local and shift with whoever opens the console, so it
    // is pinned to UTC first and the Settings zone below stays authoritative.
    const text = /[zZ]$|[+-]\d{2}:\d{2}$/.test(String(iso)) ? String(iso) : `${iso}Z`;
    const d = new Date(text);
    if (isNaN(d)) return String(iso || "");
    const host = settingsCache && (settingsCache.host || {});
    return d.toLocaleString([], {
      weekday: "short", day: "numeric", month: "short",
      hour: "2-digit", minute: "2-digit",
      hour12: localStorage.getItem("sched_clock") === "12",
      ...(host.timezone ? { timeZone: host.timezone } : {}),
    });
  }

  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

  function fmtDuration(mins) {
    if (mins < 60) return `${mins} min`;
    const hours = mins / 60;
    return hours === 1 ? "1 hour" : `${hours} hours`;
  }

  function statTile(label, valueContent, meta) {
    const tile = el("div");
    tile.className = "stat";
    tile.appendChild(el("p", label)).className = "stat-label";
    const value = el("p");
    value.className = "stat-value";
    if (typeof valueContent === "string") value.appendChild(el("span", valueContent));
    else value.appendChild(valueContent);
    tile.appendChild(value);
    if (meta) tile.appendChild(el("p", meta)).className = "stat-meta";
    return tile;
  }

  // "Not verified yet" is a neutral state, not a failure: red is reserved
  // for connections that were checked and failed.
  function healthBadge(state) {
    if (state === "ok") return badge("ok", "Connected");
    if (state === "error" || state === "failed") return badge("danger", "Error");
    return badge("", "Not verified");
  }

  function show(section) {
    document.querySelectorAll(".admin-section").forEach((s) => { s.hidden = s.id !== section; });
    document.querySelectorAll("[data-tab]").forEach((b) => {
      b.setAttribute("aria-pressed", b.dataset.tab === section ? "true" : "false");
    });
    // A slow or failed fetch must never leave the tab silently blank: show
    // skeletons while loading, and on failure say so with a way back in.
    const box = document.getElementById(section);
    box.innerHTML = "";
    const loading = el("div");
    loading.className = "tab-loading";
    loading.setAttribute("role", "status");
    loading.setAttribute("aria-label", "Loading");
    for (let i = 0; i < 4; i++) loading.appendChild(el("div", "")).className = "skeleton-row";
    box.appendChild(loading);
    loaders[section]().catch((err) => {
      box.innerHTML = "";
      const panel = el("div");
      panel.className = "empty-state";
      panel.setAttribute("role", "alert");
      panel.appendChild(el("p", `Could not load this section — ${why(err)}.`));
      const retry = el("button", "Retry");
      retry.className = "btn secondary sm";
      retry.addEventListener("click", () => show(section));
      panel.appendChild(retry);
      box.appendChild(panel);
    });
  }

  function tableView(headers) {
    const wrap = el("div");
    wrap.className = "table-scroll";
    const table = el("table");
    table.className = "admin";
    const thead = el("thead");
    const head = el("tr");
    headers.forEach(([label, cls]) => {
      const th = el("th", label);
      if (cls) th.className = cls;
      head.appendChild(th);
    });
    thead.appendChild(head);
    table.appendChild(thead);
    const tbody = el("tbody");
    table.appendChild(tbody);
    wrap.appendChild(table);
    return { wrap, tbody };
  }

  function emptyRow(tbody, span, text) {
    const row = el("tr");
    const cell = el("td", text);
    cell.colSpan = span;
    cell.className = "table-empty";
    row.appendChild(cell);
    tbody.appendChild(row);
  }

  async function loadOverview() {
    const [data, settings, typeData, upcoming] = await Promise.all([
      call("/overview"),
      hostSettings(),
      call("/event-types").catch(() => ({})),
      call("/bookings?status=confirmed").catch(() => ({})),
    ]);
    const titles = new Map((typeData.event_types || []).map((t) => [t.id, t.title]));
    const health = data.health || {};
    const box = document.getElementById("overview");
    box.innerHTML = "";
    const grid = el("div");
    grid.className = "stat-grid";
    grid.appendChild(statTile("Upcoming bookings", String(data.upcoming_count)));
    grid.appendChild(statTile("Pending operations", String(data.pending_operations),
      data.pending_operations > 0 ? "Being reconciled with the calendar" : ""));
    grid.appendChild(statTile("Calendar", healthBadge(health.calendar || "unknown"),
      health.last_check ? `Last check ${fmtWhen(health.last_check)}` : "Not checked yet"));
    grid.appendChild(statTile("Dapier", healthBadge(health.dapier || "unknown"),
      "Provider connection"));
    grid.appendChild(statTile("New bookings", badge(data.paused ? "danger" : "ok", data.paused ? "Paused" : "Open")));
    box.appendChild(grid);
    // The pause control lives in the schedule panel's header row: a button
    // floating between the stat tiles and the panel read as an orphan with
    // no object, and it governs exactly what this panel shows.
    const btn = el("button", data.paused ? "Resume new bookings" : "Pause new bookings");
    btn.className = "btn secondary sm";
    btn.addEventListener("click", async () => {
      await call("/settings", { method: "PUT", body: JSON.stringify({ pause_new_bookings: !data.paused }) });
      loadOverview();
    });
    const panel = el("div");
    panel.className = "panel next-bookings";
    const panelHead = el("div");
    panelHead.className = "panel-head";
    panelHead.appendChild(el("h2", "Next bookings"));
    panelHead.appendChild(btn);
    panel.appendChild(panelHead);
    const list = (upcoming.bookings || []).slice(0, 5);
    if (!list.length) {
      const empty = el("p", "Nothing booked ahead.");
      empty.className = "hint";
      panel.appendChild(empty);
    }
    for (const b of list) {
      const row = el("div");
      row.className = "next-booking-row";
      const when = el("strong", fmtWhen(b.start_iso));
      row.appendChild(when);
      const who = el("span", `${b.invitee_name || ""} · ${titles.get(b.event_type_id) || b.event_type_id}`);
      who.className = "cell-break";
      row.appendChild(who);
      panel.appendChild(row);
    }
    if ((upcoming.bookings || []).length > 5) {
      const more = el("button", "View all bookings");
      more.className = "btn sm secondary";
      more.addEventListener("click", () => show("bookings"));
      panel.appendChild(more);
    }
    box.appendChild(panel);
  }

  async function loadTypes() {
    const data = await call("/event-types");
    const box = document.getElementById("types");
    box.innerHTML = "";
    const { wrap, tbody } = tableView([["Title", ""], ["Slug", "col-slug"], ["Duration", ""], ["Visibility", ""], ["", "col-actions"]]);
    for (const t of data.event_types || []) {
      const row = el("tr");
      const titleCell = el("td", t.title);
      // Admin titles are free text: one long word must wrap, not clip.
      titleCell.className = "cell-break";
      titleCell.dataset.label = "Title";
      row.appendChild(titleCell);
      const slug = el("td", t.slug);
      slug.className = "col-slug";
      slug.dataset.label = "Slug";
      row.appendChild(slug);
      const duration = t.duration_mode === "fixed"
        ? fmtDuration(t.fixed_duration_min)
        : (t.allowed_durations || []).map(fmtDuration).join(" / ");
      const durationCell = el("td", duration);
      durationCell.dataset.label = "Duration";
      row.appendChild(durationCell);
      const vis = el("td");
      vis.dataset.label = "Visibility";
      vis.appendChild(badge(t.visibility === "disabled" ? "" : "ok",
        t.visibility === "disabled" ? "Disabled" : "Listed"));
      row.appendChild(vis);
      const actions = el("td");
      actions.className = "actions";
      const stack = el("div");
      stack.className = "actions-stack";
      const disabling = t.visibility !== "disabled";
      const toggle = el("button", disabling ? "Disable" : "Enable");
      toggle.className = "btn sm " + (disabling ? "danger" : "secondary");
      // Hiding a type takes bookings off the public site, so it gets the
      // same two-step arm as a cancel. Failure surfaces as a note beside
      // the actions, never inside the button label: buttons stay nowrap,
      // so a long message there would push the button past the card edge
      // on mobile.
      let armTimer = 0;
      const disarm = () => {
        clearTimeout(armTimer);
        delete toggle.dataset.armed;
        toggle.classList.remove("armed");
        toggle.textContent = disabling ? "Disable" : "Enable";
      };
      toggle.addEventListener("click", async () => {
        if (disabling && !toggle.dataset.armed) {
          toggle.dataset.armed = "1";
          toggle.classList.add("armed");
          toggle.textContent = "Really disable?";
          armTimer = setTimeout(disarm, 4000);
          return;
        }
        disarm();
        toggle.disabled = true;
        try {
          await call(`/event-types/${t.id}`, {
            method: "PUT",
            body: JSON.stringify({
              visibility: disabling ? "disabled" : "listed",
              expected_version: t.version,
            }),
          });
          loadTypes();
        } catch (err) {
          toggle.disabled = false;
          const note = el("span", `Could not save that change — ${why(err)}.`);
          note.className = "saved-note error visible row-error";
          note.setAttribute("role", "alert");
          actions.appendChild(note);
          setTimeout(() => note.remove(), 4000);
        }
      });
      toggle.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && toggle.dataset.armed) disarm();
      });
      stack.appendChild(toggle);
      const preview = el("a", "Preview");
      preview.href = `/${t.slug}`;
      preview.className = "btn sm secondary";
      stack.appendChild(preview);
      actions.appendChild(stack);
      row.appendChild(actions);
      tbody.appendChild(row);
    }
    if (!(data.event_types || []).length) emptyRow(tbody, 5, "No event types yet.");
    box.appendChild(wrap);
  }

  function flashError(container, message) {
    const note = el("span", message);
    note.className = "saved-note error visible";
    note.setAttribute("role", "alert");
    container.prepend(note);
    setTimeout(() => note.remove(), 4000);
  }

  async function loadBookings() {
    const [data, typeData] = await Promise.all([
      call("/bookings?status=confirmed"),
      Promise.all([hostSettings(), call("/event-types").catch(() => ({}))])
        .then(([, types]) => types),
    ]);
    const titles = new Map((typeData.event_types || []).map((t) => [t.id, t.title]));
    const box = document.getElementById("bookings");
    box.innerHTML = "";
    const { wrap, tbody } = tableView([["When", ""], ["Type", "col-type"], ["Invitee", ""], ["Status", ""], ["", "col-actions"]]);
    for (const b of data.bookings || []) {
      const row = el("tr");
      const when = el("td", fmtWhen(b.start_iso));
      when.dataset.label = "When";
      row.appendChild(when);
      const type = el("td", titles.get(b.event_type_id) || b.event_type_id);
      type.className = "col-type";
      type.dataset.label = "Type";
      row.appendChild(type);
      const invitee = el("td");
      invitee.dataset.label = "Invitee";
      const person = el("div");
      person.className = "invitee-main";
      person.appendChild(el("strong", b.invitee_name));
      const email = el("div", b.invitee_email);
      email.className = "cell-break";
      person.appendChild(email);
      invitee.appendChild(person);
      row.appendChild(invitee);
      const status = el("td");
      status.dataset.label = "Status";
      status.appendChild(badge(b.status === "confirmed" ? "ok" : "", cap(b.status)));
      row.appendChild(status);
      const actions = el("td");
      actions.className = "actions";
      const stack = el("div");
      stack.className = "actions-stack";
      const cancel = el("button", "Cancel");
      cancel.className = "btn sm danger";
      // Two-step inline confirm, matching the manage page: the first click
      // arms, the second commits, and the arm expires (or Escape disarms)
      // so a stray double-click cannot cancel a booking.
      const disarm = () => {
        cancel.dataset.armed = "0";
        cancel.classList.remove("armed");
        cancel.textContent = "Cancel";
        cancel.removeAttribute("aria-label");
      };
      cancel.addEventListener("click", async () => {
        if (cancel.dataset.armed !== "1") {
          cancel.dataset.armed = "1";
          cancel.classList.add("armed");
          cancel.textContent = "Confirm?";
          cancel.setAttribute("aria-label", `Confirm cancel of ${b.reference}`);
          setTimeout(() => { if (cancel.dataset.armed === "1") disarm(); }, 4000);
          return;
        }
        cancel.disabled = true;
        try {
          await call(`/bookings/${b.id}/cancel`, {
            method: "POST",
            body: JSON.stringify({ revision: b.revision, idempotency_key: crypto.randomUUID() }),
          });
          loadBookings();
        } catch (err) {
          disarm();
          cancel.disabled = false;
          flashError(box, `Could not cancel that booking — ${why(err)}.`);
        }
      });
      cancel.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && cancel.dataset.armed === "1") disarm();
      });
      stack.appendChild(cancel);
      actions.appendChild(stack);
      row.appendChild(actions);
      tbody.appendChild(row);
    }
    if (!(data.bookings || []).length) emptyRow(tbody, 5, "No confirmed bookings.");
    box.appendChild(wrap);
  }

  async function loadSettings() {
    const data = await hostSettings();
    const box = document.getElementById("settings");
    box.innerHTML = "";
    const host = data.host || {};
    const card = el("div");
    card.className = "panel settings-card";
    const form = el("form");
    form.className = "admin-form";
    const fields = [
      ["display_name", "Display name", "Shown as the host on public pages."],
      ["timezone", "Timezone", "Zone the schedule and admin times follow."],
      ["public_base_url", "Public base URL", "Canonical origin visitors will see."],
      ["contact_fallback", "Contact fallback", "Shown when booking is paused."],
      ["host_notification_email", "Host notification email", "Where new-booking notices go."],
    ];
    let zones = [];
    try { zones = JSON.parse(root.dataset.config || "{}").zones || []; } catch (err) { zones = []; }
    for (const [name, label, hint] of fields) {
      const wrap = el("div");
      wrap.className = "field";
      const lab = el("label", label);
      lab.htmlFor = `set-${name}`;
      const input = name === "timezone" ? el("select") : el("input");
      input.id = `set-${name}`;
      input.name = name;
      if (name === "timezone") {
        // A typo in a free-text IANA zone would silently break the schedule;
        // a fixed list cannot be mistyped.
        const current = host[name] || "";
        for (const zone of zones) input.appendChild(new Option(zone, zone));
        if (current && !zones.includes(current)) input.prepend(new Option(current, current));
        input.value = current;
      } else {
        input.value = host[name] || "";
      }
      wrap.appendChild(lab);
      wrap.appendChild(input);
      wrap.appendChild(el("span", hint)).className = "hint";
      form.appendChild(wrap);
    }
    const save = el("button", "Save settings");
    save.type = "submit";
    save.className = "btn";
    const note = el("span", "Saved");
    note.className = "saved-note";
    note.setAttribute("role", "status");
    form.appendChild(save);
    form.appendChild(note);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      save.disabled = true;
      note.classList.remove("error", "visible");
      note.setAttribute("role", "status");
      const payload = {};
      fields.forEach(([name]) => { payload[name] = form.elements[name].value; });
      try {
        await call("/settings", { method: "PUT", body: JSON.stringify(payload) });
        settingsCache = null;
        await hostSettings();
        note.textContent = "Saved";
        note.classList.add("visible");
        setTimeout(() => note.classList.remove("visible"), 2400);
      } catch (err) {
        note.textContent = `Could not save — ${why(err)}.`;
        note.setAttribute("role", "alert");
        note.classList.add("error", "visible");
      } finally {
        save.disabled = false;
      }
    });
    card.appendChild(form);
    box.appendChild(card);
  }

  const loaders = { overview: loadOverview, types: loadTypes, bookings: loadBookings, settings: loadSettings };
  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.addEventListener("click", () => show(b.dataset.tab));
  });
  show("overview");
})();
