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


  /* ---- list chrome: column headings that stay in view, a "back to top" pill, and a "go to page" box ------
     These work on any change list. The headings are a copy of the table's heading row, shown fixed at the
     top of the screen while the real one has scrolled away (this works whichever part of the page scrolls
     and however wide the table is). */
  var chrome = { per: null, table: null, bar: null, inner: null, pill: null, scroller: null, queued: false, wrapper: null };

  function pageTop(scroller) {
    if (scroller && scroller !== document && scroller !== document.documentElement && scroller !== document.body) { return scroller.scrollTop || 0; }
    return window.pageYOffset || document.documentElement.scrollTop || 0;
  }

  // How far down the screen a bar that stays at the top (Unfold's page header) reaches.
  function topOffset() {
    var offset = 0;
    var stack = document.elementsFromPoint ? document.elementsFromPoint(Math.floor(window.innerWidth * 0.6), 3) : [];
    stack.forEach(function (node) {
      if (chrome.bar && chrome.bar.contains(node)) { return; }
      if (node === chrome.pill) { return; }
      var style = window.getComputedStyle(node);
      if (style.position !== "fixed" && style.position !== "sticky") { return; }
      var rect = node.getBoundingClientRect();
      if (rect.top <= 3 && rect.height < 200 && rect.bottom > offset) { offset = rect.bottom; }
    });
    return offset;
  }

  function backgroundOf(node) {
    for (var el = node; el && el.nodeType === 1; el = el.parentNode) {
      var color = window.getComputedStyle(el).backgroundColor;
      if (color && color !== "transparent" && !/rgba\(\s*\d+,\s*\d+,\s*\d+,\s*0\s*\)/.test(color)) { return color; }
    }
    return "";
  }

  function horizontalScroller(node) {
    for (var el = node.parentNode; el && el.nodeType === 1 && el !== document.body; el = el.parentNode) {
      var overflow = window.getComputedStyle(el).overflowX;
      if (overflow === "auto" || overflow === "scroll") { return el; }
    }
    return null;
  }

  function dropHeadingCopy() {
    if (chrome.bar && chrome.bar.parentNode) { chrome.bar.parentNode.removeChild(chrome.bar); }
    chrome.bar = chrome.inner = chrome.table = chrome.wrapper = null;
  }

  function buildHeadingCopy() {
    dropHeadingCopy();
    var table = document.getElementById("result_list");
    if (!table || !table.tHead || !table.tHead.rows.length) { return; }
    var copy = document.createElement("table");
    copy.className = table.className;
    var head = table.tHead.cloneNode(true);
    copy.appendChild(head);
    Array.prototype.forEach.call(copy.querySelectorAll("*"), function (node) {
      node.removeAttribute("id");
      Array.prototype.slice.call(node.attributes).forEach(function (attribute) {
        // the copy is only for looking at and clicking: it carries none of the page's script hooks
        if (/^(x-|@|:|data-erp-)/.test(attribute.name)) { node.removeAttribute(attribute.name); }
      });
    });
    var inner = document.createElement("div");
    inner.className = "erp-sticky-inner";
    inner.appendChild(copy);
    var bar = document.createElement("div");
    bar.className = "erp-sticky";
    bar.hidden = true;
    bar.appendChild(inner);
    // the "select all" box of the copy works the real one
    var boxes = copy.querySelectorAll('input[type="checkbox"]');
    var real = table.tHead.querySelector('input[type="checkbox"]');
    if (boxes.length && real) {
      boxes[0].addEventListener("click", function (e) {
        e.preventDefault();
        real.click();
        // (the browser puts the clicked box back after a cancelled click, so copy the real state a moment later)
        window.setTimeout(function () { boxes[0].checked = real.checked; }, 0);
      });
      real.addEventListener("change", function () { boxes[0].checked = real.checked; });
    }
    document.body.appendChild(bar);
    chrome.bar = bar;
    chrome.inner = inner;
    chrome.table = table;
    chrome.wrapper = horizontalScroller(table);
    if (chrome.wrapper) { chrome.wrapper.addEventListener("scroll", function () { if (chrome.inner) { chrome.inner.scrollLeft = chrome.wrapper.scrollLeft; } }); }
    update();
  }

  function update() {
    chrome.queued = false;
    var scrolled = pageTop(chrome.scroller);
    if (chrome.pill) { chrome.pill.hidden = scrolled < 500; }
    document.body.classList.toggle("erp-scrolled", scrolled >= 500);
    var bar = chrome.bar, table = chrome.table;
    if (!bar || !table || !table.isConnected) { if (bar) { bar.hidden = true; } return; }
    var rect = table.getBoundingClientRect();
    var headHeight = table.tHead.offsetHeight;
    var offset = topOffset();
    var show = rect.top + headHeight < offset + 1 && rect.bottom > offset + headHeight + 8;
    if (!show) { bar.hidden = true; return; }
    var box = (chrome.wrapper || table).getBoundingClientRect();
    var width = chrome.wrapper ? chrome.wrapper.clientWidth : table.offsetWidth;
    bar.style.top = offset + "px";
    bar.style.left = box.left + "px";
    bar.style.width = width + "px";
    var background = backgroundOf(table.parentNode);
    if (background) { bar.style.backgroundColor = background; }
    var copy = bar.firstChild.firstChild;
    copy.style.width = table.offsetWidth + "px";
    copy.style.tableLayout = "fixed";
    var real = table.tHead.rows[0].cells, mine = copy.tHead.rows[0].cells;
    for (var i = 0; i < real.length && i < mine.length; i++) {
      var w = real[i].getBoundingClientRect().width + "px";
      mine[i].style.width = w;
      mine[i].style.minWidth = w;
      mine[i].style.maxWidth = w;
      mine[i].style.boxSizing = "border-box";
    }
    bar.hidden = false;
    chrome.inner.scrollLeft = chrome.wrapper ? chrome.wrapper.scrollLeft : 0;
  }

  function queueUpdate(e) {
    // the left menu scrolling on its own is not the page scrolling: no "Back to Top" for it
    if (e && e.type === "scroll" && e.target && e.target.closest && e.target.closest("#nav-sidebar, aside, nav")) { return; }
    if (e && e.type === "scroll" && e.target) {
      // remember which part of the page scrolls up and down (a table scrolling sideways does not count)
      if (e.target === document) { chrome.scroller = document.scrollingElement; }
      else if (e.target.scrollTop > 0) { chrome.scroller = e.target; }
    }
    if (chrome.queued) { return; }
    chrome.queued = true;
    window.requestAnimationFrame(update);
  }

  function buildPill() {
    if (chrome.pill) { return; }
    var pill = document.createElement("button");
    pill.type = "button";
    pill.className = "erp-totop";
    pill.hidden = true;
    pill.setAttribute("aria-label", "Back to the top of the page");
    pill.innerHTML = '<svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M10 16V4M4.5 9.5 10 4l5.5 5.5"/></svg><span>Back to Top</span>';
    pill.addEventListener("click", function () {
      var scroller = chrome.scroller;
      if (scroller && scroller !== document.scrollingElement && scroller !== document.documentElement && scroller !== document.body) {
        scroller.scrollTo({ top: 0, behavior: "smooth" });
      }
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    document.body.appendChild(pill);
    chrome.pill = pill;
  }

  /* "Page 3 of 122 [Go]": the page box is hidden in the summary line and shown in the row with the page numbers
     under the table ("1 2 3 ... 123 - 12230 Tenant payments"), found by its page links. */
  var PAGE_LINK = /[?&]p=\d+(&|$)/;

  function findPagerRow(table) {
    var links = Array.prototype.filter.call(document.querySelectorAll("a[href]"), function (a) {
      return PAGE_LINK.test(a.getAttribute("href")) && !table.contains(a) && (table.compareDocumentPosition(a) & 4) &&
        !a.closest(".erp-chips, .erp-sticky, .erp-fbar, .erp-summary, .erp-jump");
    });
    if (!links.length) { return null; }
    var row = links[0].parentElement;
    // the row with the numbers also says what is being counted ("... Tenant payments"): go up until it does
    while (row && !/[A-Za-z]/.test(row.textContent) && row.parentElement && !row.parentElement.contains(table)) { row = row.parentElement; }
    return row;
  }

  /* A list with one page has no page numbers, only a line such as "56 owners": the boxes go into the row that holds it. */
  function findCountRow(table) {
    var found = Array.prototype.filter.call(document.querySelectorAll("p, div, span, li"), function (n) {
      return (!table || (!table.contains(n) && (table.compareDocumentPosition(n) & 4))) && !n.children.length &&
        !n.closest(".erp-chips, .erp-sticky, .erp-fbar, .erp-summary, .erp-jump, .erp-pagerline, .erp-totop, .erp-footer-bar, [role=dialog]") &&
        /^[\d,]+\s+[^\d]{2,40}$/.test(n.textContent.trim());
    });
    // with no table (an empty list) the count at the bottom is the last one on the page, the top one is in the header
    var hit = table ? found[0] : found[found.length - 1];
    return hit ? hit.parentElement : null;
  }

  function addPageBox() {
    Array.prototype.forEach.call(document.querySelectorAll(".erp-jump-bottom, .erp-jump-line"), function (node) {
      if (node.parentNode) { node.parentNode.removeChild(node); }
    });
    var table = document.getElementById("result_list");
    var top = document.querySelector(".erp-jump:not(.erp-jump-bottom):not([data-static])");
    var row = (table && findPagerRow(table)) || findCountRow(table);
    // "Rows per page" moves down next to the page numbers (a fresh one comes with every refreshed list)
    var per = document.querySelector("[data-erp-per]:not(.erp-per-moved)") || (chrome.per && !document.body.contains(chrome.per) ? chrome.per : null);
    if (row && !per) { per = row.querySelector(".erp-per-moved"); }
    var copy = null;
    if (top) {
      copy = top.cloneNode(true);
      copy.removeAttribute("hidden");
      copy.classList.add("erp-jump-bottom");
    }
    if (row) {
      var flex = /flex/.test(window.getComputedStyle(row).display);
      var mode = flex ? "is-flex" : "is-float";
      if (copy) { copy.classList.add(mode); }
      if (per) {
        var holder = per.parentElement;
        per.classList.add("erp-per-moved", mode);
        chrome.per = per;
        if (copy) { copy.classList.add("after-per"); }
        // in a floated row the first box added sits furthest right, so the page box goes first
        if (flex) { row.appendChild(per); if (copy) { row.appendChild(copy); } }
        else { if (copy) { row.appendChild(copy); } row.appendChild(per); }
        if (holder && holder !== row && holder.classList.contains("erp-fbar") && !holder.children.length) {
          holder.parentNode.removeChild(holder);
        }
      } else if (copy) {
        row.appendChild(copy);
      }
      return;
    }
    if (!copy && !per) { return; }
    if (!table) {
      // an empty list with no bottom row to join: there is nothing to page through, so leave the box out of the top bar
      if (per && !per.classList.contains("erp-per-moved") && per.parentNode) {
        var bar = per.parentElement;
        bar.removeChild(per);
        if (bar.classList.contains("erp-fbar") && !bar.children.length && bar.parentNode) { bar.parentNode.removeChild(bar); }
      }
      return;
    }
    // no row with page numbers found: put the boxes on a line of their own under the table
    var form = table.closest("form");
    var anchor = form || table.parentNode;
    var line = document.createElement("div");
    line.className = "erp-jump-line erp-pagerline";
    if (per) {
      per.classList.add("erp-per-moved", "is-flex");
      chrome.per = per;
      line.appendChild(per);
      if (copy) { copy.classList.add("is-flex", "after-per"); }
    }
    if (copy) { line.appendChild(copy); }
    if (anchor.parentNode) { anchor.parentNode.insertBefore(line, anchor.nextSibling); }
  }

  /* The search box says what it can find through a small "i" next to it, not through a line of text. */
  function addSearchInfo(input, text) {
    if (!window.erpMakeInfo) { return; }
    var box = input.parentElement;
    while (box && box !== document.body && parseFloat(window.getComputedStyle(box).borderTopWidth) < 1) { box = box.parentElement; }
    if (!box || box === document.body) { box = input.parentElement; }
    if (box.querySelector(".erp-searchinfo")) { return; }
    if (window.getComputedStyle(box).position === "static") { box.style.position = "relative"; }
    var icon = window.erpMakeInfo(text);
    if (!icon) { return; }
    icon.classList.add("erp-searchinfo");
    // leave the "/" key hint of the box where it is: the icon goes just to its left
    var chip = Array.prototype.filter.call(box.querySelectorAll("*"), function (n) { return n.children.length === 0 && n.textContent.trim() === "/"; })[0];
    var right = 14;
    if (chip) { right = Math.round(box.getBoundingClientRect().right - chip.getBoundingClientRect().left + 8); }
    icon.style.right = right + "px";
    box.appendChild(icon);
    input.style.paddingRight = (right + 28) + "px";
  }

  function goToPage(box) {
    var input = box.querySelector(".erp-jump-in");
    var pages = parseInt(box.getAttribute("data-pages"), 10) || 1;
    var wanted = parseInt(input.value, 10);
    if (isNaN(wanted)) { input.focus(); input.select(); return; }
    wanted = Math.min(Math.max(wanted, 1), pages);
    input.value = wanted;
    window.__erpAllowLeave = true;
    window.location.href = box.getAttribute("data-url").replace("__P__", String(wanted));
  }

  document.addEventListener("click", function (e) {
    var go = e.target.closest ? e.target.closest(".erp-jump-go") : null;
    if (go) { e.preventDefault(); goToPage(go.closest(".erp-jump")); }
  });
  document.addEventListener("keydown", function (e) {
    var input = e.target && e.target.classList && e.target.classList.contains("erp-jump-in") ? e.target : null;
    if (input && e.key === "Enter") { e.preventDefault(); goToPage(input.closest(".erp-jump")); }
  });
  document.addEventListener("focusin", function (e) {
    if (e.target && e.target.classList && e.target.classList.contains("erp-jump-in")) { e.target.select(); }
  });

  /* The whole "Rows per page" box opens its list, not only the number and the arrow. */
  document.addEventListener("click", function (e) {
    var pill = e.target.closest ? e.target.closest(".erp-fsel") : null;
    if (!pill || e.target.closest(".erp-select")) { return; }
    var trigger = pill.querySelector(".erp-select-trigger");
    if (trigger) { e.preventDefault(); trigger.click(); }
  });

  function setUpChrome() {
    buildPill();
    buildHeadingCopy();
    addPageBox();
    update();
  }
  window.addEventListener("scroll", queueUpdate, true);
  window.addEventListener("resize", queueUpdate);
  window.erpListChrome = setUpChrome;  // called again after the list is refreshed by live search

  /* ---- live search -------------------------------------------------------------------------- */
  function start() {
    setUpChrome();
    var input = findInput();
    if (!input) { return; }
    var hint = document.querySelector("[data-erp-search-hint]");
    if (hint) {
      input.setAttribute("placeholder", "Search\u2026");
      addSearchInfo(input, "You can search by: " + hint.getAttribute("data-erp-search-hint"));
    }
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
      url.searchParams.delete("page");  // (custom list pages call their page number "page")
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
      // the strip at the bottom (count, page numbers) is outside the refreshed part: refresh it too
      var liveFoot = document.querySelector("#main > .z-40:last-child");
      var freshFoot = doc.querySelector("#main > .z-40:last-child");
      if (liveFoot && freshFoot) { liveFoot.replaceWith(freshFoot); }
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
          setUpChrome();
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
