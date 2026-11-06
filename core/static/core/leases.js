/* Leases and rent payments: pop-ups (Record payment, Renew, Terminate, Cheque numbers) and the
   auto-fill on the lease form. The server side is leases/api.py. */
(function () {
  "use strict";

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text) { node.textContent = text; }
    return node;
  }

  function csrf() {
    var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input) { return input.value; }
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function post(url, data) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
      body: new URLSearchParams(data)
    }).then(function (response) {
      return response.json().catch(function () {
        return { ok: false, errors: ["Something went wrong. Please try again."] };
      });
    }).catch(function () {
      return { ok: false, errors: ["The server could not be reached. Please try again."] };
    });
  }

  function money(value) {
    var n = Math.round(value * 100) / 100;
    return "AED " + n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }

  function reload(redirect) {
    window.__erpAllowLeave = true;  // no "unsaved changes" box on the way out
    if (redirect) { window.location.href = redirect; } else { window.location.reload(); }
  }

  /* ---- dates: always shown and typed the way System settings says -------------------------------
     DATE_SHAPE is a Python-style pattern such as %d-%m-%Y (loaded from /admin/formats.json). Dates travel to the
     server as typed; the server reads them with the same setting. */
  var DATE_SHAPE = "%Y-%m-%d", DATE_LABEL = "YYYY-MM-DD";
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  fetch("/admin/formats.json", { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { if (d && d.date) { DATE_SHAPE = d.date; DATE_LABEL = d.label || DATE_LABEL; } })
    .catch(function () { /* keep the default */ });

  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function isoToText(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    if (!m) { return iso || ""; }
    return DATE_SHAPE.replace("%Y", m[1]).replace("%m", m[2]).replace("%d", m[3]).replace("%b", MONTHS[parseInt(m[2], 10) - 1]);
  }
  function parts(year, month, day) { return year + "-" + pad2(month) + "-" + pad2(day); }
  function textToIso(text) {
    text = (text || "").trim();
    var iso = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(text);
    if (iso) { return parts(+iso[1], +iso[2], +iso[3]); }
    var order = [], pattern = DATE_SHAPE.replace(/[.*+?^${}()|[\]\\\/-]/g, "\\$&").replace(/%([dmYb])/g, function (all, k) {
      order.push(k);
      return k === "Y" ? "(\\d{4})" : (k === "b" ? "([A-Za-z]{3})" : "(\\d{1,2})");
    });
    var m = new RegExp("^" + pattern + "$").exec(text);
    if (!m) { return ""; }
    var y = 0, mo = 0, d = 0;
    order.forEach(function (k, i) {
      if (k === "Y") { y = +m[i + 1]; }
      else if (k === "d") { d = +m[i + 1]; }
      else if (k === "m") { mo = +m[i + 1]; }
      else { mo = MONTHS.map(function (x) { return x.toLowerCase(); }).indexOf(m[i + 1].toLowerCase()) + 1; }
    });
    if (!y || !mo || !d || mo > 12 || d > 31) { return ""; }
    return parts(y, mo, d);
  }

  // A small calendar that fills the text box in the chosen style.
  function openCalendar(input) {
    var old = document.getElementById("erp-cal");
    if (old) { old.remove(); }
    var iso = textToIso(input.value) || new Date().toISOString().slice(0, 10);
    var shown = { y: +iso.slice(0, 4), m: +iso.slice(5, 7) };
    var panel = el("div", "erp-cal");
    panel.id = "erp-cal";
    var rect = input.getBoundingClientRect();
    panel.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 300)) + "px";
    panel.style.top = Math.min(rect.bottom + 4, Math.max(8, window.innerHeight - 330)) + "px";
    function pick(text) {
      input.value = text;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      close();
    }
    function close() {
      document.removeEventListener("mousedown", away, true);
      document.removeEventListener("keydown", onKey, true);
      panel.remove();
    }
    function away(e) { if (!panel.contains(e.target) && e.target !== input && !(e.target.closest && e.target.closest(".erp-dateicon"))) { close(); } }
    function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); close(); } }
    function draw() {
      panel.innerHTML = "";
      var head = el("div", "erp-cal-head");
      var prev = el("button", "erp-cal-nav", "‹"), next = el("button", "erp-cal-nav", "›");
      prev.type = next.type = "button";
      prev.addEventListener("click", function () { shown.m--; if (shown.m < 1) { shown.m = 12; shown.y--; } draw(); });
      next.addEventListener("click", function () { shown.m++; if (shown.m > 12) { shown.m = 1; shown.y++; } draw(); });
      head.appendChild(prev);
      head.appendChild(el("b", "", MONTHS[shown.m - 1] + " " + shown.y));
      head.appendChild(next);
      panel.appendChild(head);
      var grid = el("div", "erp-cal-grid");
      ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].forEach(function (d) { grid.appendChild(el("span", "erp-cal-dow", d)); });
      var first = (new Date(shown.y, shown.m - 1, 1).getDay() + 6) % 7;  // Monday first
      var days = new Date(shown.y, shown.m, 0).getDate();
      for (var i = 0; i < first; i++) { grid.appendChild(el("span", "")); }
      for (var d = 1; d <= days; d++) {
        var value = parts(shown.y, shown.m, d);
        var cell = el("button", "erp-cal-day" + (value === iso ? " is-on" : "") + (value === new Date().toISOString().slice(0, 10) ? " is-today" : ""), String(d));
        cell.type = "button";
        cell.addEventListener("click", (function (v) { return function () { pick(isoToText(v)); }; })(value));
        grid.appendChild(cell);
      }
      panel.appendChild(grid);
      var foot = el("div", "erp-cal-foot");
      var clear = el("button", "erp-cal-link", "Clear"), today = el("button", "erp-cal-link", "Today");
      clear.type = today.type = "button";
      clear.addEventListener("click", function () { pick(""); });
      today.addEventListener("click", function () {
        var n = new Date();
        pick(isoToText(parts(n.getFullYear(), n.getMonth() + 1, n.getDate())));
      });
      foot.appendChild(clear);
      foot.appendChild(today);
      panel.appendChild(foot);
    }
    draw();
    document.body.appendChild(panel);
    document.addEventListener("mousedown", away, true);
    document.addEventListener("keydown", onKey, true);
  }

  /* ---- a small dialog builder -------------------------------------------------------------
     cfg: { title, subtitle, fields: [...], note: function(values) -> text, buttons: [...] }
     field: { name, label, type: text|number|date|select|checkbox, value, options: [[v, l]],
              hint, min, max, step, placeholder, when: function(values) -> bool }
     button: { label, kind: primary|danger|neutral, run: function(values, ui) } */
  function openDialog(cfg) {
    var old = document.getElementById("erp-dlg-overlay");
    if (old) { old.remove(); }
    var overlay = el("div", "erp-dlg-overlay");
    overlay.id = "erp-dlg-overlay";
    var dlg = el("div", "erp-dlg" + (cfg.wide ? " erp-dlg-wide" : ""));
    dlg.setAttribute("role", "dialog");
    dlg.setAttribute("aria-modal", "true");
    dlg.setAttribute("aria-label", cfg.title);
    dlg.appendChild(el("h3", "erp-dlg-title", cfg.title));
    if (cfg.subtitle) { dlg.appendChild(el("p", "erp-dlg-sub", cfg.subtitle)); }

    var form = el("form", "erp-dlg-form");
    var inputs = {}, rows = {};
    (cfg.fields || []).forEach(function (f) {
      var row = el("div", "erp-dlg-row" + (f.full ? " erp-dlg-full" : ""));
      var input;
      if (f.type === "select") {
        input = el("select", "erp-dlg-input");  // shown as the app's own styled dropdown (erp.js picks it up)
        (f.options || []).forEach(function (o) {
          var opt = el("option", "", o[1]);
          opt.value = o[0];
          input.appendChild(opt);
        });
      } else {
        input = el("input", "erp-dlg-input");
        input.type = f.type === "date" ? "text" : (f.type || "text");
        if (f.min) { input.min = f.min; }
        if (f.max) { input.max = f.max; }
        if (f.step) { input.step = f.step; }
        if (f.placeholder) { input.placeholder = f.placeholder; }
        if (f.type === "checkbox") { input.className = "erp-dlg-check"; }
      }
      input.name = f.name;
      input.id = "erp-dlg-" + f.name;
      if (f.type === "checkbox") {
        input.checked = !!f.value;
        var wrap = el("label", "erp-dlg-checkrow");
        wrap.appendChild(input);
        wrap.appendChild(el("span", "", f.label));
        row.appendChild(wrap);
      } else {
        if (f.type === "date") {
          input.value = isoToText(f.value);
          input.placeholder = DATE_LABEL;
          input.autocomplete = "off";
        } else if (f.value !== undefined && f.value !== null) { input.value = f.value; }
        var label = el("label", "erp-dlg-label", f.label);
        label.setAttribute("for", input.id);
        row.appendChild(label);
        if (f.type === "date") {
          var box = el("div", "erp-dateinput");
          var icon = el("button", "erp-dateicon", "");
          icon.type = "button";
          icon.setAttribute("aria-label", "Open the calendar");
          icon.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>';
          icon.addEventListener("click", function () { openCalendar(input); });
          input.addEventListener("keydown", function (e) { if (e.key === "ArrowDown") { e.preventDefault(); openCalendar(input); } });
          box.appendChild(input);
          box.appendChild(icon);
          row.appendChild(box);
        } else {
          row.appendChild(input);
        }
      }
      if (f.hint) { row.appendChild(el("div", "erp-dlg-hint", f.hint)); }
      inputs[f.name] = input;
      rows[f.name] = { row: row, field: f, last: input.value };
      form.appendChild(row);
    });
    var note = cfg.note ? el("div", "erp-dlg-note") : null;
    if (note) { form.appendChild(note); }
    var error = el("div", "erp-dlg-error");
    error.hidden = true;
    form.appendChild(error);
    dlg.appendChild(form);

    function values() {
      var out = {};
      Object.keys(inputs).forEach(function (name) {
        var input = inputs[name];
        out[name] = input.type === "checkbox" ? (input.checked ? "on" : "") : input.value;
      });
      return out;
    }
    var buttonEls = [];
    function refresh() {
      var v = values();
      Object.keys(rows).forEach(function (name) {
        var f = rows[name].field;
        rows[name].row.hidden = f.when ? !f.when(v) : false;
      });
      buttonEls.forEach(function (x) { x.btn.hidden = x.b.when ? !x.b.when(v) : false; });
      if (note) { note.textContent = cfg.note(v) || ""; note.hidden = !note.textContent; }
    }
    form.addEventListener("input", refresh);
    form.addEventListener("change", function (e) {
      refresh();
      var name = e.target && e.target.name;
      var r = name && rows[name];
      if (r && r.field.onchange && inputs[name].value !== r.last) {
        r.last = inputs[name].value;
        r.field.onchange(inputs[name].value);
      }
    });

    var actions = el("div", "erp-dlg-actions");
    var cancel = el("button", "erp-fbtn", "Cancel");
    cancel.type = "button";
    actions.appendChild(cancel);
    var all = [cancel];
    var ui = {
      error: function (list) {
        error.hidden = !(list && list.length);
        error.innerHTML = "";
        (list || []).forEach(function (text) { error.appendChild(el("div", "", text)); });
      },
      busy: function (on) { all.forEach(function (b) { b.disabled = on; }); }
    };
    (cfg.buttons || []).forEach(function (b) {
      var btn = el("button", "erp-fbtn" + (b.kind === "primary" ? " erp-fbtn-primary" : b.kind === "danger" ? " erp-fbtn-danger" : ""), b.label);
      btn.type = "button";
      btn.addEventListener("click", function () {
        ui.error([]);
        b.run(values(), ui);
      });
      all.push(btn);
      buttonEls.push({ btn: btn, b: b });
      actions.appendChild(btn);
    });
    dlg.appendChild(actions);
    overlay.appendChild(dlg);
    document.body.appendChild(overlay);

    function close() {
      document.removeEventListener("keydown", onKey, true);
      overlay.remove();
    }
    function onKey(e) {
      if (e.key === "Escape") {
        if (document.querySelector(".erp-select-panel.is-open") || document.getElementById("erp-cal")) { return; }  // Esc closes the open dropdown or calendar first
        e.stopPropagation(); close();
      }
    }
    document.addEventListener("keydown", onKey, true);
    cancel.addEventListener("click", close);
    overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) { close(); } });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var primary = (cfg.buttons || [])[0];
      if (primary) { all[1].click(); }
    });
    refresh();
    var first = form.querySelector("input, select");
    if (first) { first.focus(); }
    return { close: close };
  }

  // Sends the form values; on success the page reloads (the server's message then pops up).
  function send(url, data, ui) {
    ui.busy(true);
    post(url, data).then(function (reply) {
      if (reply && reply.ok) {
        reload(reply.redirect);
      } else {
        ui.busy(false);
        ui.error((reply && reply.errors) || ["Something went wrong."]);
      }
    });
  }

  /* ---- the list of banks (System settings -> Banks), loaded once ---------------------------- */
  var BANKS = [];
  fetch("/admin/banks.json", { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
    .then(function (r) { return r.ok ? r.json() : { banks: [] }; })
    .then(function (d) { BANKS = (d && d.banks) || []; })
    .catch(function () { BANKS = []; });

  // The options of a bank drop-down. `current` (an older typed name) stays available; `blank` is the first line.
  function bankOptions(current, blank) {
    var out = [["", blank || "Select a bank"]];
    var seen = {};
    BANKS.forEach(function (name) { out.push([name, name]); seen[name.toLowerCase()] = true; });
    if (current && !seen[String(current).toLowerCase()]) { out.push([current, current]); }
    return out;
  }

  /* ---- the pop-ups -------------------------------------------------------------------------- */
  var METHODS = [["cheque", "Cheque"], ["transfer", "Bank transfer"], ["cash", "Cash"], ["card", "Card"], ["other", "Other"]];

  function recordDialog(url, info) {
    var held = !!info.held;
    var what = info.label || ("Payment " + info.part);
    var buttons = [
      { label: held ? "Cheque Cashed" : "Record Payment", kind: "primary", run: function (v, ui) {
        send(url, { action: "paid", date: v.date, method: held ? "cheque" : v.method, cheque_number: v.cheque_number, bank_name: v.bank_name }, ui);
      } }
    ];
    if (!held) {
      buttons.push({ label: "Cheque Held (Not Cashed)", kind: "light", when: function (v) { return v.method === "cheque"; }, run: function (v, ui) {
        if (v.method !== "cheque") { ui.error(["Choose Cheque in “Paid by” to hold a cheque."]); return; }
        send(url, { action: "held", date: v.date, cheque_number: v.cheque_number, bank_name: v.bank_name }, ui);
      } });
    }
    buttons.push({ label: "Cheque Bounced", kind: "danger", when: function (v) { return held || v.method === "cheque"; }, run: function (v, ui) { send(url, { action: "bounced" }, ui); } });
    if (info.overdue) {
      buttons.push({ label: "Waive (Write Off)", kind: "light", run: function (v, ui) { send(url, { action: "waived" }, ui); } });
    }
    var pickField = [];
    if (info.pick && info.pick.length > 1) {
      pickField = [{ name: "which", label: "Which payment?", type: "select", value: url, full: true,
        options: info.pick.map(function (p) {
          return [p.url, p.label + " · " + p.amount + " · due " + p.due + (p.held ? " · cheque held" : "") + (p.overdue ? " · overdue" : "")];
        }),
        onchange: function (chosenUrl) {
          var next = info.pick.filter(function (p) { return p.url === chosenUrl; })[0];
          if (next) { recordDialog(next.url, Object.assign({}, next, { pick: info.pick, today: info.today })); }
        } }];
    }
    var d = openDialog({
      title: held ? "Cash the Cheque" : "Record Payment",
      subtitle: info.code + " · " + what + " · " + info.amount + " due " + info.due +
        (info.overdue ? " (overdue)" : "") + (held ? " · the cheque is being held" : ""),
      fields: pickField.concat([
        { name: "date", label: held ? "Date Cashed" : "Date Received", type: "date", value: info.today },
        { name: "method", label: "Paid by", type: "select", options: METHODS, value: info.method || "cheque",
          when: function () { return !held; } },
        { name: "cheque_number", label: "Cheque number", type: "text", value: info.cheque, placeholder: "e.g. 000123",
          when: function (v) { return held || v.method === "cheque"; } },
        { name: "bank_name", label: "Bank", type: "select", value: info.bank, options: bankOptions(info.bank),
          when: function (v) { return held || v.method === "cheque"; } }
      ]),
      buttons: buttons
    });
    return d;
  }

  function renewDialog(url, info) {
    var rent = parseFloat(info.rent);
    openDialog({
      title: "Renew " + info.code,
      subtitle: info.tenant + " · the new lease runs " + info.new_start_text + " to " + info.new_end_text,
      fields: [
        { name: "mode", label: "Rent for the new lease", type: "select",
          options: [["same", "Same rent (" + money(rent) + ")"], ["percent", "Change by a percentage"], ["amount", "A new amount"]], value: "same" },
        { name: "value", label: "Value", type: "number", step: "0.01",
          when: function (v) { return v.mode !== "same"; }, placeholder: "e.g. 5 (for +5%)" }
      ],
      note: function (v) {
        var n = parseFloat(v.value);
        var next = rent;
        if (v.mode === "percent" && !isNaN(n)) { next = rent * (1 + n / 100); }
        if (v.mode === "amount" && !isNaN(n)) { next = n; }
        return "New annual rent: " + money(next) + (next !== rent ? " (" + (next > rent ? "+" : "") + money(next - rent) + ")" : "");
      },
      buttons: [{ label: "Renew lease", kind: "primary", run: function (v, ui) {
        send(url, { mode: v.mode, value: v.value }, ui);
      } }]
    });
  }

  function terminateDialog(url, info) {
    var today = info.today;
    var day = today < info.start ? info.start : (today > info.end ? info.end : today);
    openDialog({
      title: "Terminate " + info.code + " early",
      subtitle: info.tenant,
      fields: [
        { name: "date", label: "Last day of the tenancy", type: "date", value: day, min: info.start, max: info.end },
        { name: "reason", label: "Reason", type: "text", placeholder: "e.g. Tenant moved to a larger office" },
        { name: "cancel_unpaid", label: "Cancel the unpaid payments due after that day", type: "checkbox", value: true }
      ],
      note: function () {
        return info.pending_later ? info.pending_later + " pending payment(s) are still to come." : "";
      },
      buttons: [{ label: "Terminate lease", kind: "danger", run: function (v, ui) {
        if (!v.reason.trim()) { ui.error(["Say why the lease is ending early."]); return; }
        send(url, { date: v.date, reason: v.reason, cancel_unpaid: v.cancel_unpaid }, ui);
      } }]
    });
  }

  function chequesDialog(url, info) {
    openDialog({
      title: "Cheque numbers for " + info.code,
      subtitle: "Enter the first cheque number. The others follow in order, one for each payment that is not paid yet (" + info.unpaid + ").",
      fields: [
        { name: "first", label: "First cheque number", type: "text", placeholder: "e.g. 000123 or CQ-0042" },
        { name: "bank", label: "Bank (optional)", type: "select", options: bankOptions("", "No bank chosen") }
      ],
      buttons: [{ label: "Fill in the numbers", kind: "primary", run: function (v, ui) {
        if (!v.first.trim()) { ui.error(["Enter the first cheque number."]); return; }
        send(url, { first: v.first, bank: v.bank }, ui);
      } }]
    });
  }

  var STATUSES = [["pending", "Pending"], ["paid", "Paid"], ["held", "Held (Cheque Not Cashed)"], ["bounced", "Bounced"], ["waived", "Waived (Written Off)"]];
  var KINDS = [["fine", "Fine"], ["other", "Other"], ["refund", "Deposit Refund (Paid Back to the Tenant)"],
    ["rent", "Rent"], ["deposit", "Security Deposit"], ["office", "Office Fee"], ["agency", "Agency Fee"], ["ejari", "Ejari Fee"]];

  function paymentFields(v, withStatus) {
    var isCheque = function (x) { return x.method === "cheque"; };
    var fields = [
      { name: "due_date", label: "Due on", type: "date", value: v.due_date },
      { name: "amount", label: "Amount (AED, before VAT)", type: "number", step: "0.01", min: "0.01", value: v.amount },
      { name: "method", label: "Paid by", type: "select", options: METHODS.concat([["deposit", "Kept From the Deposit"]]), value: v.method || "cheque" },
      { name: "cheque_number", label: "Cheque number", type: "text", value: v.cheque_number, when: isCheque },
      { name: "bank_name", label: "Bank", type: "select", value: v.bank_name, options: bankOptions(v.bank_name), when: isCheque },
      { name: "cheque_date", label: "Date written on the cheque", type: "date", value: v.cheque_date, when: isCheque },
      { name: "deposited_on", label: "Deposited in the bank on", type: "date", value: v.deposited_on, when: isCheque }
    ];
    if (withStatus) {
      fields.push({ name: "status", label: "Status", type: "select", options: STATUSES, value: v.status });
      fields.push({ name: "paid_on", label: "Date received", type: "date", value: v.paid_on,
        when: function (x) { return x.status === "paid" || x.status === "held"; } });
    }
    fields.push({ name: "notes", label: "Note", type: "text", value: v.notes, placeholder: "optional", full: true });
    return fields;
  }

  function editPaymentDialog(url) {
    fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json().catch(function () { return { ok: false, errors: ["Something went wrong."] }; }); })
      .then(function (data) {
        if (!data || !data.ok) {
          openDialog({ title: "Cannot edit this payment", subtitle: ((data && data.errors) || ["Something went wrong."]).join(" "), buttons: [] });
          return;
        }
        var armed = false;
        var buttons = [{ label: "Save changes", kind: "primary", run: function (v, ui) { send(url, v, ui); } }];
        if (data.can_remove) {
          buttons.push({ label: "Remove payment", kind: "danger", run: function (v, ui) {
            if (!armed) { armed = true; ui.error(["Press “Remove payment” again to confirm. This cannot be undone."]); return; }
            send(url.replace(/edit\/$/, "remove/"), {}, ui);
          } });
        }
        openDialog({
          title: (data.label || ("Payment " + data.part)) + " of " + data.code,
          subtitle: "Change any detail of this payment.",
          fields: paymentFields(data.values, true),
          wide: true,
          buttons: buttons
        });
      });
  }

  function addPaymentDialog(url, info) {
    openDialog({
      title: "Add a Payment to " + info.code,
      subtitle: "Choose what the payment is for. Rent, deposit and fees each get their own line.",
      fields: [{ name: "kind", label: "Type", type: "select", options: KINDS, value: "fine" }].concat(
        paymentFields({ due_date: info.today, amount: "", method: "cheque", cheque_number: "", bank_name: "",
          cheque_date: "", deposited_on: "", status: "pending", paid_on: "", notes: "" }, false)),
      wide: true,
      buttons: [{ label: "Add payment", kind: "primary", run: function (v, ui) { send(url, v, ui); } }]
    });
  }

  /* An "Add Payment" button on the lease's Payments tab itself (the same button as in the lease header). */
  function addPaymentOnTab() {
    var group = document.getElementById("payments-group");
    var source = document.querySelector('[data-erp-act="addpay"]');
    if (!group || !source || group.querySelector(".erp-addpay-bar")) { return; }
    var bar = el("div", "erp-addpay-bar");
    var button = source.cloneNode(true);
    button.textContent = "Add Payment";
    bar.appendChild(button);
    group.insertBefore(bar, group.firstChild);
  }
  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", addPaymentOnTab); }
  else { addPaymentOnTab(); }

  /* ---- The lease's Payments tab: summary cards, progress, filters and totals ---- */
  function paymentsDashboard() {
    var group = document.getElementById("payments-group");
    if (!group || group.getAttribute("data-erp-dash")) { return; }
    var metas = group.querySelectorAll(".erp-rowmeta");
    if (!metas.length) { return; }
    group.setAttribute("data-erp-dash", "1");
    var table = group.querySelector("table");
    var rows = [];
    var source = document.querySelector('[data-erp-act="addpay"]');
    var code = "";
    try { code = JSON.parse(source.getAttribute("data-info") || "{}").code || ""; } catch (err) { code = ""; }

    for (var i = 0; i < metas.length; i++) {
      var m = metas[i], tr = m.closest("tr");
      if (!tr) { continue; }
      var d = {
        tr: tr, kind: m.getAttribute("data-kind"), status: m.getAttribute("data-status"),
        method: m.getAttribute("data-method"), total: parseFloat(m.getAttribute("data-total")) || 0,
        due: m.getAttribute("data-due"), overdue: m.getAttribute("data-overdue") === "1"
      };
      d.refund = d.kind === "refund";
      d.fromDeposit = d.method === "deposit";
      d.rent = d.kind === "rent";
      d.charge = ["deposit", "office", "agency", "ejari"].indexOf(d.kind) >= 0;
      tr.setAttribute("data-erp-status", d.overdue ? "overdue" : d.status);
      tr.classList.add("erp-prow");
      rows.push(d);
      /* the small "LSE-0001 payment 1" caption above each row repeats the lease; hide it */
      var leaves = tr.querySelectorAll("p, span, div, small, a");
      for (var j = 0; j < leaves.length; j++) {
        var node = leaves[j];
        if (node.children.length === 0 && code && node.textContent.trim().indexOf(code + " ") === 0 && !node.closest("button, .erp-rowacts")) {
          node.classList.add("erp-hide-caption");
        }
      }
    }

    function money(v) {
      return "AED " + Math.round(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    }
    function niceDate(iso) {
      var p = iso.split("-");
      return p[2] + "-" + p[1] + "-" + p[0];
    }
    var counted = rows.filter(function (r) { return !r.refund && !r.fromDeposit; });
    function sum(list) { return list.reduce(function (a, r) { return a + r.total; }, 0); }
    var total = sum(counted);
    var received = sum(counted.filter(function (r) { return r.status === "paid"; }));
    var held = counted.filter(function (r) { return r.status === "held"; });
    var open = counted.filter(function (r) { return r.status === "pending" || r.status === "bounced"; });
    var late = counted.filter(function (r) { return r.overdue && r.status !== "paid" && r.status !== "held"; });
    var upcoming = open.filter(function (r) { return !r.overdue; }).sort(function (a, b) { return a.due < b.due ? -1 : 1; })[0];
    var pct = total ? Math.min(100, Math.round(received * 100 / total)) : 0;

    function card(label, value, note, tone) {
      var c = el("div", "erp-pcard" + (tone ? " erp-pcard-" + tone : ""));
      c.appendChild(el("span", "erp-pcard-l", label));
      c.appendChild(el("b", "erp-pcard-v", value));
      c.appendChild(el("small", "erp-pcard-n", note || ""));
      return c;
    }
    var wrap = el("div", "erp-pdash");
    var cards = el("div", "erp-pcards");
    cards.appendChild(card("Total scheduled", money(total), counted.length + " payments"));
    cards.appendChild(card("Received", money(received), pct + "% collected", "ok"));
    cards.appendChild(card("Outstanding", money(sum(open)), open.length + " to collect", open.length ? "" : "ok"));
    cards.appendChild(card("Overdue", money(sum(late)), late.length ? late.length + " late" : "None", late.length ? "bad" : "ok"));
    if (held.length) { cards.appendChild(card("Cheques held", money(sum(held)), held.length + " not cashed", "warn")); }
    cards.appendChild(card("Next due", upcoming ? niceDate(upcoming.due) : "-", upcoming ? money(upcoming.total) : "Nothing coming up"));
    wrap.appendChild(cards);
    var bar = el("div", "erp-pbar");
    var fill = el("div", "erp-pbar-fill" + (late.length ? " erp-pbar-bad" : (pct >= 100 ? " erp-pbar-done" : "")));
    fill.style.width = pct + "%";
    bar.appendChild(fill);
    wrap.appendChild(bar);

    /* filter chips and a search box */
    var tools = el("div", "erp-ptools");
    var chips = el("div", "erp-pchips");
    var filters = [
      ["all", "All", function () { return true; }],
      ["rent", "Rent", function (r) { return r.rent; }],
      ["fees", "Deposit & fees", function (r) { return r.charge; }],
      ["other", "Fines & other", function (r) { return !r.rent && !r.charge; }],
      ["pending", "Pending", function (r) { return r.status === "pending" && !r.overdue; }],
      ["overdue", "Overdue", function (r) { return r.overdue && r.status !== "paid" && r.status !== "held"; }],
      ["paid", "Paid", function (r) { return r.status === "paid"; }],
      ["held", "Held / bounced", function (r) { return r.status === "held" || r.status === "bounced"; }]
    ];
    var active = filters[0], query = "";
    var footer = el("div", "erp-pfoot");
    function apply() {
      var shown = 0, amount = 0;
      rows.forEach(function (r) {
        var ok = active[2](r) && (!query || r.tr.textContent.toLowerCase().indexOf(query) >= 0);
        r.tr.style.display = ok ? "" : "none";
        if (ok) { shown++; if (!r.refund && !r.fromDeposit) { amount += r.total; } }
      });
      footer.textContent = "Showing " + shown + " of " + rows.length + " lines  ·  " + money(amount) + " in the lines shown";
    }
    filters.forEach(function (f) {
      var n = rows.filter(f[2]).length;
      if (!n && f[0] !== "all") { return; }
      var b = el("button", "erp-pchip" + (f === active ? " is-on" : ""), f[1] + " (" + n + ")");
      b.type = "button";
      b.addEventListener("click", function () {
        active = f;
        Array.prototype.forEach.call(chips.children, function (c) { c.classList.remove("is-on"); });
        b.classList.add("is-on");
        apply();
      });
      chips.appendChild(b);
    });
    tools.appendChild(chips);
    var search = el("input", "erp-psearch");
    search.type = "search";
    search.placeholder = "Search cheque no., bank, amount ...";
    search.addEventListener("input", function () { query = search.value.trim().toLowerCase(); apply(); });
    tools.appendChild(search);
    wrap.appendChild(tools);

    var anchor = group.querySelector(".erp-addpay-bar");
    group.insertBefore(wrap, anchor ? anchor.nextSibling : group.firstChild);
    if (table && table.parentNode) { table.parentNode.insertBefore(footer, table.nextSibling); }
    apply();
  }
  function startPayments() { addPaymentOnTab(); paymentsDashboard(); }
  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", startPayments); }
  else { startPayments(); }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("[data-erp-act]") : null;
    if (!btn) { return; }
    e.preventDefault();
    e.stopPropagation();
    var info = {};
    try { info = JSON.parse(btn.getAttribute("data-info") || "{}"); } catch (err) { info = {}; }
    var url = btn.getAttribute("data-url");
    var act = btn.getAttribute("data-erp-act");
    if (act === "record") { recordDialog(url, info); }
    else if (act === "renew") { renewDialog(url, info); }
    else if (act === "terminate") { terminateDialog(url, info); }
    else if (act === "cheques") { chequesDialog(url, info); }
    else if (act === "editpay") { editPaymentDialog(url); }
    else if (act === "addpay") { addPaymentDialog(url, info); }
  }, true);

  /* ---- bulk: mark the ticked payments as paid / their cheques as deposited ------------------ */
  function ticked() {
    return Array.prototype.map.call(document.querySelectorAll('input[name="_selected_action"]:checked'), function (c) { return c.value; });
  }

  function today() {
    var d = new Date();
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  function plural(n) { return n + " payment" + (n === 1 ? "" : "s"); }

  function bulkEditDialog(ids, url) {
    openDialog({
      title: "Edit " + plural(ids.length),
      subtitle: "Fill in only what you want to change. Anything left empty stays as it is. The date and amount of received payments are never changed here.",
      wide: true,
      fields: [
        { name: "due_date", label: "Due On", type: "date", value: "" },
        { name: "amount", label: "Amount (AED, Before VAT)", type: "number", step: "0.01", min: "0.01", value: "", placeholder: "keep as it is" },
        { name: "method", label: "Paid By", type: "select", value: "", options: [["", "Keep each as it is"]].concat(METHODS) },
        { name: "bank_name", label: "Bank", type: "select", value: "", options: bankOptions("", "Keep as it is") },
        { name: "cheque_first", label: "Cheque Numbers, Starting At", type: "text", value: "", placeholder: "e.g. 000123 (the others follow in order)" },
        { name: "cheque_date", label: "Date Written on the Cheque", type: "date", value: "" },
        { name: "deposited_on", label: "Deposited in the Bank On", type: "date", value: "" },
        { name: "notes", label: "Note", type: "text", value: "", placeholder: "keep as it is", full: true }
      ],
      buttons: [{ label: "Save Changes", kind: "primary", run: function (v, ui) {
        send(url, { ids: ids.join(","), action: "edit", due_date: v.due_date, amount: v.amount, method: v.method,
          bank_name: v.bank_name, cheque_first: v.cheque_first, cheque_date: v.cheque_date, deposited_on: v.deposited_on, notes: v.notes }, ui);
      } }]
    });
  }

  function bulkDeleteDialog(ids, url) {
    openDialog({
      title: "Remove " + plural(ids.length) + "?",
      subtitle: "Payments that were received, and held cheques, are not removed. This cannot be undone.",
      fields: [],
      buttons: [{ label: "Remove Payments", kind: "danger", run: function (v, ui) {
        send(url, { ids: ids.join(","), action: "delete" }, ui);
      } }]
    });
  }

  function bulkDialog(kind, url) {
    var ids = ticked();
    if (!ids.length) {
      openDialog({ title: "Nothing is ticked", subtitle: "Tick the payments in the list first, then press the button again.", buttons: [] });
      return;
    }
    if (kind === "delete") { bulkDeleteDialog(ids, url); return; }
    if (kind === "edit") { bulkEditDialog(ids, url); return; }
    var paid = kind === "paid";
    var fields = [{ name: "date", label: paid ? "Date received" : "Date deposited in the bank", type: "date", value: today() }];
    if (paid) {
      fields.push({ name: "method", label: "Paid by", type: "select", value: "",
        options: [["", "Keep each payment as scheduled"]].concat(METHODS) });
    }
    openDialog({
      title: (paid ? "Mark " : "Mark cheques of ") + ids.length + " payment" + (ids.length === 1 ? "" : "s") + (paid ? " as paid" : " as deposited"),
      subtitle: paid
        ? "Payments that were already received are skipped."
        : "Only cheques that are not deposited yet are changed. Other payments are skipped.",
      fields: fields,
      buttons: [{ label: paid ? "Mark as paid" : "Mark as deposited", kind: "primary", run: function (v, ui) {
        send(url, { ids: ids.join(","), action: kind, date: v.date, method: v.method || "" }, ui);
      } }]
    });
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-erp-bulk]") : null;
    if (!b) { return; }
    e.preventDefault();
    var bar = b.closest(".erp-bulk");
    bulkDialog(b.getAttribute("data-erp-bulk"), bar ? bar.getAttribute("data-url") : "");
  });

  /* The "Action" drop-down above the list: Edit / Delete selected open the same pop-ups as the buttons. */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var choice = form && form.querySelector ? form.querySelector('select[name="action"]') : null;
    var bar = document.querySelector(".erp-bulk");
    if (!choice || !bar) { return; }
    var kind = choice.value === "bulk_edit_selected" ? "edit" : choice.value === "bulk_delete_selected" ? "delete" : "";
    if (!kind) { return; }
    e.preventDefault();
    e.stopPropagation();
    bulkDialog(kind, bar.getAttribute("data-url"));
  }, true);

  document.addEventListener("change", function (e) {
    if (!e.target || !e.target.matches || !e.target.matches('input[name="_selected_action"], #action-toggle')) { return; }
    var label = document.querySelector("[data-erp-bulk-count]");
    if (label) {
      var n = ticked().length;
      label.textContent = n ? n + " ticked" : "";
    }
  });

  /* ---- filter drop-downs -------------------------------------------------------------------- */
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.matches && t.matches("select[data-erp-nav]")) {
      window.__erpAllowLeave = true;
      window.location.href = t.value;
    }
  });


  /* ---- search-as-you-type pickers (tenant, unit, property) ---------------------------------- */
  function pickers() {
    var selects = document.querySelectorAll("select[data-erp-search]");
    if (!selects.length) { return; }
    var base = window.location.pathname.replace(/\/(add|\d+\/change)\/?$/, "/") + "search/";

    Array.prototype.forEach.call(selects, function (select) {
      var kind = select.getAttribute("data-erp-search");
      var wrap = el("div", "erp-pick");
      var input = el("input", "erp-pick-input");
      input.type = "text";
      input.autocomplete = "off";
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-expanded", "false");
      input.placeholder = select.getAttribute("data-placeholder") || "Type to search";
      var clear = el("button", "erp-pick-clear", "×");
      clear.type = "button";
      clear.title = "Clear";
      clear.setAttribute("aria-label", "Clear");
      var list = el("ul", "erp-pick-list");
      list.hidden = true;
      list.setAttribute("role", "listbox");
      wrap.appendChild(input);
      wrap.appendChild(clear);
      wrap.appendChild(list);
      select.parentNode.insertBefore(wrap, select);
      select.hidden = true;
      select.style.display = "none";
      select.removeAttribute("required");  // a hidden empty required field would block the Save button silently
      wrap.addEventListener("mouseenter", sync);  // picks up a tenant added through the "+" pop-up

      var timer = null, token = 0, active = -1, items = [];

      function chosenLabel() {
        var opt = select.options[select.selectedIndex];
        return select.value && opt ? opt.text : "";
      }
      function sync() {
        input.value = chosenLabel();
        clear.hidden = !select.value;
        wrap.classList.toggle("has-value", !!select.value);
      }
      function hide() {
        list.hidden = true;
        input.setAttribute("aria-expanded", "false");
        active = -1;
      }
      function choose(item) {
        var opt = null;
        Array.prototype.forEach.call(select.options, function (o) { if (o.value === String(item.id)) { opt = o; } });
        if (!opt) { opt = new Option(item.label, String(item.id)); select.add(opt); }
        opt.text = item.label;
        select.value = String(item.id);
        select.dispatchEvent(new Event("change", { bubbles: true }));
        sync();
        hide();
      }
      function paint() {
        list.innerHTML = "";
        if (!items.length) {
          var none = el("li", "erp-pick-empty", "No match");
          list.appendChild(none);
        }
        items.forEach(function (item, i) {
          var li = el("li", "erp-pick-item" + (i === active ? " is-active" : ""));
          li.setAttribute("role", "option");
          li.appendChild(el("span", "erp-pick-main", item.label));
          if (item.sub) { li.appendChild(el("small", "", item.sub)); }
          li.addEventListener("mousedown", function (e) { e.preventDefault(); choose(item); });
          list.appendChild(li);
        });
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
      }
      function look(text) {
        var mine = ++token;
        fetch(base + "?" + new URLSearchParams({ kind: kind, q: text }).toString(), {
          credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" }
        }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
          if (mine !== token || !data) { return; }
          items = data.results || [];
          active = items.length ? 0 : -1;
          paint();
        }).catch(function () { /* the list simply stays closed */ });
      }

      input.addEventListener("focus", function () { input.select(); look(select.value ? "" : input.value.trim()); });
      input.addEventListener("input", function () {
        window.clearTimeout(timer);
        timer = window.setTimeout(function () { look(input.value.trim()); }, 250);
      });
      input.addEventListener("keydown", function (e) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          if (list.hidden) { look(input.value.trim()); return; }
          if (items.length) { active = (active + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length; paint(); }
        } else if (e.key === "Enter") {
          if (!list.hidden && items[active]) { e.preventDefault(); choose(items[active]); }
        } else if (e.key === "Escape") {
          if (!list.hidden) { e.stopPropagation(); hide(); sync(); }
        }
      });
      input.addEventListener("blur", function () { window.setTimeout(function () { hide(); sync(); }, 120); });
      clear.addEventListener("click", function () {
        select.value = "";
        select.dispatchEvent(new Event("change", { bubbles: true }));
        sync();
        input.focus();
      });
      select.addEventListener("change", sync);
      sync();
    });
  }

  /* ---- auto-fill on the lease form -------------------------------------------------------- */
  function leaseForm() {
    var form = document.getElementById("lease_form");
    if (!form) { return; }
    var unit = document.getElementById("id_unit");
    var prop = document.getElementById("id_property");
    var start = document.getElementById("id_start_date");
    var end = document.getElementById("id_end_date");
    var rent = document.getElementById("id_annual_rent");
    var vat = document.getElementById("id_vat_applies");
    if (!unit || !prop || !start) { return; }
    var url = form.getAttribute("data-info-url") || (window.location.pathname.replace(/\/(add|\d+\/change)\/?$/, "/") + "info/");
    var leaseId = (window.location.pathname.match(/\/(\d+)\/change\/?$/) || [])[1] || "";
    var lastRent = "", vatTouched = false, busy = false;

    var note = el("div", "erp-field-note");
    note.hidden = true;
    var anchor = unit.closest(".related-widget-wrapper") || unit.parentNode;
    anchor.parentNode.insertBefore(note, anchor.nextSibling);
    if (vat) { vat.addEventListener("change", function () { vatTouched = true; }); }

    function fire(node) { node.dispatchEvent(new Event("change", { bubbles: true })); }

    function ask(applyDefaults) {
      var query = new URLSearchParams({ unit: unit.value, property: prop.value, start: start.value, lease: leaseId });
      fetch(url + "?" + query.toString(), { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) { return; }
          if (applyDefaults) {
            if (rent && data.rent && (rent.value === "" || rent.value === lastRent)) {
              rent.value = data.rent;
              lastRent = data.rent;
            }
            if (vat && !vatTouched && data.vat) { vat.checked = true; }
          }
          if (end && data.end && end.value === "") { end.value = data.end; }
          var parts = (data.notes || []).slice();
          note.classList.toggle("is-warning", !!data.warning);
          if (data.warning) { parts.unshift(data.warning); }
          note.textContent = parts.join(" · ");
          note.hidden = !parts.length;
        });
    }

    /* Quick-fill buttons under the date fields: "Today" for the start, "+1 Year" for the end. */
    var datesUrl = url.replace(/info\/$/, "dates/");
    function quickDate(input, label, title, fill) {
      var bar = el("div", "erp-datebtns");
      var btn = el("button", "erp-fbtn erp-datebtn", label);
      btn.type = "button";
      btn.title = title;
      btn.addEventListener("click", function () {
        fetch(datesUrl + "?" + new URLSearchParams({ start: start.value }).toString(), { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (data) { if (data) { fill(data); } });
      });
      bar.appendChild(btn);
      var host = input.closest(".related-widget-wrapper") || input.parentNode;
      host.insertAdjacentElement("afterend", bar);
    }
    function setDate(input, text) {
      input.value = text;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      fire(input);
    }
    quickDate(start, "Today", "Starts today", function (data) { setDate(start, data.today); });
    if (end) {
      quickDate(end, "+1 Year", "Ends one year after the start date (the day before the anniversary)", function (data) {
        if (data.year_end) { setDate(end, data.year_end); return; }
        // no start date yet: start today, then one year from today
        setDate(start, data.today);
        fetch(datesUrl + "?" + new URLSearchParams({ start: data.today }).toString(), { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d2) { if (d2 && d2.year_end) { setDate(end, d2.year_end); } });
      });
    }

    unit.addEventListener("change", function () {
      if (busy) { return; }
      if (unit.value && prop.value) { busy = true; prop.value = ""; fire(prop); busy = false; }
      ask(true);
    });
    prop.addEventListener("change", function () {
      if (busy) { return; }
      if (prop.value && unit.value) { busy = true; unit.value = ""; fire(unit); busy = false; }
      ask(true);
    });
    ["change", "blur"].forEach(function (name) {
      start.addEventListener(name, function () {
        if ((unit.value || prop.value) && start.value) { ask(false); }
      });
    });
    if (unit.value || prop.value) { ask(false); }
  }

  /* ---- several photos at once on the Photos tab of a unit or property ------------------------ */
  function photoUpload() {
    var group = document.getElementById("photos-group");
    if (!group || group.querySelector(".erp-photobar")) { return; }
    var url = window.location.pathname.replace(/change\/?$/, "") + "photos/upload/";
    var bar = el("div", "erp-photobar");
    var text = el("span", "erp-photobar-text", "Add many photos at once: choose them here, or drag them onto this bar.");
    var pick = el("button", "erp-fbtn erp-fbtn-primary", "Upload Photos");
    pick.type = "button";
    var input = el("input", "");
    input.type = "file";
    input.multiple = true;
    input.accept = "image/png,image/jpeg,image/webp";
    input.hidden = true;
    var status = el("span", "erp-photobar-status");
    bar.appendChild(text);
    bar.appendChild(pick);
    bar.appendChild(status);
    bar.appendChild(input);
    group.insertBefore(bar, group.firstChild);

    function upload(files) {
      if (!files || !files.length) { return; }
      var body = new FormData();
      Array.prototype.forEach.call(files, function (f) { body.append("photos", f); });
      pick.disabled = true;
      status.textContent = "Uploading " + files.length + " photo" + (files.length === 1 ? "" : "s") + " ...";
      status.classList.remove("is-bad");
      fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" }, body: body })
        .then(function (r) { return r.json().catch(function () { return { ok: false, errors: ["Something went wrong."] }; }); })
        .then(function (reply) {
          if (reply && reply.ok) {
            try { window.sessionStorage.setItem("erpReopenTab", "Photos"); } catch (err) { /* private window */ }
            reload();
          } else {
            pick.disabled = false;
            status.classList.add("is-bad");
            status.textContent = ((reply && reply.errors) || ["Something went wrong."]).join(" ");
          }
        })
        .catch(function () { pick.disabled = false; status.classList.add("is-bad"); status.textContent = "The server could not be reached."; });
    }
    pick.addEventListener("click", function () { input.click(); });
    input.addEventListener("change", function () { upload(input.files); });
    ["dragenter", "dragover"].forEach(function (name) {
      bar.addEventListener(name, function (e) { e.preventDefault(); bar.classList.add("is-drop"); });
    });
    ["dragleave", "drop"].forEach(function (name) {
      bar.addEventListener(name, function (e) { e.preventDefault(); bar.classList.remove("is-drop"); });
    });
    bar.addEventListener("drop", function (e) { upload(e.dataTransfer && e.dataTransfer.files); });
  }

  /* After an upload the page reloads; open the tab the person was on. */
  function reopenTab() {
    var name = "";
    try { name = window.sessionStorage.getItem("erpReopenTab") || ""; window.sessionStorage.removeItem("erpReopenTab"); } catch (err) { name = ""; }
    if (!name) { return; }
    var links = document.querySelectorAll("a, button");
    for (var i = 0; i < links.length; i++) {
      if (links[i].textContent.trim() === name && links[i].closest("nav, ul, [role=tablist], [x-data]")) { links[i].click(); return; }
    }
  }

  function start() { pickers(); leaseForm(); photoUpload(); reopenTab(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
