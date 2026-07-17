/* ============================================================
   URBAN THREAD — shared JavaScript (all 5 pages)
   CSC 106 / IFT 203 TMA — MIVA Open University

   Shared features (every page):
     - Mobile nav toggle
     - Highlight the current page's nav link
     - Auto-fill the footer year
     - Stop the <marquee> for users who prefer reduced motion

   Page-specific features (chosen by <body data-page="...">):
     - home:      recursive DOM-tree visualiser (DOM Structure deliverable)
     - products:  live category filter + text search
     - trustees:  expandable bios
     - inquiries: form validation + confirmation message
     - events:    live countdown to the next event
   ============================================================ */

document.addEventListener("DOMContentLoaded", function () {
  initSharedFeatures();

  // Run only the feature that belongs to the current page
  var page = document.body.dataset.page;
  if (page === "home") initDomViewer();
  if (page === "products") initProductFilter();
  if (page === "trustees") initTrusteeBios();
  if (page === "inquiries") initInquiryForm();
  if (page === "events") initEventCountdown();
});

/* ---------- Shared: nav toggle, active link, year, marquee ---------- */
function initSharedFeatures() {
  // Mobile menu toggle
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? "Close" : "Menu";
    });
  }

  // Mark the link that points at the current page, so the same
  // HTML nav can be reused everywhere and still show location.
  var current = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".site-nav a").forEach(function (link) {
    if (link.getAttribute("href") === current) {
      link.setAttribute("aria-current", "page");
    }
  });

  // Footer year
  var year = document.getElementById("year");
  if (year) year.textContent = new Date().getFullYear();

  // Respect prefers-reduced-motion: freeze the marquee ticker
  var marquee = document.querySelector("marquee");
  if (marquee && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    if (typeof marquee.stop === "function") marquee.stop();
  }
}

/* ---------- Home: DOM Structure visualiser ----------
   Recursively walks the live DOM starting at <html> and prints
   each element as an indented line: tagname + #id + .classes.   */
function initDomViewer() {
  var button = document.getElementById("dom-btn");
  var output = document.getElementById("dom-output");
  if (!button || !output) return;

  function describe(el) {
    var text = el.tagName.toLowerCase();
    if (el.id) text += "#" + el.id;
    if (el.classList.length) text += "." + Array.from(el.classList).join(".");
    return text;
  }

  // The recursive walk: one line per element, indented by depth
  function walk(el, depth) {
    var lines = ["  ".repeat(depth) + describe(el)];
    Array.from(el.children).forEach(function (child) {
      lines = lines.concat(walk(child, depth + 1));
    });
    return lines;
  }

  button.addEventListener("click", function () {
    var showing = !output.hidden;
    if (showing) {
      output.hidden = true;
      button.textContent = "Show DOM structure";
      button.setAttribute("aria-expanded", "false");
    } else {
      // Walk the live document each time, so the tree is always current
      output.textContent = walk(document.documentElement, 0).join("\n");
      output.hidden = false;
      button.textContent = "Hide DOM structure";
      button.setAttribute("aria-expanded", "true");
    }
  });
}

/* ---------- Products: category filter + search ---------- */
function initProductFilter() {
  var buttons = document.querySelectorAll(".filter-btn");
  var search = document.getElementById("product-search");
  var cards = document.querySelectorAll(".product-card");
  var count = document.getElementById("product-count");
  var empty = document.getElementById("no-results");
  var activeCategory = "all";

  function applyFilters() {
    var term = (search.value || "").trim().toLowerCase();
    var visible = 0;

    cards.forEach(function (card) {
      var matchesCategory =
        activeCategory === "all" || card.dataset.category === activeCategory;
      var matchesSearch =
        card.dataset.name.toLowerCase().indexOf(term) !== -1;

      var show = matchesCategory && matchesSearch;
      card.hidden = !show;
      if (show) visible++;
    });

    count.textContent = visible + " of " + cards.length + " pieces showing";
    empty.hidden = visible !== 0;
  }

  buttons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      buttons.forEach(function (b) { b.classList.remove("is-active"); });
      btn.classList.add("is-active");
      activeCategory = btn.dataset.filter;
      applyFilters();
    });
  });

  search.addEventListener("input", applyFilters);
  applyFilters();
}

