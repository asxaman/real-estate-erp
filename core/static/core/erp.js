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

  function init() {
    var form = document.querySelector('form[id$="_form"]');
    if (!form) {
      return;
    }
    addInfoIconsFromData(form);
    addInfoIcons(form);
    markRows(form);
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
