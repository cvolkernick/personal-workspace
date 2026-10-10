"""Pass the stored Daily Brief through unchanged.

The paper is private and internal-only, reachable over Tailscale. There is no
public edition and no money filter. ``scrub_edition`` returns a copy of the
stored edition and a held count of 0 so the page and ``/daily-brief/api/latest``
share one view. HTML escaping lives in the renderer, not here.
"""

from __future__ import annotations

import copy


def scrub_edition(edition: dict) -> tuple[dict, int]:
    """(stored edition, lines held back).

    Nothing is held back. The same object is rendered to HTML and returned by
    ``/daily-brief/api/latest``.
    """
    if not isinstance(edition, dict):
        return {}, 0
    return copy.deepcopy(edition), 0
