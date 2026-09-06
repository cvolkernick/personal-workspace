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

  function setInvalid(el, invalid) {
    if (!el) return;
    el.classList.toggle("invalid", !!invalid);
  }

  function isValidEmail(value) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  }

  function bindForm(formId, statusId, successMessage, extraValidate) {
    var form = document.getElementById(formId);
    var status = document.getElementById(statusId);
    if (!form || !status) return;

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      status.textContent = "";
      status.className = "form-status";

      var name = form.elements.namedItem("name");
      var email = form.elements.namedItem("email");
      var select = form.elements.namedItem("interest") || form.elements.namedItem("role");
      var message = form.elements.namedItem("message");

      var fields = [name, email, select, message];
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
      if (!select || !String(select.value || "").trim()) {
        setInvalid(select, true);
        errors.push("Please select an option.");
      }
      if (!message || !String(message.value || "").trim()) {
        setInvalid(message, true);
        errors.push("Message is required.");
      }

      if (typeof extraValidate === "function") {
        extraValidate(form, errors, setInvalid);
      }

      if (errors.length) {
        status.textContent = errors[0];
        status.classList.add("err");
        return;
      }

      status.textContent = successMessage;
      status.classList.add("ok");
      form.reset();
    });
  }

  bindForm(
    "contact-form",
    "form-status",
    "Thanks — your inquiry was recorded locally. We will follow up by email."
  );

  bindForm(
    "interest-form",
    "interest-form-status",
    "Thanks — your interest was recorded locally on this demo. This is not an investment, reservation, or Tesla order.",
    function (form, errors, markInvalid) {
      var ack = form.elements.namedItem("ack_demo");
      var role = form.elements.namedItem("role");
      var accredited = form.elements.namedItem("accredited");
      if (!ack || !ack.checked) {
        markInvalid(ack, true);
        errors.push("Please confirm you understand this is a demo, not an offer to sell securities.");
      }
      if (role && role.value === "capital" && (!accredited || !accredited.checked)) {
        markInvalid(accredited, true);
        errors.push("Capital-partner interest on this demo requires accredited-investor self-attestation.");
      }
    }
  );
})();
