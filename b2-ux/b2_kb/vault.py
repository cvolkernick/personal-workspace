"""Pure vault I/O: index Markdown, search, retrieve snippets, parse wikilinks."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# Default vault: ~/B2 if present (often a symlink), else personal-workspace/brain2.
# UX package is b2-ux/ (not b2/) to avoid case-insensitive collision with vault names.
_PKG_ROOT = Path(__file__).resolve().parent.parent  # .../b2-ux
_REPO_VAULT = _PKG_ROOT.parent / "brain2"
_HOME_VAULT = Path.home() / "B2"


def _default_vault() -> Path:
    env = (os.environ.get("B2_VAULT_PATH") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if _HOME_VAULT.exists():
        return _HOME_VAULT.expanduser().resolve()
    return _REPO_VAULT.resolve()


# Resolved once for imports/docs; prefer resolve_vault_path() at call sites.
DEFAULT_VAULT_PATH = _default_vault()

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
SKIP_DIR_NAMES = {".obsidian", ".git", ".trash", "node_modules", "__pycache__"}


@dataclass
class Note:
    """One Markdown note in the vault."""

    path: str  # relative POSIX path from vault root, e.g. domains/Foo.md
    title: str
    body: str
    wikilinks: List[str] = field(default_factory=list)

    @property
    def rel_path(self) -> str:
        return self.path


def resolve_vault_path(vault_path: Optional[os.PathLike | str] = None) -> Path:
    if vault_path is None:
        return _default_vault()
    return Path(vault_path).expanduser().resolve()


def extract_wikilinks(text: str) -> List[str]:
    """Return unique wikilink targets (note titles) in order of first appearance."""
    seen = set()
    out: List[str] = []
    for m in WIKILINK_RE.finditer(text or ""):
        target = (m.group(1) or "").strip()
        if not target or target in seen:
            continue
        seen.add(target)
        out.append(target)
    return out


def _title_from_path(rel: Path, body: str) -> str:
    # Prefer first ATX heading
    for line in (body or "").splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip() or rel.stem
    return rel.stem


def _iter_md_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            if name.lower().endswith(".md"):
                yield Path(dirpath) / name


def index_vault(vault_path: Optional[os.PathLike | str] = None) -> List[Note]:
    """Load all Markdown notes under the vault (skips .obsidian)."""
    root = resolve_vault_path(vault_path)
    notes: List[Note] = []
    if not root.is_dir():
        return notes
    for fp in sorted(_iter_md_files(root), key=lambda p: str(p).lower()):
        try:
            body = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = fp.relative_to(root).as_posix()
        title = _title_from_path(fp.relative_to(root), body)
        notes.append(
            Note(
                path=rel,
                title=title,
                body=body,
                wikilinks=extract_wikilinks(body),
            )
        )
    return notes


def list_notes(vault_path: Optional[os.PathLike | str] = None) -> List[dict]:
    """Lightweight note list for the UI/API."""
    return [
        {
            "path": n.path,
            "title": n.title,
            "wikilinks": list(n.wikilinks),
            "chars": len(n.body),
        }
        for n in index_vault(vault_path)
    ]


def read_note(
    path: str,
    vault_path: Optional[os.PathLike | str] = None,
) -> Optional[Note]:
    """Read a single note by relative path. Returns None if missing/unsafe."""
    root = resolve_vault_path(vault_path)
    rel = (path or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    if not rel.lower().endswith(".md"):
        rel = rel + ".md"
    fp = (root / rel).resolve()
    try:
        fp.relative_to(root)
    except ValueError:
        return None
    if not fp.is_file():
        # Try match by title stem across vault
        target = Path(rel).stem.lower()
        for n in index_vault(root):
            if Path(n.path).stem.lower() == target or n.title.lower() == target:
                return n
        return None
    try:
        body = fp.read_text(encoding="utf-8")
    except OSError:
        return None
    return Note(
        path=rel,
        title=_title_from_path(Path(rel), body),
        body=body,
        wikilinks=extract_wikilinks(body),
    )


def _tokenize(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9'/-]{1,}", (text or "").lower()) if len(t) > 1}


def _score_note(note: Note, query_tokens: set, query_raw: str) -> float:
    if not query_tokens and not query_raw:
        return 0.0
    title_l = note.title.lower()
    path_l = note.path.lower()
    body_l = note.body.lower()
    score = 0.0
    q = (query_raw or "").lower().strip()
    if q and q in title_l:
        score += 50.0
    if q and q in path_l:
        score += 20.0
    if q and q in body_l:
        score += 10.0
    title_tokens = _tokenize(note.title)
    body_tokens = _tokenize(note.body)
    path_tokens = _tokenize(note.path.replace("/", " ").replace(".md", " ").replace("-", " "))
    for tok in query_tokens:
        if tok in title_tokens:
            score += 12.0
        if tok in path_tokens:
            score += 4.0
        # body frequency (capped)
        if tok in body_tokens:
            count = body_l.count(tok)
            score += min(8.0, 2.0 + count * 0.5)
    return score


def _snippet(body: str, query_tokens: set, query_raw: str, radius: int = 120) -> str:
    text = body or ""
    lower = text.lower()
    q = (query_raw or "").lower().strip()
    idx = -1
    if q:
        idx = lower.find(q)
    if idx < 0:
        for tok in sorted(query_tokens, key=len, reverse=True):
            idx = lower.find(tok)
            if idx >= 0:
                break
    if idx < 0:
        snip = text[: radius * 2].strip()
        return snip + ("…" if len(text) > radius * 2 else "")
    start = max(0, idx - radius)
    end = min(len(text), idx + radius)
    snip = text[start:end].strip().replace("\n", " ")
    if start > 0:
        snip = "…" + snip
    if end < len(text):
        snip = snip + "…"
    return snip


def search_notes(
    query: str,
    vault_path: Optional[os.PathLike | str] = None,
    *,
    limit: int = 20,
    notes: Optional[Sequence[Note]] = None,
) -> List[dict]:
    """Full-text-ish search over titles, paths, and bodies."""
    q = (query or "").strip()
    if not q:
        return []
    corpus = list(notes) if notes is not None else index_vault(vault_path)
    tokens = _tokenize(q)
    scored: List[Tuple[float, Note]] = []
    for n in corpus:
        s = _score_note(n, tokens, q)
        if s > 0:
            scored.append((s, n))
    scored.sort(key=lambda x: (-x[0], x[1].path.lower()))
    results = []
    for s, n in scored[: max(1, limit)]:
        results.append(
            {
                "path": n.path,
                "title": n.title,
                "score": round(s, 2),
                "snippet": _snippet(n.body, tokens, q),
                "wikilinks": list(n.wikilinks),
            }
        )
    return results


def retrieve(
    query: str,
    vault_path: Optional[os.PathLike | str] = None,
    *,
    top_k: int = 5,
    max_chars_per_note: int = 4000,
    notes: Optional[Sequence[Note]] = None,
) -> List[dict]:
    """Top-k notes with truncated body for Ask Grok context packing."""
    hits = search_notes(query, vault_path, limit=top_k, notes=notes)
    if not hits:
        return []
    corpus = {n.path: n for n in (notes if notes is not None else index_vault(vault_path))}
    out: List[dict] = []
    for h in hits:
        n = corpus.get(h["path"])
        body = (n.body if n else "") or ""
        if len(body) > max_chars_per_note:
            body = body[:max_chars_per_note] + "\n…[truncated]"
        out.append(
            {
                "path": h["path"],
                "title": h["title"],
                "score": h["score"],
                "snippet": h["snippet"],
                "body": body,
            }
        )
    return out


def _resolve_wikilink_target(
    target: str,
    notes: Sequence[Note],
    by_title: dict,
    by_stem: dict,
) -> Optional[Note]:
    """Match a [[wikilink]] target to a vault note (title, stem, or path)."""
    t = (target or "").strip()
    if not t:
        return None
    if t in by_title:
        return by_title[t]
    # case-insensitive title
    tl = t.lower()
    for title, note in by_title.items():
        if title.lower() == tl:
            return note
    stem = Path(t).stem if t.endswith(".md") else t
    stem = stem.split("/")[-1]
    if stem in by_stem:
        return by_stem[stem]
    for s, note in by_stem.items():
        if s.lower() == stem.lower():
            return note
    # path suffix match
    for n in notes:
        if n.path == t or n.path.endswith("/" + t) or n.path == t + ".md":
            return n
        if Path(n.path).stem.lower() == stem.lower():
            return n
    return None


def build_graph(
    vault_path: Optional[os.PathLike | str] = None,
    *,
    notes: Optional[Sequence[Note]] = None,
    include_orphans: bool = True,
) -> dict:
    """
    Build an Obsidian-style knowledge graph from wikilinks.

    Returns:
      {
        "nodes": [{id, path, title, group, degree}, ...],
        "edges": [{source, target, kind}, ...],  # source/target are node ids (paths)
        "stats": {note_count, edge_count, orphan_count}
      }
    """
    corpus = list(notes) if notes is not None else index_vault(vault_path)
    by_title = {n.title: n for n in corpus}
    by_stem = {Path(n.path).stem: n for n in corpus}

    edge_set = set()  # frozenset of {a,b} for undirected uniqueness, store directed too
    edges: List[dict] = []
    degree: dict = {n.path: 0 for n in corpus}

    for n in corpus:
        for target in n.wikilinks:
            dest = _resolve_wikilink_target(target, corpus, by_title, by_stem)
            if not dest or dest.path == n.path:
                continue
            key = tuple(sorted((n.path, dest.path)))
            if key in edge_set:
                continue
            edge_set.add(key)
            edges.append(
                {
                    "source": n.path,
                    "target": dest.path,
                    "kind": "wikilink",
                }
            )
            degree[n.path] = degree.get(n.path, 0) + 1
            degree[dest.path] = degree.get(dest.path, 0) + 1

    nodes: List[dict] = []
    for n in corpus:
        deg = degree.get(n.path, 0)
        if not include_orphans and deg == 0:
            continue
        # group by top-level folder (domains, map, root)
        parts = n.path.split("/")
        group = parts[0] if len(parts) > 1 else "root"
        nodes.append(
            {
                "id": n.path,
                "path": n.path,
                "title": n.title,
                "group": group,
                "degree": deg,
            }
        )

    orphan_count = sum(1 for n in nodes if n["degree"] == 0)
    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "note_count": len(nodes),
            "edge_count": len(edges),
            "orphan_count": orphan_count,
        },
    }
