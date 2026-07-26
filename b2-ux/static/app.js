/* B2 Brain 2 — browse, search, Ask Grok */
(() => {
  const $ = (id) => document.getElementById(id);

  const state = {
    notes: [],
    filtered: [],
    activePath: null,
    searchTimer: null,
  };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderNoteList(items) {
    const nav = $("note-list");
    nav.innerHTML = "";
    if (!items.length) {
      nav.innerHTML = `<p class="muted" style="padding:0.5rem">No notes match.</p>`;
      return;
    }
    for (const n of items) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "note-item" + (n.path === state.activePath ? " active" : "");
      btn.innerHTML = `<div class="title">${escapeHtml(n.title)}</div>
        <div class="path">${escapeHtml(n.path)}</div>`;
      btn.addEventListener("click", () => openNote(n.path));
      nav.appendChild(btn);
    }
  }

  async function loadNotes() {
    const res = await fetch("/api/notes");
    const data = await res.json();
    state.notes = data.notes || [];
    state.filtered = state.notes.slice();
    $("vault-label").textContent = data.vault_path || "B2 vault";
    $("note-count-pill").textContent = `${state.notes.length} notes`;
    renderNoteList(state.filtered);
    // Open hub by default
    const hub =
      state.notes.find((n) => /00 Home/i.test(n.path) || /hub/i.test(n.title)) ||
      state.notes[0];
    if (hub) openNote(hub.path);
  }

  async function openNote(path) {
    state.activePath = path;
    renderNoteList(state.filtered);
    $("note-title").textContent = "Loading…";
    $("note-path").textContent = path;
    const res = await fetch("/api/note?path=" + encodeURIComponent(path));
    const data = await res.json();
    if (!res.ok) {
      $("note-title").textContent = "Not found";
      $("note-body").innerHTML = `<p class="muted">${escapeHtml(data.error || "error")}</p>`;
      return;
    }
    $("note-title").textContent = data.title || path;
    $("note-path").textContent = data.path;
    const body = data.body || "";
    if (window.marked) {
      $("note-body").innerHTML = marked.parse(body);
      // Wikilink click-through: [[Title]]
      $("note-body").querySelectorAll("a").forEach((a) => {
        const href = a.getAttribute("href") || "";
        if (href.startsWith("http") || href.startsWith("/")) return;
      });
      // Convert raw [[wikilinks]] left as text by marked
      $("note-body").innerHTML = $("note-body").innerHTML.replace(
        /\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]/g,
        (_, title) => {
          const t = title.trim();
          const match = state.notes.find(
            (n) =>
              n.title === t ||
              n.path === t + ".md" ||
              n.path.endsWith("/" + t + ".md") ||
              n.path.replace(/\.md$/i, "") === t
          );
          if (match) {
            return `<a href="#" data-note="${escapeHtml(match.path)}">${escapeHtml(t)}</a>`;
          }
          return `<span class="muted">[[${escapeHtml(t)}]]</span>`;
        }
      );
      $("note-body").querySelectorAll("a[data-note]").forEach((a) => {
        a.addEventListener("click", (e) => {
          e.preventDefault();
          openNote(a.getAttribute("data-note"));
        });
      });
    } else {
      $("note-body").innerHTML = `<pre>${escapeHtml(body)}</pre>`;
    }
  }

  async function runSearch(q) {
    const meta = $("search-meta");
    if (!q.trim()) {
      state.filtered = state.notes.slice();
      meta.textContent = "";
      renderNoteList(state.filtered);
      return;
    }
    meta.textContent = "Searching…";
    const res = await fetch(
      "/api/search?q=" + encodeURIComponent(q) + "&limit=50"
    );
    const data = await res.json();
    const results = data.results || [];
    // Map search hits back to note list shape
    const byPath = new Map(state.notes.map((n) => [n.path, n]));
    state.filtered = results.map((r) => byPath.get(r.path) || r);
    meta.textContent = `${results.length} result(s) for “${q}”`;
    renderNoteList(state.filtered);
  }

  async function loadAuth() {
    try {
      const res = await fetch("/api/auth");
      const a = await res.json();
      const pill = $("auth-pill");
      if (a.ok) {
        pill.textContent = `live · ${a.source || "xai"}`;
        pill.className = "pill ok";
      } else {
        pill.textContent = "offline fallback";
        pill.className = "pill warn";
        pill.title = a.error || "No live credentials";
      }
    } catch (e) {
      $("auth-pill").textContent = "auth error";
      $("auth-pill").className = "pill warn";
    }
  }

  async function onAsk(e) {
    e.preventDefault();
    const q = $("ask-question").value.trim();
    if (!q) return;
    const btn = $("btn-ask");
    const status = $("ask-status");
    const answerEl = $("ask-answer");
    const sourcesEl = $("ask-sources");
    btn.disabled = true;
    status.textContent = "Thinking…";
    answerEl.hidden = true;
    sourcesEl.hidden = true;
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: q,
          force_offline: $("ask-offline").checked,
        }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      const mode = data.mode || "unknown";
      const fallback = data.fallback_reason
        ? ` · fallback: ${data.fallback_reason}`
        : "";
      answerEl.hidden = false;
      answerEl.innerHTML = `<div class="mode-tag">mode: ${escapeHtml(mode)}${escapeHtml(
        fallback
      )} · hits: ${data.hit_count ?? "?"}</div>${
        window.marked
          ? marked.parse(data.answer || "")
          : `<pre>${escapeHtml(data.answer || "")}</pre>`
      }`;
      const sources = data.sources || [];
      if (sources.length) {
        sourcesEl.hidden = false;
        sourcesEl.innerHTML =
          "<strong>Sources</strong><ul>" +
          sources
            .map(
              (s) =>
                `<li><a data-note="${escapeHtml(s.path)}">${escapeHtml(
                  s.title || s.path
                )}</a> <span class="muted">(${escapeHtml(s.path)})</span></li>`
            )
            .join("") +
          "</ul>";
        sourcesEl.querySelectorAll("a[data-note]").forEach((a) => {
          a.addEventListener("click", (ev) => {
            ev.preventDefault();
            openNote(a.getAttribute("data-note"));
          });
        });
      }
      status.textContent = "Done";
    } catch (err) {
      status.textContent = String(err.message || err);
      answerEl.hidden = false;
      answerEl.textContent = "Error: " + (err.message || err);
    } finally {
      btn.disabled = false;
    }
  }

  function wire() {
    $("search-input").addEventListener("input", (e) => {
      clearTimeout(state.searchTimer);
      const q = e.target.value;
      state.searchTimer = setTimeout(() => runSearch(q), 200);
    });
    $("ask-form").addEventListener("submit", onAsk);
  }

  wire();
  loadNotes().catch((e) => {
    $("note-body").textContent = "Failed to load notes: " + e;
  });
  loadAuth();
})();
