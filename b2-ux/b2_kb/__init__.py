"""B2 knowledge-base library: vault I/O, search, Ask Grok."""

from .vault import (
    DEFAULT_VAULT_PATH,
    Note,
    build_graph,
    extract_wikilinks,
    index_vault,
    list_notes,
    read_note,
    retrieve,
    search_notes,
)
from .ask import ask_grok, build_ask_context, offline_grounded_answer

__all__ = [
    "DEFAULT_VAULT_PATH",
    "Note",
    "ask_grok",
    "build_ask_context",
    "build_graph",
    "extract_wikilinks",
    "index_vault",
    "list_notes",
    "offline_grounded_answer",
    "read_note",
    "retrieve",
    "search_notes",
]
