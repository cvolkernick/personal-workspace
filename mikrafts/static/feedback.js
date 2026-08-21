(function () {
  var FEEDBACK_TO = "cvolkern@gmail.com";
  var FEEDBACK_SUBJECT = "mikrafts feedback";
  var FEEDBACK_REPLY_HINT = "MiKraftsLLC@gmail.com";

  function subjectIsMikraftsFeedback(subject) {
    return String(subject || "").toLowerCase().indexOf(FEEDBACK_SUBJECT) !== -1;
  }

  function buildFeedbackMailto(message, name, replyTo) {
    var text = String(message || "").trim();
    if (!text) {
      throw new Error("message is required");
    }
    var reply = String(replyTo || "").trim() || FEEDBACK_REPLY_HINT;
    var lines = [];
    if (String(name || "").trim()) {
      lines.push("Name: " + String(name).trim());
    }
    lines.push("Reply-to: " + reply);
    lines.push("");
    lines.push(text);
    return (
      "mailto:" +
      FEEDBACK_TO +
      "?subject=" +
      encodeURIComponent(FEEDBACK_SUBJECT) +
      "&body=" +
      encodeURIComponent(lines.join("\n"))
    );
  }

  function init(form) {
    var root = form || document.getElementById("feedback-form");
    if (!root) {
      return;
    }
    var status = document.getElementById("feedback-status");
    root.addEventListener("submit", function (event) {
      event.preventDefault();
      var name = (root.elements.name && root.elements.name.value) || "";
      var email = (root.elements.email && root.elements.email.value) || "";
      var message = (root.elements.message && root.elements.message.value) || "";
      if (!String(message).trim()) {
        if (status) {
          status.textContent = "Add a message before opening email.";
          status.dataset.state = "error";
        }
        return;
      }
      var href = buildFeedbackMailto(message, name, email);
      if (status) {
        status.textContent =
          "Opening your email app. If nothing opens, send the message to cvolkern@gmail.com with subject mikrafts feedback.";
        status.dataset.state = "open-mail";
      }
      window.location.href = href;
    });
  }

  window.MiKraftsFeedback = {
    FEEDBACK_TO: FEEDBACK_TO,
    FEEDBACK_SUBJECT: FEEDBACK_SUBJECT,
    subjectIsMikraftsFeedback: subjectIsMikraftsFeedback,
    buildFeedbackMailto: buildFeedbackMailto,
    init: init,
  };

  if (document.getElementById("feedback-form")) {
    init();
  }
})();
