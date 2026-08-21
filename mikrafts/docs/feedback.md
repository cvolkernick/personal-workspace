# MiKrafts feedback (v1)

Mike sends site notes to Chris from the public pages. This is **not** a mail
server. No SMTP keys, Gmail tokens, or `GOOGLE_*` values belong on Vercel.

## Email contract

Same style as the `new print` ingest contract:

1. **To:** `cvolkern@gmail.com`
2. **Subject** contains `mikrafts feedback` (case-insensitive).
3. **Body:** the required message, plus optional name and reply-to.
   Reply-to defaults to `MiKraftsLLC@gmail.com` when the field is left blank.

Helpers:

- `ingest.subject_is_mikrafts_feedback(subject) -> bool`
- `ingest.build_feedback_mailto(message, name="", reply_to="") -> str`
- `static/feedback.js` builds the same `mailto:` URL and opens the mail app

Honest UX: the page says it is opening email. It never claims the message
was sent.

A later Gmail routine (not this package) can watch for that subject.
