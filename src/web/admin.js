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
  // error surface embeds this fragment after its own context prefix. A
  // trailing period is stripped: the embedding contexts supply their own,
  // and server messages often arrive with one attached.
  function why(err) {
    const message = err.message === "Failed to fetch"
      ? "the server could not be reached"
      : (err.message || "something went wrong");
    return message.replace(/\.+$/, "");
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
      // A failed load must never be cached as an empty result: an empty
      // object here once rendered a blank editable form whose Save would
      // wipe the real config, and quietly degrade every zone-formatted
      // time. Throw instead — each section's failure card offers Retry.
      // The same discipline for a 200 with no host payload: cached, it
      // would pin every later Retry to the empty answer long after the
      // server recovered, so it is judged before it reaches the cache.
      const data = await call("/settings");
      if (!data || !data.host) throw new Error("the server returned an empty settings payload");
      settingsCache = data;
      const tz = (settingsCache.host || {}).timezone || "";
      const note = document.getElementById("tz-note");
      if (tz && note) note.textContent = `Times shown in ${tz} — the zone from Settings`;
    }
    return settingsCache;
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
    const settled = loaders[section]();
    settled.catch((err) => {
      box.innerHTML = "";
      const panel = el("div");
      // A failed fetch is an error, not an empty shelf: the .error modifier
      // swaps the dashed "nothing here" frame for a solid, full-width card
      // with recovery as the one visible action.
      panel.className = "empty-state error";
      panel.setAttribute("role", "alert");
      panel.appendChild(el("p", `Could not load this section — ${why(err)}.`));
      const retry = el("button", "Retry");
      retry.className = "btn sm";
      retry.addEventListener("click", () => {
        const reloading = show(section);
        // The clicked Retry is about to be removed; hold keyboard position
        // on the skeleton that replaces it instead of dumping to <body>,
        // and on success land inside the reloaded section — the section box
        // is the reading start, the next Tab walks its content.
        const skeleton = box.querySelector(".tab-loading");
        if (skeleton) { skeleton.tabIndex = -1; skeleton.focus({ preventScroll: true }); }
        reloading.then(() => {
          const reloaded = document.getElementById(section);
          reloaded.tabIndex = -1;
          reloaded.focus({ preventScroll: true });
        }).catch(() => {});
      });
      panel.appendChild(retry);
      box.appendChild(panel);
      // Same handback for the failure itself: the alert is announced, and
      // the keyboard lands on its recovery action.
      retry.focus({ preventScroll: true });
    });
    return settled;
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
      // Every other action here speaks when it fails; a dead-looking toggle
      // would be the one silent surface. Locked while in flight so a double
      // click cannot fire two contradictory PUTs.
      btn.disabled = true;
      const note = el("span");
      note.className = "saved-note";
      try {
        await call("/settings", { method: "PUT", body: JSON.stringify({ pause_new_bookings: !data.paused }) });
        // Success rebuilds the whole section, so the pressed button is gone
        // and a keyboard admin would land on <body> right after the state
        // they asked for. The fresh toggle is the same control with the
        // flipped label — put them back on it (and on a failed refetch the
        // old button never left, so re-enable and hold it there).
        loadOverview().then(() => {
          const again = document.querySelector("#overview .panel-head .btn");
          if (again) again.focus();
        }, () => { btn.disabled = false; btn.focus(); });
      } catch (err) {
        btn.disabled = false;
        // Disabling the focused button dropped the keyboard to the body;
        // put it back where the verdict lands.
        btn.focus();
        note.textContent = `Could not save that change — ${why(err)}.`;
        note.className = "saved-note error visible";
        note.setAttribute("role", "alert");
        panelHead.appendChild(note);
        setTimeout(() => note.remove(), 4000);
      }
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

  // The one open questions editor across the table, as { button, editorRow }.
  let openEditor = null;

  // Every path that dismisses the editor funnels through here: the row
  // goes, the Edit button stops claiming expansion, and the singleton
  // forgets the record. Missing any one of the three strands the button's
  // aria state or makes the next click on it a swallowed no-op, because
  // the stale record still answers "this one was open".
  function closeEditor() {
    if (!openEditor) return;
    openEditor.button.setAttribute("aria-expanded", "false");
    openEditor.editorRow.remove();
    openEditor = null;
  }

  async function loadTypes() {
    openEditor = null;
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
      let armTimer = 0, toggleNote = null, toggleNoteTimer = 0;
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
        // Where this row sits in the table: the reload rebuilds every row,
        // and the keyboard must land on the rebuilt row's own toggle, not
        // the top of the document.
        const rowIndex = [...tbody.children].indexOf(row);
        try {
          await call(`/event-types/${t.id}`, {
            method: "PUT",
            body: JSON.stringify({
              visibility: disabling ? "disabled" : "listed",
              expected_version: t.version,
            }),
          });
          const againIn = () => document.querySelectorAll("#types tbody tr")[rowIndex]
            ?.querySelector(".actions-stack button");
          loadTypes().then(() => { const again = againIn(); if (again) again.focus(); },
            () => { toggle.disabled = false; toggle.focus(); });
        } catch (err) {
          toggle.disabled = false;
          // Disabling the focused button dropped the keyboard to the body;
          // the verdict's own button keeps a keyboard admin in the row.
          // One note per row: a repeat failure rewrites, not stacks.
          toggle.focus();
          if (!toggleNote) {
            toggleNote = el("span");
            toggleNote.className = "saved-note error visible row-error";
            toggleNote.setAttribute("role", "alert");
            actions.appendChild(toggleNote);
          }
          toggleNote.textContent = `Could not save that change — ${why(err)}.`;
          clearTimeout(toggleNoteTimer);
          toggleNoteTimer = setTimeout(() => { toggleNote.remove(); toggleNote = null; }, 4000);
        }
      });
      toggle.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && toggle.dataset.armed) disarm();
      });
      stack.appendChild(toggle);
      const edit = el("button", "Questions");
      edit.className = "btn sm secondary";
      edit.setAttribute("aria-expanded", "false");
      edit.addEventListener("click", () => {
        // One editor per type at a time, enforced: opening another type's
        // editor closes the open one, and clicking this button while its
        // own editor is up toggles it away. A close that would drop
        // unsaved edits stops here once, wearing the arm verdict.
        const wasOpen = openEditor && openEditor.button === edit;
        if (openEditor && !openEditor.requestClose()) return;
        closeEditor();
        if (wasOpen) return;
        edit.setAttribute("aria-expanded", "true");
        const opened = openQuestionsEditor(t, row);
        openEditor = { button: edit, editorRow: opened.row, requestClose: opened.requestClose };
      });
      stack.appendChild(edit);
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

  // --- booking questions editor ---------------------------------------------
  // The questions a visitor answers after picking a time. Everything here is
  // host-owned configuration: labels, answer types, required flags, choice
  // lists, and order, saved as one replacement with the expected version.
  const QUESTION_KINDS = [["text", "Short answer"], ["textarea", "Long text"],
                          ["single_choice", "Single choice"]];

  function normalizeQuestion(raw, index) {
    const limit = Math.min(Math.max(parseInt(raw.max_length, 10) || 2000, 1), 5000);
    return {
      id: raw.id || `q-${index}-${Math.random().toString(36).slice(2, 7)}`,
      label: raw.label || "",
      // A stored type wins; a legacy question with none resolves the way
      // the visitor page resolves it — length decides — so the editor
      // shows, and saves back, the widget the visitor actually gets
      // instead of quietly demoting a 500-character answer to one line.
      type: QUESTION_KINDS.some(([kind]) => kind === raw.type)
        ? raw.type
        : (raw.type == null && limit >= 200 ? "textarea" : "text"),
      required: !!raw.required,
      max_length: limit,
      choices: (raw.choices || []).map((c) => String(c)),
      allow_other: !!raw.allow_other,
    };
  }

  // rerender() rebuilds every card, so keyboard hosts would drop focus to the
  // page after a move or a type switch. Only one editor is ever open, so the
  // Nth .q-card in the document is the Nth question of this editor. A disabled
  // destination (the up button of the first card) swallows focus(), so fall
  // back to the question label instead of dropping focus to the page.
  function refocusCard(index, sel) {
    const card = document.querySelectorAll(".q-card")[index];
    if (!card) return;
    const target = card.querySelector(sel);
    if (target && !target.disabled) { target.focus(); return; }
    const fallback = card.querySelector("input");
    if (fallback) fallback.focus();
  }

  function questionCard(item, index, items, rerender) {
    const card = el("div");
    card.className = "q-card";
    // Per-card verdict slot: the save pre-flight writes here so a bad
    // config is named on the field it belongs to, not in a footer note.
    const err = el("span");
    err.className = "q-error";
    err.id = `q-err-${item.id}`;
    // Editing the flagged field withdraws the verdict, like the booking form.
    const liveClear = (input) => input.addEventListener("input", () => {
      if (!err.textContent) return;
      err.textContent = "";
      card.querySelectorAll('[aria-invalid="true"]').forEach((n) => n.removeAttribute("aria-invalid"));
    });
    const head = el("div");
    head.className = "q-card-head";
    // Sighted hosts get the same "Question N" anchor the aria-labels speak,
    // so a reorder is visible feedback, not a screen-reader-only event.
    const chip = el("span", `Q${index + 1}`);
    chip.className = "q-index";
    chip.setAttribute("aria-hidden", "true");
    head.appendChild(chip);
    const labelInput = el("input");
    labelInput.value = item.label;
    labelInput.placeholder = "Question visitors see";
    labelInput.setAttribute("aria-label", `Question ${index + 1} label`);
    labelInput.setAttribute("aria-describedby", err.id);
    labelInput.addEventListener("input", () => { item.label = labelInput.value; });
    liveClear(labelInput);
    head.appendChild(labelInput);
    const typeSelect = el("select");
    typeSelect.setAttribute("aria-label", `Question ${index + 1} answer type`);
    for (const [kind, name] of QUESTION_KINDS) typeSelect.appendChild(new Option(name, kind));
    typeSelect.value = item.type;
    typeSelect.addEventListener("change", () => {
      item.type = typeSelect.value;
      rerender();
      refocusCard(index, "select");
    });
    head.appendChild(typeSelect);
    const required = el("label");
    required.className = "q-check";
    const requiredBox = el("input");
    requiredBox.type = "checkbox";
    requiredBox.checked = item.required;
    requiredBox.addEventListener("change", () => { item.required = requiredBox.checked; });
    required.appendChild(requiredBox);
    required.appendChild(document.createTextNode("Required"));
    head.appendChild(required);
    card.appendChild(head);
    if (item.type === "single_choice") {
      const choicesField = el("div");
      choicesField.className = "q-choices";
      const choicesLabel = el("span", "Options — one per line");
      choicesLabel.className = "hint";
      choicesField.appendChild(choicesLabel);
      const choicesInput = el("textarea");
      choicesInput.rows = Math.max(3, item.choices.length + 1);
      choicesInput.value = item.choices.join("\n");
      choicesInput.setAttribute("aria-label", `Question ${index + 1} options`);
      choicesInput.setAttribute("aria-describedby", err.id);
      choicesInput.addEventListener("input", () => {
        item.choices = choicesInput.value.split("\n");
      });
      liveClear(choicesInput);
      choicesField.appendChild(choicesInput);
      const other = el("label");
      other.className = "q-check";
      const otherBox = el("input");
      otherBox.type = "checkbox";
      otherBox.checked = item.allow_other;
      otherBox.addEventListener("change", () => { item.allow_other = otherBox.checked; });
      other.appendChild(otherBox);
      other.appendChild(document.createTextNode("Include an \u201cOther\u201d free-text option"));
      choicesField.appendChild(other);
      card.appendChild(choicesField);
    }
    const tail = el("div");
    tail.className = "q-card-tail";
    const limitWrap = el("label");
    limitWrap.className = "q-check";
    const limitInput = el("input");
    limitInput.type = "number";
    limitInput.min = "1";
    limitInput.max = "5000";
    limitInput.value = String(item.max_length);
    limitInput.setAttribute("aria-label", `Question ${index + 1} maximum answer length`);
    limitInput.setAttribute("aria-describedby", err.id);
    limitInput.addEventListener("input", () => {
      // Zero and negatives must reach the save pre-flight, which flags
      // them on the field; only true garbage falls back to the default.
      // `|| 2000` here silently saved a typed 0 as a 2000 nobody saw.
      const n = parseInt(limitInput.value, 10);
      item.max_length = Number.isNaN(n) ? 2000 : n;
    });
    liveClear(limitInput);
    limitWrap.appendChild(limitInput);
    limitWrap.appendChild(document.createTextNode("Max characters"));
    tail.appendChild(limitWrap);
    const moves = el("div");
    moves.className = "q-card-moves";
    const up = el("button", "↑");
    up.type = "button";
    up.className = "btn sm secondary";
    up.disabled = index === 0;
    up.addEventListener("click", () => {
      [items[index - 1], items[index]] = [items[index], items[index - 1]];
      rerender();
      refocusCard(index - 1, ".q-card-moves button:nth-child(1)");
    });
    const down = el("button", "↓");
    down.type = "button";
    down.className = "btn sm secondary";
    down.disabled = index === items.length - 1;
    down.addEventListener("click", () => {
      [items[index + 1], items[index]] = [items[index], items[index + 1]];
      rerender();
      refocusCard(index + 1, ".q-card-moves button:nth-child(2)");
    });
    const remove = el("button", "Remove");
    remove.type = "button";
    remove.className = "btn sm danger";
    remove.addEventListener("click", () => { items.splice(index, 1); rerender(); });
    // "Move question 3 up" names a position, not a question — a screen-reader
    // host hopping these buttons hears numbers with nothing to anchor them.
    // The name carries the question's own label, and follows the typing live
    // since the label input can change without a rerender. An unnamed
    // question falls back to the number the card's Q-chip shows.
    const qName = () => item.label.trim() || `question ${index + 1}`;
    const syncMoves = () => {
      up.setAttribute("aria-label", `Move “${qName()}” up`);
      down.setAttribute("aria-label", `Move “${qName()}” down`);
      remove.setAttribute("aria-label", `Remove “${qName()}”`);
    };
    syncMoves();
    labelInput.addEventListener("input", syncMoves);
    moves.append(up, down, remove);
    tail.appendChild(moves);
    card.appendChild(tail);
    card.appendChild(err);
    return card;
  }

  function openQuestionsEditor(t, hostRow) {
    // The row's position among the type rows: the post-save reload rebuilds
    // the table, and the keyboard must land back on this row's Questions
    // toggle (now collapsed) instead of the top of the document.
    const rowIndex = [...hostRow.parentNode.children].indexOf(hostRow);
    const editorRow = el("tr");
    const cell = el("td");
    cell.colSpan = 5;
    editorRow.appendChild(cell);
    const panel = el("div");
    panel.className = "panel q-editor";
    panel.setAttribute("aria-label", `Booking questions for ${t.title}`);
    const items = (t.questions || []).map(normalizeQuestion);
    let rerender;
    const save = el("button", "Save questions");
    save.type = "button";
    save.className = "btn";
    const close = el("button", "Close");
    close.type = "button";
    close.className = "btn secondary";
    close.addEventListener("click", () => { if (requestClose()) closeEditor(); });
    const list = el("div");
    rerender = () => {
      list.replaceChildren(...items.map((item, i) => questionCard(item, i, items, rerender)));
      if (!items.length) {
        list.appendChild(el("p", "No questions yet — visitors are only asked for name and email."));
      }
    };
    rerender();
    const head = el("div");
    head.className = "panel-head";
    head.appendChild(el("h2", "Booking questions"));
    const add = el("button", "Add question");
    add.type = "button";
    add.className = "btn sm secondary";
    add.addEventListener("click", () => {
      items.push(normalizeQuestion({}, items.length));
      rerender();
    });
    head.appendChild(add);
    panel.appendChild(head);
    const intro = el("p", "Asked after a visitor picks a time. Answers reach you in the booking email and on the calendar event.");
    intro.className = "hint";
    panel.appendChild(intro);
    panel.appendChild(list);
    const foot = el("div");
    foot.className = "form-actions";
    const note = el("span");
    note.className = "saved-note";
    note.setAttribute("role", "status");
    // Unsaved edits must not vanish on a close. There is no native dialog
    // in this app: the first close attempt arms a verdict ("close again
    // to discard"), the second goes through — the same arm grammar as the
    // danger buttons, expiring after four seconds like they do.
    let dirty = false, discardArmed = false, disarmTimer = 0;
    panel.addEventListener("input", () => { dirty = true; }, true);
    panel.addEventListener("change", () => { dirty = true; }, true);
    const requestClose = () => {
      if (!dirty) return true;
      if (!discardArmed) {
        discardArmed = true;
        note.textContent = "Unsaved edits — close again to discard them.";
        note.classList.add("error", "visible");
        clearTimeout(disarmTimer);
        disarmTimer = setTimeout(() => {
          discardArmed = false;
          if (note.textContent.startsWith("Unsaved")) {
            note.classList.remove("error", "visible");
            note.textContent = "";
          }
        }, 4000);
        return false;
      }
      return true;
    };
    foot.append(save, close, note);
    panel.appendChild(foot);
    save.addEventListener("click", async () => {
      save.disabled = true;
      note.classList.remove("error", "visible");
      // The server bounces a bad config with one positional message in the
      // footer; the same rules run here so the failing card is named on the
      // field itself and keyboard focus lands on the first offender. A card
      // with no label and no options is untouched intent — still dropped
      // below without ceremony.
      let firstBad = null;
      list.querySelectorAll(".q-card").forEach((card, i) => {
        const item = items[i];
        if (!item.label.trim() && !item.choices.some((c) => c.trim())) return;
        const flag = (input, message) => {
          card.querySelector(".q-error").textContent = message;
          input.setAttribute("aria-invalid", "true");
          firstBad = firstBad || input;
        };
        if (!item.label.trim()) {
          return flag(card.querySelector(".q-card-head input"), "Give the question a label.");
        }
        if (item.type === "single_choice" && item.choices.filter((c) => c.trim()).length < 2) {
          const choicesInput = card.querySelector(".q-choices textarea");
          if (choicesInput) return flag(choicesInput, "Add at least two options — one per line.");
        }
        if (!(item.max_length >= 1 && item.max_length <= 5000)) {
          return flag(card.querySelector(".q-card-tail input"), "Max characters must be between 1 and 5000.");
        }
      });
      if (firstBad) {
        firstBad.focus();
        note.textContent = "Fix the highlighted question cards.";
        note.setAttribute("role", "alert");
        note.classList.add("error", "visible");
        save.disabled = false;
        return;
      }
      const clean = items
        .map((q) => ({ ...q, label: q.label.trim(), choices: q.choices.map((c) => c.trim()).filter(Boolean) }))
        .filter((q) => q.label || q.choices.length);
      try {
        await call(`/event-types/${t.id}`, {
          method: "PUT",
          body: JSON.stringify({ questions: clean, expected_version: t.version }),
        });
        note.textContent = "Saved";
        note.classList.add("visible");
        dirty = false;
        setTimeout(() => {
          // Only tear down while this editor is still the open one: an
          // admin who opened another type's editor during the pause must
          // not have it wiped by the trailing table reload.
          note.classList.remove("visible");
          if (openEditor && openEditor.editorRow === editorRow) {
            closeEditor();
            // The reload replaced every row; the saved editor's own
            // Questions button is where a keyboard admin continues.
            const againIn = () => document.querySelectorAll("#types tbody tr")[rowIndex]
              ?.querySelector('.actions-stack button[aria-expanded]');
            loadTypes().then(() => { const again = againIn(); if (again) again.focus(); },
              () => { const again = againIn(); if (again) again.focus(); });
          }
        }, 900);
      } catch (err) {
        save.disabled = false;
        // Disabling a focused button drops focus to the body; putting the
        // keyboard back on the verdict's own button keeps a keyboard admin
        // in the conversation instead of dumping them at the document.
        save.focus();
        note.textContent = `Could not save — ${why(err)}.`;
        note.setAttribute("role", "alert");
        note.classList.add("error", "visible");
      }
    });
    cell.appendChild(panel);
    hostRow.after(editorRow);
    return { row: editorRow, requestClose };
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
      // One verdict per row: a repeat failure rewrites the note instead of
      // stacking a crowd of them beside the actions.
      let rowNote = null, rowNoteTimer = 0;
      const showRowNote = (message) => {
        if (!rowNote) {
          rowNote = el("span");
          rowNote.className = "saved-note error visible row-error";
          rowNote.setAttribute("role", "alert");
          actions.appendChild(rowNote);
        }
        rowNote.textContent = message;
        clearTimeout(rowNoteTimer);
        rowNoteTimer = setTimeout(() => { rowNote.remove(); rowNote = null; }, 4000);
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
        // Same handback as the types table: the reload rebuilds every row,
        // so the keyboard continues on the rebuilt row's own action instead
        // of falling off the page.
        const rowIndex = [...tbody.children].indexOf(row);
        try {
          await call(`/bookings/${b.id}/cancel`, {
            method: "POST",
            body: JSON.stringify({ revision: b.revision, idempotency_key: crypto.randomUUID() }),
          });
          const againIn = () => document.querySelectorAll("#bookings tbody tr")[rowIndex]
            ?.querySelector(".actions-stack button");
          loadBookings().then(() => { const again = againIn(); if (again) again.focus(); },
            () => { cancel.disabled = false; cancel.focus(); });
        } catch (err) {
          disarm();
          cancel.disabled = false;
          // Disabling the focused button drops the keyboard to the body;
          // putting it back on the verdict's own button keeps a keyboard
          // admin in the row they acted on.
          cancel.focus();
          // The verdict belongs to the row the admin acted on — beside the
          // actions, matching the types table — not at the section top,
          // a screen away from a tall table's failing row.
          showRowNote(`Could not cancel that booking — ${why(err)}.`);
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
    // A 200 without a host payload is as dangerous as a failure: rendering
    // it would invite a Save of all-empty settings over the real config.
    if (!data || !data.host) {
      throw new Error("the server returned an empty settings payload");
    }
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
      // The failing field describes its own error, like the booking form:
      // the verdict sits under the input, and typing withdraws the flag.
      const fieldError = el("span");
      fieldError.className = "error";
      fieldError.setAttribute("role", "alert");
      wrap.appendChild(fieldError);
      input.addEventListener("input", () => {
        input.removeAttribute("aria-invalid");
        fieldError.textContent = "";
      });
      form.appendChild(wrap);
    }
    const save = el("button", "Save settings");
    save.type = "submit";
    save.className = "btn";
    const note = el("span", "Saved");
    note.className = "saved-note";
    note.setAttribute("role", "status");
    // Button and verdict live in a .form-actions flex row like every other
    // form here: loose inline children would let a long verdict wrap around
    // the button, splitting the sentence beside and below it.
    const foot = el("div");
    foot.className = "form-actions";
    foot.append(save, note);
    form.appendChild(foot);
    // The same rules the server enforces, run first so the failing field is
    // named on itself and focus lands on the first offender — the save never
    // flies just to bounce. Blank stays legal: an empty notice address means
    // "no notices", not a malformed one.
    function isEmailAddress(value) {
      return value.includes("@") && value.includes(".") && value.length <= 320;
    }
    const RULES = {
      public_base_url: (v) => {
        try { const u = new URL(v); return u.protocol === "http:" || u.protocol === "https:"; }
        catch (err) { return false; }
      },
      contact_fallback: isEmailAddress,
      host_notification_email: isEmailAddress,
    };
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      note.classList.remove("error", "visible");
      note.setAttribute("role", "status");
      fields.forEach(([name]) => {
        const input = form.elements[name];
        input.removeAttribute("aria-invalid");
        input.closest(".field").querySelector(".error").textContent = "";
      });
      let firstBad = null;
      const flag = (input, message) => {
        input.setAttribute("aria-invalid", "true");
        input.closest(".field").querySelector(".error").textContent = message;
        firstBad = firstBad || input;
      };
      fields.forEach(([name]) => {
        const rule = RULES[name];
        const value = form.elements[name].value.trim();
        // The host's name is the one required setting: blank is not "no
        // name", it is every public page losing its host. The booking
        // form's own words keep the grammar one voice across surfaces.
        if (name === "display_name") {
          if (!value) flag(form.elements[name], "This field is required.");
          return;
        }
        if (!rule) return;
        if (value && !rule(value)) {
          flag(form.elements[name], name === "public_base_url"
            ? "Enter a full address starting with https://."
            : "Enter a valid email address.");
        }
      });
      if (firstBad) {
        firstBad.focus();
        note.textContent = "Fix the highlighted fields.";
        note.setAttribute("role", "alert");
        note.classList.add("error", "visible");
        return;
      }
      save.disabled = true;
      const payload = {};
      fields.forEach(([name]) => { payload[name] = form.elements[name].value.trim(); });
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
