"""Per-seat scopes. write:* does not include delete."""

from __future__ import annotations

from dataclasses import dataclass

# Alexandra cannot create parties or delete. Buzz writes interactions and lead status.
SEAT_SCOPES: dict[str, tuple[str, ...]] = {
    "grok": (
        "read:*",
        "write:party",
        "write:interaction",
        "write:note",
        "write:task",
        "write:status",
        "write:suppression",
        "merge",
    ),
    "alexandra": (
        "read:party",
        "read:vehicle",
        "write:interaction",
        "write:note",
        "write:task",
        "write:lead-status",
    ),
    "buzz": ("read:*", "write:interaction", "write:status"),
    "roadside-pipeline": (
        "read:*",
        "write:party",
        "write:interaction",
        "write:note",
        "write:task",
        "write:status",
        "write:suppression",
    ),
    "chris": ("read:*", "write:*", "delete", "merge"),
}


@dataclass(frozen=True)
class Actor:
    seat: str
    cred_id: str
    scopes: frozenset[str]


class CrmError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def require(actor: Actor | None, scope: str) -> None:
    if actor is None:
        raise CrmError(401, "unauthorized")
    held = actor.scopes
    if scope in held:
        return
    if scope.startswith("read:") and "read:*" in held:
        return
    if scope.startswith("write:") and "write:*" in held:
        return
    raise CrmError(403, "forbidden")