/* ---------- Trustees: expandable bios ---------- */
function initTrusteeBios() {
  document.querySelectorAll(".bio-toggle").forEach(function (button) {
    var bio = button.parentElement.querySelector(".bio");
    button.addEventListener("click", function () {
      var open = bio.hidden;
      bio.hidden = !open;
      button.setAttribute("aria-expanded", String(open));
      button.textContent = open ? "Hide bio" : "Read bio";
    });
  });
}

/* ---------- Inquiries: validation + confirmation ---------- */
function initInquiryForm() {
  var form = document.getElementById("inquiry-form");
  var errorBox = document.getElementById("form-error");
  var confirmPanel = document.getElementById("form-confirm");
  var confirmText = document.getElementById("confirm-text");
  var resetBtn = document.getElementById("form-reset");
  var dateInput = document.getElementById("inq-date");

  // Block past dates: set min to today (yyyy-mm-dd)
  dateInput.min = new Date().toISOString().split("T")[0];

  form.addEventListener("submit", function (event) {
    event.preventDefault(); // front-end only — no backend to post to

    var problems = [];
    form.querySelectorAll(".field-error").forEach(function (el) {
      el.classList.remove("field-error");
    });

    var name = form.name.value.trim();
    var email = form.email.value.trim();
    var phone = form.phone.value.trim();
    var date = form.date.value;
    var message = form.message.value.trim();

    function flag(input, text) {
      input.classList.add("field-error");
      problems.push(text);
    }

    if (name === "") flag(form.name, "Full name is required.");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) flag(form.email, "Enter a valid email address.");
    if (!/^[0-9+()\s-]{7,}$/.test(phone)) flag(form.phone, "Enter a valid phone number (digits only, at least 7).");
    if (date === "") flag(dateInput, "Pick a preferred date.");
    if (message.length < 10) flag(form.message, "Message must be at least 10 characters.");

    if (problems.length > 0) {
      errorBox.textContent = "Hold up — " + problems.join(" ");
      errorBox.hidden = false;
      return;
    }

    // Valid: hide the form and show the confirmation summary
    errorBox.hidden = true;
    var reason = form.reason.value;
    var prettyDate = new Date(date + "T00:00:00").toDateString();
    confirmText.textContent =
      "Thanks " + name + " — your \"" + reason + "\" request for " + prettyDate +
      " is in. We'll reply to " + email + " within two working days.";
    form.hidden = true;
    confirmPanel.hidden = false;
    confirmPanel.focus && confirmPanel.setAttribute("tabindex", "-1");
    confirmPanel.focus();
  });

  // Let the user start over
  resetBtn.addEventListener("click", function () {
    form.reset();
    form.hidden = false;
    confirmPanel.hidden = true;
  });
}

/* ---------- Events: countdown to the next event ---------- */
function initEventCountdown() {
  var display = document.getElementById("countdown");
  var cards = document.querySelectorAll(".event-card");
  if (!display || cards.length === 0) return;

  // Find the soonest event that is still in the future
  function nextEvent() {
    var soonest = null;
    var now = Date.now();
    cards.forEach(function (card) {
      var when = new Date(card.dataset.date).getTime();
      if (when > now && (soonest === null || when < soonest.when)) {
        soonest = { when: when, title: card.dataset.title };
      }
    });
    return soonest;
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function tick() {
    var target = nextEvent();
    if (!target) {
      display.textContent = "No upcoming events — new dates drop soon.";
      clearInterval(timer);
      return;
    }
    var diff = target.when - Date.now();
    var days = Math.floor(diff / 86400000);
    var hours = Math.floor((diff % 86400000) / 3600000);
    var mins = Math.floor((diff % 3600000) / 60000);
    var secs = Math.floor((diff % 60000) / 1000);
    display.textContent =
      target.title + " — " + days + "d " + pad(hours) + "h " +
      pad(mins) + "m " + pad(secs) + "s";
  }

  tick();
  var timer = setInterval(tick, 1000);
}
