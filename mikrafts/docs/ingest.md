# MiKrafts catalog ingest (stub)

This package does **not** read Gmail. A later pipeline owns the mailbox.
This file is the contract that pipeline should implement, plus the local
function it should call.

## Email contract

Inbound mail is a catalog candidate when **all** of the following are true:

1. **Subject** contains `new print` (case-insensitive). Examples that match:
   `New print`, `NEW PRINT — bracket`, `Re: new print`.
2. **One photo** is attached (JPEG, PNG, or WebP). The attachment is the print.
3. **Body is optional.** If present:
   - first non-empty line → **title**
   - remaining lines → **notes**

Helpers in `ingest.py`:

- `subject_is_new_print(subject) -> bool`
- `parse_email_body(body) -> (title, note)`
- `ingest_print(image_path, title, note="")` → processed JPEG + JSON row

## Adding an item by hand (git-friendly)

1. Drop the source photo anywhere local.
2. Run:

   ```bash
   python3 mikrafts/ingest.py --image /path/to/photo.jpg --title "Title" --note "Optional note"
   ```

3. Commit `mikrafts/catalog/items.json` and the new file under
   `mikrafts/catalog/images/`.

The processor crops toward 4:3, lifts contrast, and biases a clean background
toward **white** or **plum**. It does not draw a nozzle, cubes, or other
geometry onto the print.

## Live catalog

`mikrafts/catalog/items.json` ships as `[]`. Do not seed dummy prints.

An **example** card exists only in `docs/design.md` and `tests/fixtures/`.
It is not a live catalog row.

Site notes are a separate docs-only contract (`docs/feedback.md`): Mike
emails `cvolkern@gmail.com` with subject containing `mikrafts feedback`.
Grok parses, implements, and replies. Not a site UI and not this ingest path.
