/* Real Estate ERP - small UX layer on top of Unfold admin forms.
   1. Turns the grey help text under fields into a hover "i" icon next to the label.
   2. On the Property form: live "Has units / Standalone" pill and automatic upper-case code.
   Nothing here changes what is submitted to the server. */
(function () {
  "use strict";

  var BUILDING_TYPES = ["residential_building", "commercial_building"];
  var tip = null;

  function ensureTip() {
    if (!tip) {
      tip = document.createElement("div");
      tip.id = "erp-tooltip";
      tip.setAttribute("role", "tooltip");
      document.body.appendChild(tip);
    }
    return tip;
  }

  function showTip(el, text) {
    var t = ensureTip();
    t.textContent = text;
    t.classList.add("show");
    var r = el.getBoundingClientRect();
    var w = t.offsetWidth;
    var h = t.offsetHeight;
    var left = r.left + r.width / 2 - w / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    var top = r.top - h - 8;
    if (top < 8) {
      top = r.bottom + 8;
    }
    t.style.left = left + "px";
    t.style.top = top + "px";
  }

  function hideTip() {
    if (tip) {
      tip.classList.remove("show");
    }
  }

  function findLabel(help, form) {
    // Best case: the input points to its help text with aria-describedby.
    if (help.id) {
      var control = form.querySelector('[aria-describedby~="' + help.id + '"]');
      if (control && control.id) {
        var direct = form.querySelector('label[for="' + control.id + '"]');
        if (direct) {
          return direct;
        }
      }
    }
    // Fallback: walk up and take the closest label that comes before the help text.
    var node = help.parentElement;
    while (node && node !== form) {
      var labels = Array.prototype.slice.call(node.querySelectorAll("label[for]"));
      var before = labels.filter(function (l) {
        return l.compareDocumentPosition(help) & Node.DOCUMENT_POSITION_FOLLOWING;
      });
      if (before.length) {
        return before[before.length - 1];
      }
      node = node.parentElement;
    }
    return null;
  }

  function makeIcon(label, text) {
    if (label.querySelector(".erp-info")) {
      return;
    }
    var icon = document.createElement("span");
    icon.className = "erp-info";
    icon.tabIndex = 0;
    icon.setAttribute("role", "button");
    icon.setAttribute("aria-label", "More information");
    icon.textContent = "i";
    icon.addEventListener("mouseenter", function () { showTip(icon, text); });
    icon.addEventListener("mouseleave", hideTip);
    icon.addEventListener("focus", function () { showTip(icon, text); });
    icon.addEventListener("blur", hideTip);
    icon.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      showTip(icon, text);
    });
    label.appendChild(icon);
  }

  function labelForControl(control, form) {
    if (control.id) {
      var direct = form.querySelector('label[for="' + control.id + '"]');
      if (direct) {
        return direct;
      }
    }
    // Fallback: closest label that comes before the control.
    var node = control.parentElement;
    while (node && node !== form) {
      var labels = Array.prototype.slice.call(node.querySelectorAll("label"));
      var before = labels.filter(function (l) {
        return l.compareDocumentPosition(control) & Node.DOCUMENT_POSITION_FOLLOWING;
      });
      if (before.length) {
        return before[before.length - 1];
      }
      node = node.parentElement;
    }
    return null;
  }

  // The server puts each field's help text on the input as data-erp-help.
  function addInfoIconsFromData(form) {
    Array.prototype.forEach.call(form.querySelectorAll("[data-erp-help]"), function (control) {
      var text = control.getAttribute("data-erp-help");
      var label = labelForControl(control, form);
      if (text && label) {
        makeIcon(label, text);
      }
    });
  }

  function addInfoIcons(form) {
    var helps = form.querySelectorAll(".help, .helptext");
    Array.prototype.forEach.call(helps, function (help) {
      if (help.getAttribute("data-erp-done") || help.closest("table")) {
        return;
      }
      var text = (help.textContent || "").replace(/\s+/g, " ").trim();
      if (!text) {
        return;
      }
      var label = findLabel(help, form);
      if (!label) {
        return;
      }
      help.setAttribute("data-erp-done", "1");
      help.classList.add("erp-help-hidden");

      var icon = document.createElement("span");
      icon.className = "erp-info";
      icon.tabIndex = 0;
      icon.setAttribute("role", "button");
      icon.setAttribute("aria-label", "More information");
      icon.textContent = "i";
      icon.addEventListener("mouseenter", function () { showTip(icon, text); });
      icon.addEventListener("mouseleave", hideTip);
      icon.addEventListener("focus", function () { showTip(icon, text); });
      icon.addEventListener("blur", hideTip);
      icon.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        showTip(icon, text);
      });
      label.appendChild(icon);
    });
  }

  function markRows(form) {
    Array.prototype.forEach.call(form.querySelectorAll("label[for]"), function (label) {
      var row = label.closest(".form-row, .field-line");
      if (row) {
        row.classList.add("erp-row");
      }
    });
  }

  // ---- Property form: show only the fields that make sense --------------------------

  var NO_BUILDING_INFO_TYPES = ["warehouse", "yard", "shop", "apartment", "parking_spot"];

  function namedControls(node) {
    return node.querySelectorAll('[name]:not([type="hidden"])');
  }

  // True when the block holds no input other than this one.
  function holdsOnly(node, control) {
    var found = namedControls(node);
    for (var i = 0; i < found.length; i++) {
      if (found[i] !== control) {
        return false;
      }
    }
    return true;
  }

  function mark(node) {
    node.style.display = "none";
    node.setAttribute("data-erp-hid", "1");
  }

  // The blocks on the page that belong to one field: its input (with wrappers) and its label.
  function fieldNodes(form, control) {
    var label = control.id ? form.querySelector('label[for="' + control.id + '"]') : null;
    var anchor = control;
    while (
      anchor.parentElement &&
      anchor.parentElement !== form &&
      holdsOnly(anchor.parentElement, control)
    ) {
      anchor = anchor.parentElement;
    }
    var nodes = [anchor];
    if (label && !anchor.contains(label)) {
      var labelAnchor = label;
      while (
        labelAnchor.parentElement &&
        labelAnchor.parentElement !== form &&
        namedControls(labelAnchor.parentElement).length === 0
      ) {
        labelAnchor = labelAnchor.parentElement;
      }
      nodes.push(labelAnchor);
    }
    return nodes;
  }

  function hideFields(form, names) {
    var wanted = {};
    names.forEach(function (n) { wanted[n] = true; });
    var hidden = [];
    names.forEach(function (name) {
      var control = form.querySelector('[name="' + name + '"]');
      if (!control) {
        return;
      }
      fieldNodes(form, control).forEach(function (node) {
        mark(node);
        hidden.push(node);
      });
    });
    // Also hide any enclosing block (row, card, section with its heading) that now
    // contains nothing but hidden fields.
    hidden.forEach(function (node) {
      var parent = node.parentElement;
      while (parent && parent !== form) {
        var inside = namedControls(parent);
        var onlyHidden = inside.length > 0;
        for (var i = 0; i < inside.length; i++) {
          if (!wanted[inside[i].getAttribute("name")]) {
            onlyHidden = false;
            break;
          }
        }
        if (!onlyHidden) {
          break;
        }
        mark(parent);
        parent = parent.parentElement;
      }
    });
  }

  function propertyFormExtras(form) {
    var typeSelect = form.querySelector('[name="property_type"]');
    var ownerSelect = form.querySelector('[name="owner"]');
    var feeSelect = form.querySelector('[name="management_fee_type"]');

    // Pill next to the "Property type" label
    var badge = null;
    if (typeSelect && typeSelect.id) {
      var typeLabel = form.querySelector('label[for="' + typeSelect.id + '"]');
      if (typeLabel) {
        badge = document.createElement("span");
        typeLabel.appendChild(badge);
      }
    }

    function updateBadge() {
      if (!badge || !typeSelect) {
        return;
      }
      if (!typeSelect.value) {
        badge.className = "";
        badge.textContent = "";
        return;
      }
      var hasUnits = BUILDING_TYPES.indexOf(typeSelect.value) !== -1;
      badge.className = "erp-badge " + (hasUnits ? "is-units" : "is-standalone");
      badge.textContent = hasUnits ? "Has units" : "Standalone · no units";
    }

    // Is the selected owner the built-in "Company Owned" owner?
    function ownerIsCompany() {
      if (!ownerSelect) {
        return false;
      }
      var companyId = ownerSelect.getAttribute("data-company-owner-id");
      if (ownerSelect.value && companyId && ownerSelect.value === companyId) {
        return true;
      }
      var option = ownerSelect.options ? ownerSelect.options[ownerSelect.selectedIndex] : null;
      return !!(option && option.text && option.text.trim().toLowerCase() === "company owned");
    }

    function applyRules() {
      // Show everything we hid before, then hide what does not apply now.
      Array.prototype.forEach.call(form.querySelectorAll("[data-erp-hid]"), function (n) {
        n.style.display = "";
        n.removeAttribute("data-erp-hid");
      });

      var type = typeSelect ? typeSelect.value : "";
      var feeType = feeSelect ? feeSelect.value : "";
      var isBuilding = BUILDING_TYPES.indexOf(type) !== -1;
      var isStandalone = !!type && !isBuilding;

      var hide = [];
      if (NO_BUILDING_INFO_TYPES.indexOf(type) !== -1) {
        hide.push("year_built", "total_floors");
      }
      if (type !== "apartment") {
        hide.push("apartment_type", "bathrooms");
      }
      if (!isStandalone) {
        hide.push("annual_rent", "status");
      }
      if (ownerIsCompany()) {
        hide.push("management_fee_type", "management_fee_percent", "management_fee_fixed");
      } else {
        if (feeType !== "percent") {
          hide.push("management_fee_percent");
        }
        if (feeType !== "fixed") {
          hide.push("management_fee_fixed");
        }
      }
      hideFields(form, hide);
    }

    function refresh() {
      updateBadge();
      applyRules();
    }

    // Native change events, plus a light check every 300 ms because the searchable
    // Owner dropdown does not always fire a native change event.
    [typeSelect, ownerSelect, feeSelect].forEach(function (el) {
      if (el) {
        el.addEventListener("change", refresh);
      }
    });
    var lastState = "";
    window.setInterval(function () {
      var state = [
        typeSelect ? typeSelect.value : "",
        ownerSelect ? ownerSelect.value : "",
        ownerIsCompany() ? "company" : "other",
        feeSelect ? feeSelect.value : "",
      ].join("|");
      if (state !== lastState) {
        lastState = state;
        refresh();
      }
    }, 300);
    refresh();

    var code = form.querySelector('input[name="code"]');
    if (code) {
      code.addEventListener("input", function () {
        var s = code.selectionStart;
        var e = code.selectionEnd;
        code.value = code.value.toUpperCase();
        try {
          code.setSelectionRange(s, e);
        } catch (err) { /* ignore */ }
      });
    }
  }

  /* ---- Owner and tenant pages: show only the fields that apply ---- */
  function statusExtras(form) {
    var active = form.querySelector('[name="is_active"]');
    var blacklisted = form.querySelector('[name="is_blacklisted"]');
    var type = form.querySelector('[name="tenant_type"]');

    function applyRules() {
      Array.prototype.forEach.call(form.querySelectorAll("[data-erp-hid]"), function (n) {
        n.style.display = "";
        n.removeAttribute("data-erp-hid");
      });
      var hide = [];
      if (active && active.checked) {
        hide.push("inactive_reason");  // a reason only matters for an inactive record
      }
      if (blacklisted && !blacklisted.checked) {
        hide.push("blacklist_reason");
      }
      if (type) {
        if (type.value === "company") {
          hide.push("emirates_id_number", "passport_number", "date_of_birth");
        } else {
          hide.push("trade_licence_number", "authorised_signatory");
        }
      }
      hideFields(form, hide);
    }

    [active, blacklisted, type].forEach(function (el) {
      if (el) {
        el.addEventListener("change", applyRules);
      }
    });
    // The themed dropdown does not always fire a native change event, so also check regularly.
    var last = "";
    window.setInterval(function () {
      var state = [
        active ? active.checked : "",
        blacklisted ? blacklisted.checked : "",
        type ? type.value : "",
      ].join("|");
      if (state !== last) {
        last = state;
        applyRules();
      }
    }, 300);
    applyRules();
  }

  /* ---- Unsaved-changes guard: our own pop-up instead of the browser's "Leave site?" box ---- */
  function leaveGuard(form) {
    var initial = null;
    var allowLeave = false;
    var modal = null;
    var pendingHref = null;
    var lastFocus = null;

    function serialise() {
      try {
        return new URLSearchParams(new FormData(form)).toString();
      } catch (e) {
        return "";
      }
    }
    // Take the starting snapshot a moment after load, once dropdowns have finished setting up.
    setTimeout(function () { initial = serialise(); }, 600);

    function isDirty() {
      return initial !== null && serialise() !== initial;
    }

    function closeModal() {
      if (!modal) { return; }
      modal.classList.remove("is-open");
      pendingHref = null;
      if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
    }

    function buildModal() {
      modal = document.createElement("div");
      modal.id = "erp-leave-modal";
      modal.innerHTML =
        '<div class="erp-leave-dialog" role="alertdialog" aria-modal="true" ' +
        'aria-labelledby="erp-leave-title" aria-describedby="erp-leave-text">' +
        '<div class="erp-leave-icon" aria-hidden="true">!</div>' +
        '<h2 id="erp-leave-title">Leave this page?</h2>' +
        '<p id="erp-leave-text">You have unsaved changes. If you leave now, they will be lost.</p>' +
        '<div class="erp-leave-actions">' +
        '<button type="button" class="erp-leave-stay">Keep editing</button>' +
        '<button type="button" class="erp-leave-go">Leave without saving</button>' +
        '</div></div>';
      document.body.appendChild(modal);
      modal.addEventListener("click", function (e) {
        if (e.target === modal) { closeModal(); }
      });
      modal.querySelector(".erp-leave-stay").addEventListener("click", closeModal);
      modal.querySelector(".erp-leave-go").addEventListener("click", function () {
        var href = pendingHref;
        allowLeave = true;
        modal.classList.remove("is-open");
        if (href) { window.location.href = href; }
      });
      modal.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { e.stopPropagation(); closeModal(); }
        if (e.key === "Tab") {  // keep keyboard focus inside the pop-up
          var stay = modal.querySelector(".erp-leave-stay");
          var go = modal.querySelector(".erp-leave-go");
          if (e.shiftKey && document.activeElement === stay) { e.preventDefault(); go.focus(); }
          else if (!e.shiftKey && document.activeElement === go) { e.preventDefault(); stay.focus(); }
        }
      });
    }

    function showModal(href) {
      if (!modal) { buildModal(); }
      pendingHref = href;
      lastFocus = document.activeElement;
      modal.classList.add("is-open");
      modal.querySelector(".erp-leave-stay").focus();
    }

    document.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
      var link = e.target.closest ? e.target.closest("a[href]") : null;
      if (!link || (modal && modal.contains(link))) { return; }
      var href = link.getAttribute("href");
      if (!href || href.charAt(0) === "#" || /^(javascript|mailto|tel):/i.test(href)) { return; }
      if (link.target && link.target !== "_self") { return; }
      if (link.hasAttribute("download") || link.closest(".related-widget-wrapper") || /_popup=/.test(href)) { return; }
      if (!isDirty()) { return; }
      e.preventDefault();
      e.stopPropagation();
      showModal(link.href);
    }, true);

    form.addEventListener("submit", function () { allowLeave = true; });

    // Closing the tab, reloading or the Back button cannot be intercepted by a page:
    // for those the browser's own box is the only option.
    window.addEventListener("beforeunload", function (e) {
      if (!allowLeave && !window.__erpAllowLeave && isDirty()) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
  }

  /* ---- Delete confirmation: a pop-up instead of Django's separate "Are you sure?" page ---- */
  function deleteGuard() {
    var CANCEL_TEXT = /take me back|^no,|^yes,|^cancel$/i;
    var BLOCKED = /protected related objects|would require deleting|have permission to delete|cannot delete/i;

    function el(tag, cls, text) {
      var node = document.createElement(tag);
      if (cls) { node.className = cls; }
      if (text) { node.textContent = text; }
      return node;
    }

    // Read the visible text of a node line by line (it must be in the page, even off-screen).
    function textLines(node) {
      var holder = el("div");
      holder.style.cssText = "position:absolute;left:-9999px;top:0;width:800px;";
      holder.appendChild(node);
      document.body.appendChild(holder);
      var lines = (holder.innerText || "").split("\n")
        .map(function (l) { return l.trim(); })
        .filter(function (l) { return l && !CANCEL_TEXT.test(l); });
      holder.remove();
      return lines;
    }

    function singular(word) {
      if (/ies$/i.test(word)) { return word.replace(/ies$/i, "y"); }
      if (/(ss|us)$/i.test(word)) { return word; }
      return word.replace(/s$/i, "");
    }

    function csrfToken() {
      var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
      if (input) { return input.value; }
      var m = document.cookie.match(/csrftoken=([^;]+)/);
      return m ? decodeURIComponent(m[1]) : "";
    }

    // The request that really deletes: our own form, so it does not depend on Unfold's markup.
    function submitDelete(url, pairs) {
      var form = el("form");
      form.setAttribute("method", "post");
      form.setAttribute("action", url);
      form.style.display = "none";
      pairs.concat([["csrfmiddlewaretoken", csrfToken()], ["post", "yes"]]).forEach(function (kv) {
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = kv[0];
        input.value = kv[1];
        form.appendChild(input);
      });
      document.body.appendChild(form);
      window.__erpAllowLeave = true;  // no "unsaved changes" box on the way out
      HTMLFormElement.prototype.submit.call(form);
    }

    function closeDialog() {
      var old = document.getElementById("erp-delete-modal");
      if (old) { old.remove(); }
    }

    // opts: title, text, sections [{heading, items[], chips}], confirmLabel, onConfirm
    function showDialog(opts) {
      closeDialog();
      var overlay = el("div");
      overlay.id = "erp-delete-modal";
      var dlg = el("div", "erp-leave-dialog erp-del-dialog");
      dlg.setAttribute("role", "alertdialog");
      dlg.setAttribute("aria-modal", "true");
      dlg.setAttribute("aria-label", opts.title);
      dlg.appendChild(el("div", "erp-leave-icon erp-del-icon", "!"));
      dlg.appendChild(el("h2", "", opts.title));
      if (opts.text) { dlg.appendChild(el("p", "", opts.text)); }

      (opts.sections || []).forEach(function (sec) {
        if (!sec.items.length) { return; }
        var box = el("div", "erp-del-section");
        if (sec.heading) { box.appendChild(el("div", "erp-del-heading", sec.heading)); }
        var list = el("ul", sec.chips ? "erp-del-chips" : "erp-del-list");
        sec.items.forEach(function (item) { list.appendChild(el("li", "", item)); });
        box.appendChild(list);
        dlg.appendChild(box);
      });

      var actions = el("div", "erp-leave-actions");
      var cancel = el("button", "erp-leave-stay", opts.confirmLabel ? "Cancel" : "Close");
      cancel.type = "button";
      actions.appendChild(cancel);
      var go = null;
      if (opts.confirmLabel) {
        go = el("button", "erp-del-go", opts.confirmLabel);
        go.type = "button";
        actions.appendChild(go);
      }
      dlg.appendChild(actions);
      overlay.appendChild(dlg);
      document.body.appendChild(overlay);
      overlay.classList.add("is-open");
      cancel.focus();

      cancel.addEventListener("click", closeDialog);
      overlay.addEventListener("click", function (e) { if (e.target === overlay) { closeDialog(); } });
      overlay.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { e.stopPropagation(); closeDialog(); }
        if (e.key === "Tab" && go) {
          if (e.shiftKey && document.activeElement === cancel) { e.preventDefault(); go.focus(); }
          else if (!e.shiftKey && document.activeElement === go) { e.preventDefault(); cancel.focus(); }
        }
      });
      if (go) {
        go.addEventListener("click", function () {
          go.disabled = true;
          go.textContent = "Deleting...";
          opts.onConfirm();
        });
      }
    }

    // opts: url, body (POST data, optional), postUrl, pairs, names, count, noun, fallback
    function confirmDelete(opts) {
      var options = { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } };
      if (opts.body) { options.method = "POST"; options.body = opts.body; }
      fetch(opts.url, options).then(function (response) {
        if (response.redirected && /login/.test(response.url)) { throw new Error("login"); }
        return response.text();
      }).then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var pageText = doc.body ? doc.body.textContent : "";
        var area = doc.querySelector("main") || doc.querySelector("#content-main") || doc.querySelector("#content");
        var lines = area ? textLines(document.importNode(area, true)) : [];

        // Django itself says it cannot delete (protected related records / no permission).
        if (BLOCKED.test(area ? area.textContent : pageText)) {
          var reason = lines.filter(function (l) { return l.length > 25; }).slice(0, 6);
          showDialog({
            title: "This cannot be deleted",
            text: reason.length ? "" : "Other records depend on it, or you do not have permission to delete it.",
            sections: [{ heading: "", items: reason }]
          });
          return;
        }

        // Read Django's own list of what will be deleted; fall back to the names we already know.
        var low = lines.map(function (l) { return l.toLowerCase(); });
        var iSummary = low.indexOf("summary");
        var iObjects = low.indexOf("objects");
        var summary = [], objects = [], noun = opts.noun || "", count = opts.count, noundFromPage = false;
        if (iObjects > -1) {
          summary = iSummary > -1 ? lines.slice(iSummary + 1, iObjects) : [];
          objects = lines.slice(iObjects + 1);
          var text = lines.join("\n");
          var found = (text.match(/delete the selected ([^?\n]+)\?/i) || [])[1] ||
                      (text.match(/delete the ([^"“”'?\n]+?)\s*["“”']/i) || [])[1];
          if (found) { noun = found.trim(); noundFromPage = true; }
          var n = parseInt(((summary[0] || "").match(/:\s*(\d+)/) || [])[1], 10);
          if (n) { count = n; }
        }
        if (!objects.length) { objects = opts.names; }
        if (!noundFromPage && count <= 1) { noun = singular(noun); }  // "properties" -> "property"

        // "Property: Sharjah 3 (SHJ3)" -> name "Sharjah 3 (SHJ3)"
        var first = (objects[0] || "").replace(/^[^:]{1,40}:\s*/, "");
        var related = objects.length > 1;
        var title, message;
        if (count > 1) {
          title = "Delete " + count + " " + (noun || "records") + "?";
          message = "These " + count + " " + (noun || "records") + " will be permanently deleted" +
                    (related ? ", together with the related items listed below" : "") +
                    ". This cannot be undone.";
        } else {
          title = noun && first ? "Delete " + noun + " “" + first + "”?" : "Delete this record?";
          message = related
            ? "This " + (noun || "record") + " and the related items listed below will be permanently deleted. This cannot be undone."
            : "This " + (noun || "record") + " will be permanently deleted. This cannot be undone.";
        }
        showDialog({
          title: title,
          text: message,
          sections: [
            { heading: "Summary", items: summary.length > 1 ? summary : [], chips: true },
            { heading: count > 1 || related ? "Will be deleted" : "", items: count > 1 || related ? objects : [] }
          ],
          confirmLabel: "Yes, delete",
          onConfirm: function () { submitDelete(opts.postUrl, opts.pairs); }
        });
      }).catch(function () { opts.fallback(); });
    }

    // Delete button inside a record
    document.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
      var link = e.target.closest ? e.target.closest("a[href]") : null;
      if (!link) { return; }
      if (!/\/delete\/?$/.test(link.pathname || "") || (link.target && link.target !== "_self")) { return; }
      if (link.closest(".related-widget-wrapper") || /_popup=/.test(link.search)) { return; }
      e.preventDefault();
      e.stopPropagation();
      var heading = document.querySelector("h1");
      confirmDelete({
        url: link.href, postUrl: link.href, pairs: [],
        names: heading && heading.textContent.trim() ? [heading.textContent.trim()] : [],
        count: 1, noun: "",
        fallback: function () { window.location.href = link.href; }
      });
    }, true);

    // "Delete selected ..." from a list page
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form || form.tagName !== "FORM" || form.dataset.erpBypass) { return; }
      var data;
      try { data = new FormData(form); } catch (err) { return; }
      if (data.get("action") !== "delete_selected" || data.get("post")) { return; }
      var ticked = Array.prototype.slice.call(form.querySelectorAll('input[name="_selected_action"]:checked'));
      if (!ticked.length) { return; }  // nothing ticked: Django explains it
      e.preventDefault();
      e.stopPropagation();

      var names = ticked.map(function (box) {
        var row = box.closest("tr");
        var best = "";
        if (row) {
          Array.prototype.forEach.call(row.querySelectorAll("a"), function (a) {
            var t = a.textContent.trim();
            if (t.length > best.length) { best = t; }
          });
        }
        return best || "Selected item";
      });
      var select = form.querySelector('select[name="action"]');
      var label = select && select.selectedOptions[0] ? select.selectedOptions[0].textContent : "";
      var noun = (label.match(/delete selected (.+)/i) || [])[1] || "";

      var pairs = [];
      data.forEach(function (value, key) {
        if (key !== "csrfmiddlewaretoken" && key !== "post" && key !== "index" && typeof value === "string") {
          pairs.push([key, value]);
        }
      });
      var target = new URL(form.getAttribute("action") || window.location.href, window.location.href).href;
      confirmDelete({
        url: target, body: data, postUrl: target, pairs: pairs,
        names: names, count: names.length, noun: noun,
        fallback: function () { form.dataset.erpBypass = "1"; HTMLFormElement.prototype.submit.call(form); }
      });
    }, true);
  }

  // Sidebar sections: remember what the person opened or closed (Unfold forgets on every page load).
  // Default is open; the section holding the current page is always open.
  function sidebarMemory() {
    var KEY = "erp-nav-closed";
    function load() {
      try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; }
    }
    function save(state) {
      try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* storage blocked */ }
    }
    function apply() {
      if (!window.Alpine || !window.Alpine.$data) { return; }
      var state = load();
      document.querySelectorAll("h2.cursor-pointer, h2[x-on\\:click]").forEach(function (h) {
        var box = h.closest("[x-data]");
        var title = (h.textContent || "").trim();
        if (!box || !title || h.dataset.erpNav) { return; }
        h.dataset.erpNav = "1";
        var data = window.Alpine.$data(box);
        if (!data || typeof data.navigationOpen === "undefined") { return; }
        var hasActive = !!box.querySelector("a[aria-current], li.active, a.font-semibold, a.bg-base-100, [class*='active']");
        data.navigationOpen = hasActive ? true : state[title] !== true;
        h.addEventListener("click", function () {
          setTimeout(function () {
            var s2 = load();
            s2[title] = !data.navigationOpen;  // true = closed
            save(s2);
          }, 0);
        });
      });
    }
    document.addEventListener("alpine:initialized", apply);
    setTimeout(apply, 0);
    setTimeout(apply, 300);
  }

  // Pop-up notifications: the server hands over Django's messages as window.__erpToasts.
  var TOAST_STYLE = {
    success: { title: "Success", icon: "M5 13l4 4L19 7" },
    error: { title: "Error", icon: "M6 6l12 12M18 6L6 18" },
    warning: { title: "Warning", icon: "M12 8v5m0 3h.01" },
    info: { title: "Information", icon: "M12 11v5m0-8h.01" }
  };

  function toastHost() {
    var host = document.getElementById("erp-toasts");
    if (!host) {
      host = document.createElement("div");
      host.id = "erp-toasts";
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    return host;
  }

  function showToast(level, parts) {
    var style = TOAST_STYLE[level] || TOAST_STYLE.info;
    var kind = TOAST_STYLE[level] ? level : "info";
    var toast = document.createElement("div");
    toast.className = "erp-toast erp-toast-" + kind;
    toast.setAttribute("role", kind === "error" ? "alert" : "status");

    var icon = document.createElement("div");
    icon.className = "erp-toast-icon";
    icon.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
      'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="' + style.icon + '"/></svg>';

    var body = document.createElement("div");
    body.className = "erp-toast-body";
    var title = document.createElement("div");
    title.className = "erp-toast-title";
    title.textContent = style.title;
    var message = document.createElement("div");
    message.className = "erp-toast-text";
    (parts || []).forEach(function (part) {
      if (part.href) {
        var link = document.createElement("a");
        link.href = part.href;
        link.textContent = part.t;
        link.className = "erp-toast-link";
        message.appendChild(link);
      } else {
        message.appendChild(document.createTextNode(part.t));
      }
    });
    body.appendChild(title);
    body.appendChild(message);

    var close = document.createElement("button");
    close.type = "button";
    close.className = "erp-toast-close";
    close.setAttribute("aria-label", "Close");
    close.innerHTML = "&times;";

    var bar = document.createElement("div");
    bar.className = "erp-toast-bar";

    toast.appendChild(icon);
    toast.appendChild(body);
    toast.appendChild(close);
    toast.appendChild(bar);
    toastHost().appendChild(toast);

    // Success and information fade away by themselves; warnings and errors stay longer.
    var base = (window.__erpToastSeconds || 5) * 1000;
    var total = kind === "success" || kind === "info" ? base : base * 2;
    var left = total;
    var started = Date.now();
    var timer = null;
    bar.style.animationDuration = total + "ms";

    function dismiss() {
      clearTimeout(timer);
      toast.classList.add("is-leaving");
      setTimeout(function () { toast.remove(); }, 220);
    }
    function start() {
      started = Date.now();
      bar.style.animationPlayState = "running";
      timer = setTimeout(dismiss, left);
    }
    function pause() {
      clearTimeout(timer);
      left -= Date.now() - started;
      bar.style.animationPlayState = "paused";
    }
    toast.addEventListener("mouseenter", pause);
    toast.addEventListener("mouseleave", start);
    close.addEventListener("click", dismiss);
    requestAnimationFrame(function () { toast.classList.add("is-in"); });
    start();
  }

  function showToasts() {
    var list = window.__erpToasts;
    if (!list || !list.length || !document.body) { return; }
    window.__erpToasts = [];
    list.forEach(function (item, i) {
      setTimeout(function () { showToast(item.level, item.parts); }, i * 150);
    });
  }
  window.erpShowToasts = showToasts;

  // File fields (photos, documents): View / Edit / Delete buttons. Delete only ticks a hidden
  // "clear" box; the file is really removed when the form is saved.
  function fileWidgets() {
    var lightbox = null;
    function openLightbox(src) {
      if (!lightbox) {
        lightbox = document.createElement("div");
        lightbox.id = "erp-lightbox";
        lightbox.innerHTML = '<button type="button" class="erp-lightbox-close" aria-label="Close">&times;</button><img alt="">';
        document.body.appendChild(lightbox);
        lightbox.addEventListener("click", function () { lightbox.classList.remove("is-open"); });
        document.addEventListener("keydown", function (e) {
          if (e.key === "Escape") { lightbox.classList.remove("is-open"); }
        });
      }
      lightbox.querySelector("img").src = src;
      lightbox.classList.add("is-open");
    }

    function parts(box) {
      return {
        input: box.querySelector(".erp-file-input"),
        clear: box.querySelector(".erp-file-clear"),
        view: box.querySelector("[data-erp-view]"),
        edit: box.querySelector("[data-erp-edit]"),
        del: box.querySelector("[data-erp-delete]"),
        name: box.querySelector(".erp-file-name"),
        note: box.querySelector(".erp-file-note"),
        img: box.querySelector(".erp-file-preview img")
      };
    }

    function label(btn, text) {
      var l = btn.querySelector(".erp-fbtn-label");
      if (l) { l.textContent = text; }
      btn.title = text;
    }

    function setNote(p, text) {
      p.note.hidden = !text;
      p.note.textContent = text || "";
    }

    function refresh(box) {
      var p = parts(box);
      var hasInitial = !!p.clear || !!p.view;
      var chosen = p.input.files && p.input.files.length ? p.input.files[0] : null;
      var deleted = !!(p.clear && p.clear.checked);
      box.classList.toggle("is-deleted", deleted);
      if (p.del) {
        if (hasInitial) {
          label(p.del, deleted ? "Undo delete" : "Delete");
          p.del.hidden = false;
        } else {
          p.del.hidden = !chosen;
        }
      }
      if (p.view) { p.view.hidden = deleted; }
      if (!hasInitial && p.name) {
        p.name.textContent = chosen ? chosen.name : "No file";
        p.name.classList.toggle("erp-file-empty", !chosen);
      }
      if (deleted) {
        setNote(p, "Will be deleted when you save.");
      } else if (chosen) {
        setNote(p, "New file selected. It is saved when you press Save.");
      } else {
        setNote(p, "");
      }
    }

    document.addEventListener("click", function (e) {
      var target = e.target.closest ? e.target.closest("[data-erp-view],[data-erp-edit],[data-erp-delete]") : null;
      if (!target) { return; }
      var box = target.closest("[data-erp-file]");
      if (!box) { return; }
      var p = parts(box);
      if (target.hasAttribute("data-erp-view")) {
        if (box.getAttribute("data-image") === "1") {
          e.preventDefault();
          openLightbox(target.getAttribute("href"));
        }
        return;  // documents (PDF) open in a new tab
      }
      e.preventDefault();
      if (target.hasAttribute("data-erp-edit")) {
        p.input.click();
        return;
      }
      // Delete / Undo / Remove
      if (p.clear) {
        p.clear.checked = !p.clear.checked;
        if (p.clear.checked) { p.input.value = ""; }
      } else {
        p.input.value = "";
      }
      if (p.img && p.img.getAttribute("data-original")) { p.img.src = p.img.getAttribute("data-original"); }
      refresh(box);
    });

    document.addEventListener("change", function (e) {
      if (!e.target.classList || !e.target.classList.contains("erp-file-input")) { return; }
      var box = e.target.closest("[data-erp-file]");
      var p = parts(box);
      if (p.clear && e.target.files.length) { p.clear.checked = false; }  // a new file replaces the delete
      var file = e.target.files && e.target.files[0];
      if (p.img && file && /^image\//.test(file.type)) {
        if (!p.img.getAttribute("data-original")) { p.img.setAttribute("data-original", p.img.src); }
        p.img.src = URL.createObjectURL(file);
      }
      refresh(box);
    });
  }

  // Dropdowns: every normal <select> in the app is shown as our own styled, animated dropdown.
  // The real <select> stays in the page (hidden), so forms, validation and other scripts keep
  // working: choosing an option sets its value and fires a normal "change" event.
  function customSelects() {
    var SEARCH_FROM = 9;  // show a search box when there are more options than this
    var CHECK = '<svg class="erp-opt-check" viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
      'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M5 13l4 4L19 7"/></svg>';
    var CHEVRON = '<svg class="erp-select-chevron" viewBox="0 0 24 24" width="18" height="18" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M6 9l6 6 6-6"/></svg>';
    var uid = 0;
    var current = null;  // the open dropdown: {select, trigger, panel, list, search, items, active}

    function eligible(sel) {
      if (sel.dataset.erpSelect || sel.multiple || sel.size > 1) { return false; }
      if (sel.hasAttribute("data-erp-native")) { return false; }
      if (/__prefix__/.test((sel.name || "") + (sel.id || ""))) { return false; }
      if (sel.closest(".empty-form, .select2, .select2-container")) { return false; }
      if (sel.classList.contains("select2-hidden-accessible")) { return false; }
      if (/autocomplete|select2|tom-select/i.test(sel.className)) { return false; }
      var attrs = sel.attributes;  // searchable (ajax) dropdowns have their own widget
      for (var i = 0; i < attrs.length; i += 1) {
        if (/^data-(ajax|autocomplete|tom|select2)/i.test(attrs[i].name)) { return false; }
      }
      return true;
    }

    function isPlaceholder(option) {
      return !option || option.value === "" && /^[-–—\s]*$/.test(option.text);
    }

    function sync(sel) {
      var trigger = sel._erpTrigger;
      if (!trigger) { return; }
      var option = sel.options[sel.selectedIndex];
      var label = trigger.querySelector(".erp-select-label");
      var empty = isPlaceholder(option);
      label.textContent = empty ? "Select an option" : option.text;
      trigger.classList.toggle("is-placeholder", empty);
      trigger.disabled = sel.disabled;
      trigger.classList.toggle("is-invalid", sel.getAttribute("aria-invalid") === "true");
    }

    function enhance(sel) {
      if (!eligible(sel)) { return; }
      sel.dataset.erpSelect = "1";
      var width = sel.offsetWidth;
      var wrap = document.createElement("div");
      wrap.className = "erp-select";
      if (width > 0) {
        wrap.style.width = width + "px";
        wrap.style.maxWidth = "100%";
      } else {
        wrap.classList.add("erp-select-fluid");  // inside a hidden tab: measured later by CSS
      }
      var trigger = document.createElement("button");
      trigger.type = "button";
      trigger.className = "erp-select-trigger";
      trigger.setAttribute("aria-haspopup", "listbox");
      trigger.setAttribute("aria-expanded", "false");
      trigger.innerHTML = '<span class="erp-select-label"></span>' + CHEVRON;
      var labelEl = sel.id ? document.querySelector('label[for="' + sel.id + '"]') : null;
      if (labelEl) {
        if (!labelEl.id) { labelEl.id = "erp-label-" + (++uid); }
        trigger.setAttribute("aria-labelledby", labelEl.id);
      }
      var holder = sel.parentNode;
      holder.insertBefore(wrap, sel);
      wrap.appendChild(sel);
      wrap.appendChild(trigger);
      // The theme draws its own little arrow next to a select: hide it, ours replaces it.
      Array.prototype.forEach.call(holder.children, function (sibling) {
        if (sibling !== wrap && sibling.matches &&
            sibling.matches('[class*="material-symbols"], svg, .select-arrow')) {
          sibling.style.display = "none";
        }
      });
      sel.classList.add("erp-select-native");
      sel.tabIndex = -1;
      sel.setAttribute("aria-hidden", "true");
      sel._erpTrigger = trigger;

      trigger.addEventListener("click", function () {
        if (current && current.select === sel) { closePanel(); } else { openPanel(sel); }
      });
      trigger.addEventListener("keydown", function (e) {
        if (current) { return; }  // the open dropdown handles keys itself
        if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openPanel(sel);
        }
      });
      sel.addEventListener("focus", function () { trigger.focus(); });  // label click / validation
      sel.addEventListener("change", function () { sync(sel); });
      new MutationObserver(function () { sync(sel); }).observe(sel, {
        childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "aria-invalid"]
      });
      sync(sel);
    }

    function enhanceAll(root) {
      if (root.matches && root.matches("select")) { enhance(root); }
      if (root.querySelectorAll) {
        Array.prototype.forEach.call(root.querySelectorAll("select"), enhance);
      }
    }

    // ---- the open dropdown ----------------------------------------------------------------
    function buildItems(sel, list) {
      var items = [];
      function addOption(option, group) {
        var li = document.createElement("li");
        li.className = "erp-opt";
        li.setAttribute("role", "option");
        li.id = "erp-opt-" + (++uid);
        li.dataset.index = String(option.index);
        var selected = option.index === sel.selectedIndex;
        li.setAttribute("aria-selected", selected ? "true" : "false");
        if (selected) { li.classList.add("is-selected"); }
        if (option.disabled || (group && group.disabled)) {
          li.classList.add("is-disabled");
          li.setAttribute("aria-disabled", "true");
        }
        var text = document.createElement("span");
        text.className = "erp-opt-text";
        var placeholder = isPlaceholder(option);
        text.textContent = placeholder ? "None" : option.text;
        if (placeholder) { li.classList.add("is-none"); }
        li.appendChild(text);
        li.insertAdjacentHTML("beforeend", CHECK);
        list.appendChild(li);
        items.push(li);
      }
      Array.prototype.forEach.call(sel.children, function (child) {
        if (child.tagName === "OPTGROUP") {
          var head = document.createElement("li");
          head.className = "erp-opt-group";
          head.setAttribute("role", "presentation");
          head.textContent = child.label;
          list.appendChild(head);
          Array.prototype.forEach.call(child.children, function (o) { addOption(o, child); });
        } else if (child.tagName === "OPTION") {
          addOption(child, null);
        }
      });
      return items;
    }

    function place() {
      if (!current) { return; }
      var rect = current.trigger.getBoundingClientRect();
      var panel = current.panel;
      var gap = 6;
      var vh = window.innerHeight;
      var below = vh - rect.bottom - gap - 8;
      var above = rect.top - gap - 8;
      panel.style.minWidth = rect.width + "px";
      panel.style.maxWidth = Math.max(rect.width, Math.min(480, window.innerWidth - 16)) + "px";
      current.list.style.maxHeight = "0px";  // measure the natural height of the search box etc.
      var chrome = panel.offsetHeight;
      var natural = current.list.scrollHeight + chrome;
      var openUp = natural > below && above > below;
      var room = Math.max(120, openUp ? above : below);
      current.list.style.maxHeight = Math.min(280, room - chrome) + "px";
      var height = panel.offsetHeight;
      var left = Math.min(Math.max(8, rect.left), window.innerWidth - panel.offsetWidth - 8);
      panel.style.left = left + "px";
      panel.style.top = (openUp ? rect.top - gap - height : rect.bottom + gap) + "px";
      panel.classList.toggle("is-up", openUp);
    }

    function setActive(index, scroll) {
      if (!current) { return; }
      var visible = current.items.filter(function (li) { return !li.hidden; });
      if (!visible.length) { current.active = null; return; }
      var li = visible[Math.max(0, Math.min(index, visible.length - 1))];
      if (current.active) { current.active.classList.remove("is-active"); }
      current.active = li;
      li.classList.add("is-active");
      current.trigger.setAttribute("aria-activedescendant", li.id);
      if (scroll !== false) { li.scrollIntoView({ block: "nearest" }); }
    }

    function move(step) {
      if (!current) { return; }
      var visible = current.items.filter(function (li) {
        return !li.hidden && !li.classList.contains("is-disabled");
      });
      if (!visible.length) { return; }
      var at = visible.indexOf(current.active);
      var next = at === -1 ? (step > 0 ? 0 : visible.length - 1) : at + step;
      next = Math.max(0, Math.min(next, visible.length - 1));
      var all = current.items.filter(function (li) { return !li.hidden; });
      setActive(all.indexOf(visible[next]));
    }

    function choose(li) {
      if (!current || !li || li.classList.contains("is-disabled")) { return; }
      var sel = current.select;
      var index = parseInt(li.dataset.index, 10);
      var changed = sel.selectedIndex !== index;
      sel.selectedIndex = index;
      var trigger = current.trigger;
      closePanel();
      sync(sel);
      if (changed) {
        sel.dispatchEvent(new Event("input", { bubbles: true }));
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
      trigger.focus();
    }

    function openPanel(sel) {
      closePanel(true);
      var trigger = sel._erpTrigger;
      if (!trigger || trigger.disabled) { return; }
      var panel = document.createElement("div");
      panel.className = "erp-select-panel";
      var search = null;
      if (sel.options.length > SEARCH_FROM) {
        search = document.createElement("input");
        search.type = "text";
        search.className = "erp-select-search";
        search.placeholder = "Search...";
        search.setAttribute("aria-label", "Search options");
        search.autocomplete = "off";
        panel.appendChild(search);
      }
      var list = document.createElement("ul");
      list.className = "erp-select-list";
      list.setAttribute("role", "listbox");
      panel.appendChild(list);
      var empty = document.createElement("div");
      empty.className = "erp-select-empty";
      empty.textContent = "No matches";
      empty.hidden = true;
      panel.appendChild(empty);
      var items = buildItems(sel, list);
      document.body.appendChild(panel);
      current = { select: sel, trigger: trigger, panel: panel, list: list, search: search,
                  items: items, empty: empty, active: null, typed: "", typedAt: 0 };
      trigger.setAttribute("aria-expanded", "true");
      trigger.classList.add("is-open");
      place();
      var selected = items.filter(function (li) { return li.classList.contains("is-selected"); })[0];
      setActive(selected ? items.indexOf(selected) : 0);
      requestAnimationFrame(function () { panel.classList.add("is-open"); });
      if (search) { search.focus(); }

      list.addEventListener("mousemove", function (e) {
        var li = e.target.closest ? e.target.closest(".erp-opt") : null;
        if (li && li !== current.active && !li.classList.contains("is-disabled")) {
          setActive(current.items.filter(function (x) { return !x.hidden; }).indexOf(li), false);
        }
      });
      list.addEventListener("click", function (e) {
        var li = e.target.closest ? e.target.closest(".erp-opt") : null;
        if (li) { choose(li); }
      });
      if (search) {
        search.addEventListener("input", function () {
          var q = search.value.trim().toLowerCase();
          var shown = 0;
          current.items.forEach(function (li) {
            var match = !q || li.textContent.toLowerCase().indexOf(q) !== -1;
            li.hidden = !match;
            if (match) { shown += 1; }
          });
          Array.prototype.forEach.call(list.querySelectorAll(".erp-opt-group"), function (g) {
            g.hidden = !!q;
          });
          current.empty.hidden = shown > 0;
          setActive(0);
          place();
        });
      }
    }

    function closePanel(immediate) {
      if (!current) { return; }
      var c = current;
      current = null;
      c.trigger.setAttribute("aria-expanded", "false");
      c.trigger.removeAttribute("aria-activedescendant");
      c.trigger.classList.remove("is-open");
      c.panel.classList.remove("is-open");
      if (immediate) { c.panel.remove(); } else { setTimeout(function () { c.panel.remove(); }, 160); }
    }

    document.addEventListener("keydown", function (e) {
      if (!current) { return; }
      var key = e.key;
      if (key === "Escape") {
        e.preventDefault(); e.stopPropagation();
        var t = current.trigger; closePanel(); t.focus();
      } else if (key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (key === "Home") { e.preventDefault(); setActive(0); }
      else if (key === "End") { e.preventDefault(); setActive(current.items.length); }
      else if (key === "Enter" || (key === " " && !current.search)) {
        e.preventDefault(); e.stopPropagation(); choose(current.active);
      } else if (key === "Tab") { closePanel(); }
      else if (!current.search && key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        var now = Date.now();
        current.typed = (now - current.typedAt > 700 ? "" : current.typed) + key.toLowerCase();
        current.typedAt = now;
        var hit = current.items.filter(function (li) {
          return !li.classList.contains("is-disabled") && li.textContent.trim().toLowerCase().indexOf(current.typed) === 0;
        })[0];
        if (hit) { setActive(current.items.indexOf(hit)); }
      }
    }, true);

    document.addEventListener("mousedown", function (e) {
      if (current && !current.panel.contains(e.target) && !current.trigger.contains(e.target)) {
        closePanel();
      }
    }, true);
    window.addEventListener("resize", function () { if (current) { place(); } });
    window.addEventListener("scroll", function (e) {
      if (!current) { return; }
      if (current.panel.contains(e.target)) { return; }
      place();
    }, true);

    // Scripts that set select.value / selectedIndex directly (no event) still update the display.
    ["value", "selectedIndex"].forEach(function (prop) {
      var desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, prop);
      if (!desc || !desc.set) { return; }
      Object.defineProperty(HTMLSelectElement.prototype, prop, {
        configurable: true,
        enumerable: desc.enumerable,
        get: desc.get,
        set: function (v) {
          desc.set.call(this, v);
          if (this._erpTrigger) { sync(this); }
        }
      });
    });

    document.addEventListener("reset", function (e) {
      setTimeout(function () {
        Array.prototype.forEach.call(e.target.querySelectorAll("select"), sync);
      }, 0);
    }, true);

    enhanceAll(document);
    var queued = false;
    var added = [];
    new MutationObserver(function (records) {
      records.forEach(function (r) {
        Array.prototype.forEach.call(r.addedNodes, function (n) {
          if (n.nodeType === 1) { added.push(n); }
        });
      });
      if (queued || !added.length) { return; }
      queued = true;
      requestAnimationFrame(function () {
        queued = false;
        var nodes = added; added = [];
        nodes.forEach(function (n) { if (n.isConnected) { enhanceAll(n); } });
      });
    }).observe(document.body, { childList: true, subtree: true });
    window.addEventListener("load", function () { enhanceAll(document); });
  }

  function init() {
    customSelects();
    fileWidgets();
    showToasts();
    sidebarMemory();
    deleteGuard();
    var form = document.querySelector('form[id$="_form"]');
    if (!form) {
      return;
    }
    addInfoIconsFromData(form);
    addInfoIcons(form);
    markRows(form);
    leaveGuard(form);
    if (form.id === "property_form") {
      propertyFormExtras(form);
    }
    if (form.id === "owner_form" || form.id === "tenant_form") {
      statusExtras(form);
    }
    window.addEventListener("scroll", hideTip, true);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        hideTip();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
