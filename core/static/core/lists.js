/* Lists: search as you type (no Enter, no full reload), the search hint, and highlighting of what matched.
   If anything here fails, the normal search form still works. */
(function () {
  "use strict";

  var REGIONS = ["#content-main", "#changelist", "main"];

  function findInput() {
    return document.querySelector('#changelist input[name="q"], #changelist-search input[name="q"], main input[name="q"]');
  }

  function escapeRegExp(text) { return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  /* ---- highlight what matched the search words ------------------------------------------- */
  function clearMarks(root) {
    Array.prototype.forEach.call(root.querySelectorAll("mark.erp-hl"), function (mark) {
      var parent = mark.parentNode;
      parent.replaceChild(document.createTextNode(mark.textContent), mark);
      parent.normalize();
    });
  }

  function highlight(input) {
    var table = document.querySelector("#result_list");
    if (!table) { return; }
    clearMarks(table);
    var words = (input.value || "").split(/\s+/).filter(function (w) { return w.length >= 2; }).slice(0, 8);
    if (!words.length) { return; }
    var pattern = new RegExp("(" + words.map(escapeRegExp).join("|") + ")", "gi");
    var walker = document.createTreeWalker(table.querySelector("tbody") || table, NodeFilter.SHOW_TEXT, null);
    var nodes = [];
    while (walker.nextNode()) { nodes.push(walker.currentNode); }
    nodes.forEach(function (node) {
      var parent = node.parentNode;
      if (!parent || !node.nodeValue.trim()) { return; }
      if (parent.closest("button, input, select, textarea, script, style, mark, .erp-info")) { return; }
      pattern.lastIndex = 0;
      if (!pattern.test(node.nodeValue)) { return; }
      var fragment = document.createDocumentFragment();
      var parts = node.nodeValue.split(pattern);
      parts.forEach(function (part, index) {
        if (!part) { return; }
        if (index % 2 === 1) {
          var mark = document.createElement("mark");
          mark.className = "erp-hl";
          mark.textContent = part;
          fragment.appendChild(mark);
        } else {
          fragment.appendChild(document.createTextNode(part));
        }
      });
      parent.replaceChild(fragment, node);
    });
  }

  /* ---- live search -------------------------------------------------------------------------- */
  function start() {
    var input = findInput();
    if (!input) { return; }
    var hint = document.querySelector("[data-erp-search-hint]");
    if (hint) { input.setAttribute("placeholder", hint.getAttribute("data-erp-search-hint")); }
    highlight(input);

    // Live search only when the search box sits inside the part of the page that is refreshed.
    var inside = REGIONS.filter(function (s) { var el = document.querySelector(s); return el && el.contains(input); })[0];
    if (!inside) { return; }

    var timer = null;
    var controller = null;
    var sequence = 0;

    function targetUrl() {
      var url = new URL(window.location.href);
      url.searchParams.delete("p");
      var value = input.value.trim();
      if (value) { url.searchParams.set("q", value); } else { url.searchParams.delete("q"); }
      return url;
    }

    function swap(doc) {
      var selector = REGIONS.filter(function (s) { return document.querySelector(s) && doc.querySelector(s); })[0];
      if (!selector) { throw new Error("no region"); }
      var live = document.querySelector(selector);
      var fresh = doc.querySelector(selector);
      var liveForm = input.closest("form");
      var freshInput = fresh.querySelector('input[name="q"]');
      var freshForm = freshInput ? freshInput.closest("form") : null;
      if (!liveForm || !freshForm || !live.contains(liveForm)) { throw new Error("no form"); }
      var caret = input.selectionStart;
      freshForm.replaceWith(liveForm);  // keep the box you are typing in
      live.replaceWith(fresh);
      if (document.activeElement !== input) {
        input.focus();
        try { input.setSelectionRange(caret, caret); } catch (e) { /* ignore */ }
      }
    }

    function run() {
      var url = targetUrl();
      var mine = ++sequence;
      if (controller) { controller.abort(); }
      controller = typeof AbortController === "function" ? new AbortController() : null;
      var region = document.querySelector(REGIONS.filter(function (s) { return document.querySelector(s); })[0] || "main");
      if (region) { region.classList.add("erp-loading"); }
      fetch(url.toString(), { credentials: "same-origin", signal: controller ? controller.signal : undefined })
        .then(function (response) {
          if (!response.ok || new URL(response.url).pathname !== window.location.pathname) { throw new Error("not a list"); }
          return response.text();
        })
        .then(function (html) {
          if (mine !== sequence) { return; }
          swap(new DOMParser().parseFromString(html, "text/html"));
          window.history.replaceState(null, "", url.toString());
          highlight(input);
        })
        .catch(function (error) {
          if (error && error.name === "AbortError") { return; }
          if (mine !== sequence) { return; }
          if (region) { region.classList.remove("erp-loading"); }
          window.__erpAllowLeave = true;
          window.location.href = url.toString();  // fall back to a normal page load
        });
    }

    var last = input.value.trim();
    input.addEventListener("input", function (e) {
      if (e.isComposing) { return; }
      var value = input.value.trim();
      if (value === last) { return; }
      if (value.length === 1) { return; }  // wait for two letters
      last = value;
      clearTimeout(timer);
      timer = setTimeout(run, 350);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        clearTimeout(timer);
        last = input.value.trim();
        run();
      }
    });
  }

  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", start); } else { start(); }
})();
