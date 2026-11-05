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
    var dlg = el("div", "erp-dlg");
    dlg.setAttribute("role", "dialog");
    dlg.setAttribute("aria-modal", "true");
    dlg.setAttribute("aria-label", cfg.title);
    dlg.appendChild(el("h3", "erp-dlg-title", cfg.title));
    if (cfg.subtitle) { dlg.appendChild(el("p", "erp-dlg-sub", cfg.subtitle)); }

    var form = el("form", "erp-dlg-form");
    var inputs = {}, rows = {};
    (cfg.fields || []).forEach(function (f) {
      var row = el("div", "erp-dlg-row");
      var input;
      if (f.type === "select") {
        input = el("select", "erp-dlg-input");
        input.setAttribute("data-erp-native", "1");
        (f.options || []).forEach(function (o) {
          var opt = el("option", "", o[1]);
          opt.value = o[0];
          input.appendChild(opt);
        });
      } else {
        input = el("input", "erp-dlg-input");
        input.type = f.type || "text";
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
        if (f.value !== undefined && f.value !== null) { input.value = f.value; }
        var label = el("label", "erp-dlg-label", f.label);
        label.setAttribute("for", input.id);
        row.appendChild(label);
        row.appendChild(input);
      }
      if (f.hint) { row.appendChild(el("div", "erp-dlg-hint", f.hint)); }
      inputs[f.name] = input;
      rows[f.name] = { row: row, field: f };
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
    function refresh() {
      var v = values();
      Object.keys(rows).forEach(function (name) {
        var f = rows[name].field;
        rows[name].row.hidden = f.when ? !f.when(v) : false;
      });
      if (note) { note.textContent = cfg.note(v) || ""; note.hidden = !note.textContent; }
    }
    form.addEventListener("input", refresh);
    form.addEventListener("change", refresh);

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
      if (e.key === "Escape") { e.stopPropagation(); close(); }
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

  /* ---- the pop-ups -------------------------------------------------------------------------- */
  var METHODS = [["cheque", "Cheque"], ["transfer", "Bank transfer"], ["cash", "Cash"], ["card", "Card"], ["other", "Other"]];

  function recordDialog(url, info) {
    var d = openDialog({
      title: "Record payment",
      subtitle: info.code + " · payment " + info.part + " · " + info.amount + " due " + info.due +
        (info.overdue ? " (overdue)" : ""),
      fields: [
        { name: "date", label: "Date received", type: "date", value: info.today },
        { name: "method", label: "Paid by", type: "select", options: METHODS, value: info.method || "cheque" },
        { name: "cheque_number", label: "Cheque number", type: "text", value: info.cheque, placeholder: "e.g. 000123",
          when: function (v) { return v.method === "cheque"; } },
        { name: "bank_name", label: "Bank", type: "text", value: info.bank, placeholder: "e.g. Emirates NBD",
          when: function (v) { return v.method === "cheque"; } }
      ],
      buttons: [
        { label: "Record payment", kind: "primary", run: function (v, ui) {
          send(url, { action: "paid", date: v.date, method: v.method, cheque_number: v.cheque_number, bank_name: v.bank_name }, ui);
        } },
        { label: "Cheque bounced", kind: "danger", run: function (v, ui) { send(url, { action: "bounced" }, ui); } }
      ]
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
        { name: "deposit_status", label: "Security deposit is", type: "select", value: info.deposit,
          options: [["held", "Held"], ["refunded", "Refunded"], ["deducted", "Deducted (kept)"]] },
        { name: "cancel_unpaid", label: "Cancel the unpaid payments due after that day", type: "checkbox", value: true }
      ],
      note: function () {
        return info.pending_later ? info.pending_later + " pending payment(s) are still to come." : "";
      },
      buttons: [{ label: "Terminate lease", kind: "danger", run: function (v, ui) {
        if (!v.reason.trim()) { ui.error(["Say why the lease is ending early."]); return; }
        send(url, { date: v.date, reason: v.reason, deposit_status: v.deposit_status, cancel_unpaid: v.cancel_unpaid }, ui);
      } }]
    });
  }

  function chequesDialog(url, info) {
    openDialog({
      title: "Cheque numbers for " + info.code,
      subtitle: "Enter the first cheque number. The others follow in order, one for each payment that is not paid yet (" + info.unpaid + ").",
      fields: [
        { name: "first", label: "First cheque number", type: "text", placeholder: "e.g. 000123 or CQ-0042" },
        { name: "bank", label: "Bank (optional)", type: "text", placeholder: "e.g. Emirates NBD" }
      ],
      buttons: [{ label: "Fill in the numbers", kind: "primary", run: function (v, ui) {
        if (!v.first.trim()) { ui.error(["Enter the first cheque number."]); return; }
        send(url, { first: v.first, bank: v.bank }, ui);
      } }]
    });
  }

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
  }, true);

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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", leaseForm);
  } else {
    leaseForm();
  }
})();
