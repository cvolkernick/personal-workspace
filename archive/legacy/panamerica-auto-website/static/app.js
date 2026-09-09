(function () {
  "use strict";

  var yearEl = document.getElementById("year");
  if (yearEl) {
    yearEl.textContent = String(new Date().getFullYear());
  }

  var toggle = document.getElementById("nav-toggle");
  var nav = document.getElementById("site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    nav.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        nav.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  var form = document.getElementById("contact-form");
  var status = document.getElementById("form-status");
  if (!form || !status) return;

  function setInvalid(el, invalid) {
    if (!el) return;
    el.classList.toggle("invalid", !!invalid);
  }

  function isValidEmail(value) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    status.textContent = "";
    status.className = "form-status";

    var name = form.elements.namedItem("name");
    var email = form.elements.namedItem("email");
    var interest = form.elements.namedItem("interest");
    var message = form.elements.namedItem("message");

    var fields = [name, email, interest, message];
    fields.forEach(function (f) {
      setInvalid(f, false);
    });

    var errors = [];
    if (!name || !String(name.value || "").trim()) {
      setInvalid(name, true);
      errors.push("Name is required.");
    }
    if (!email || !isValidEmail(String(email.value || "").trim())) {
      setInvalid(email, true);
      errors.push("A valid email is required.");
    }
    if (!interest || !String(interest.value || "").trim()) {
      setInvalid(interest, true);
      errors.push("Please select an interest.");
    }
    if (!message || !String(message.value || "").trim()) {
      setInvalid(message, true);
      errors.push("Message is required.");
    }

    if (errors.length) {
      status.textContent = errors[0];
      status.classList.add("err");
      return;
    }

    // MVP: client-side only confirmation (no backend yet)
    status.textContent =
      "Thanks — your inquiry was recorded locally. We will follow up by email.";
    status.classList.add("ok");
    form.reset();
  });
})();
