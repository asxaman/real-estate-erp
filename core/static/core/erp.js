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

  function init() {
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
