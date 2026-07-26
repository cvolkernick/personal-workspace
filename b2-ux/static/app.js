/* B2 Brain 2 — browse, search, knowledge graph, Ask Grok */
(() => {
  const $ = (id) => document.getElementById(id);

  const state = {
    notes: [],
    filtered: [],
    activePath: null,
    searchTimer: null,
    graph: null,
    graphAnim: null,
  };

  const GROUP_COLORS = {
    root: "#6ea8ff",
    domains: "#8b7cff",
    map: "#3dd68c",
    default: "#f0b429",
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
    const hub =
      state.notes.find((n) => /00 Home/i.test(n.path) || /hub/i.test(n.title)) ||
      state.notes[0];
    if (hub) openNote(hub.path);
    loadGraph();
  }

  async function openNote(path) {
    state.activePath = path;
    renderNoteList(state.filtered);
    if (state.graph) state.graph.setActive(path);
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

  /* ——— Knowledge graph (Obsidian-style force layout) ——— */

  function createForceGraph(canvas, wrap) {
    const ctx = canvas.getContext("2d");
    const tooltip = $("graph-tooltip");
    let nodes = [];
    let edges = [];
    let nodeById = new Map();
    let activeId = null;
    let hoverId = null;
    let raf = null;
    let running = true;

    // camera
    let scale = 1;
    let tx = 0;
    let ty = 0;

    // interaction
    let dragging = null; // node
    let panning = false;
    let lastX = 0;
    let lastY = 0;
    let moved = false;

    function colorFor(group) {
      return GROUP_COLORS[group] || GROUP_COLORS.default;
    }

    function resize() {
      const dpr = window.devicePixelRatio || 1;
      const rect = wrap.getBoundingClientRect();
      const w = Math.max(100, rect.width);
      const h = Math.max(100, rect.height);
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { w, h };
    }

    function setData(graph) {
      const { w, h } = resize();
      const rawNodes = graph.nodes || [];
      const rawEdges = graph.edges || [];
      const cx = w / 2;
      const cy = h / 2;
      nodes = rawNodes.map((n, i) => {
        const angle = (i / Math.max(1, rawNodes.length)) * Math.PI * 2;
        const r = 40 + Math.random() * Math.min(w, h) * 0.28;
        return {
          id: n.id || n.path,
          path: n.path,
          title: n.title,
          group: n.group || "root",
          degree: n.degree || 0,
          x: cx + Math.cos(angle) * r + (Math.random() - 0.5) * 20,
          y: cy + Math.sin(angle) * r + (Math.random() - 0.5) * 20,
          vx: 0,
          vy: 0,
          fx: null,
          fy: null,
        };
      });
      nodeById = new Map(nodes.map((n) => [n.id, n]));
      edges = rawEdges
        .map((e) => ({
          source: nodeById.get(e.source),
          target: nodeById.get(e.target),
        }))
        .filter((e) => e.source && e.target);
      scale = 1;
      tx = 0;
      ty = 0;
      tick(true);
    }

    function setActive(path) {
      activeId = path;
    }

    function simStep() {
      const n = nodes.length;
      if (!n) return;
      // repulsion
      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          const a = nodes[i];
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let dist2 = dx * dx + dy * dy || 0.01;
          const dist = Math.sqrt(dist2);
          const force = 1800 / dist2;
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          a.vx += fx;
          a.vy += fy;
          b.vx -= fx;
          b.vy -= fy;
        }
      }
      // spring edges
      for (const e of edges) {
        const a = e.source;
        const b = e.target;
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const ideal = 90;
        const k = 0.03;
        const f = (dist - ideal) * k;
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }
      // weak center gravity
      const { w, h } = { w: canvas.clientWidth, h: canvas.clientHeight };
      const cx = w / 2;
      const cy = h / 2;
      for (const node of nodes) {
        node.vx += (cx - node.x) * 0.004;
        node.vy += (cy - node.y) * 0.004;
        if (node.fx != null) {
          node.x = node.fx;
          node.y = node.fy;
          node.vx = 0;
          node.vy = 0;
        } else {
          node.vx *= 0.82;
          node.vy *= 0.82;
          node.x += node.vx;
          node.y += node.vy;
        }
      }
    }

    function screenToWorld(sx, sy) {
      const rect = canvas.getBoundingClientRect();
      const x = sx - rect.left;
      const y = sy - rect.top;
      return {
        x: (x - tx) / scale,
        y: (y - ty) / scale,
      };
    }

    function hitTest(sx, sy) {
      const p = screenToWorld(sx, sy);
      let best = null;
      let bestD = Infinity;
      for (const node of nodes) {
        const r = 6 + Math.min(10, node.degree * 1.5);
        const dx = p.x - node.x;
        const dy = p.y - node.y;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d <= r + 4 && d < bestD) {
          best = node;
          bestD = d;
        }
      }
      return best;
    }

    function draw() {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);

      // subtle grid
      ctx.save();
      ctx.strokeStyle = "rgba(36, 48, 73, 0.45)";
      ctx.lineWidth = 1;
      const grid = 40 * scale;
      const ox = tx % grid;
      const oy = ty % grid;
      for (let x = ox; x < w; x += grid) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
      }
      for (let y = oy; y < h; y += grid) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
      }
      ctx.restore();

      ctx.save();
      ctx.translate(tx, ty);
      ctx.scale(scale, scale);

      // edges
      for (const e of edges) {
        const a = e.source;
        const b = e.target;
        const hi =
          hoverId && (a.id === hoverId || b.id === hoverId);
        const act =
          activeId && (a.id === activeId || b.id === activeId);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = hi || act
          ? "rgba(110, 168, 255, 0.55)"
          : "rgba(139, 155, 184, 0.22)";
        ctx.lineWidth = (hi || act ? 1.6 : 1) / scale;
        ctx.stroke();
      }

      // nodes
      for (const node of nodes) {
        const r = 6 + Math.min(10, node.degree * 1.5);
        const isActive = node.id === activeId;
        const isHover = node.id === hoverId;
        const col = colorFor(node.group);

        if (isActive || isHover) {
          ctx.beginPath();
          ctx.arc(node.x, node.y, r + 6, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(110, 168, 255, 0.18)";
          ctx.fill();
        }

        ctx.beginPath();
        ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
        ctx.fillStyle = col;
        ctx.globalAlpha = isActive || isHover ? 1 : 0.9;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = isActive ? "#fff" : "rgba(8, 13, 24, 0.8)";
        ctx.lineWidth = (isActive ? 2 : 1) / scale;
        ctx.stroke();

        // labels for high-degree / active / hover
        if (isActive || isHover || node.degree >= 2 || nodes.length <= 14) {
          ctx.font = `${12 / scale}px system-ui, sans-serif`;
          ctx.fillStyle = "rgba(232, 238, 252, 0.92)";
          ctx.textAlign = "center";
          ctx.textBaseline = "top";
          const label =
            node.title.length > 28 ? node.title.slice(0, 26) + "…" : node.title;
          ctx.fillText(label, node.x, node.y + r + 4 / scale);
        }
      }
      ctx.restore();
    }

    function tick(forceDraw) {
      if (!running && !forceDraw) return;
      // cool down: fewer steps when settled
      let energy = 0;
      for (let i = 0; i < 3; i++) simStep();
      for (const n of nodes) energy += n.vx * n.vx + n.vy * n.vy;
      draw();
      if (energy > 0.02 || dragging) {
        raf = requestAnimationFrame(() => tick(false));
      } else {
        raf = null;
      }
    }

    function kick() {
      if (!raf) raf = requestAnimationFrame(() => tick(false));
    }

    function showTooltip(node, clientX, clientY) {
      if (!node) {
        tooltip.hidden = true;
        return;
      }
      const rect = wrap.getBoundingClientRect();
      tooltip.hidden = false;
      tooltip.innerHTML = `<div class="tt-title">${escapeHtml(node.title)}</div>
        <div class="tt-path">${escapeHtml(node.path)} · degree ${node.degree}</div>`;
      tooltip.style.left = clientX - rect.left + "px";
      tooltip.style.top = clientY - rect.top + "px";
    }

    canvas.addEventListener("pointerdown", (e) => {
      moved = false;
      lastX = e.clientX;
      lastY = e.clientY;
      const hit = hitTest(e.clientX, e.clientY);
      if (hit) {
        dragging = hit;
        hit.fx = hit.x;
        hit.fy = hit.y;
        canvas.setPointerCapture(e.pointerId);
      } else {
        panning = true;
        canvas.setPointerCapture(e.pointerId);
      }
      kick();
    });

    canvas.addEventListener("pointermove", (e) => {
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      if (dragging) {
        const p = screenToWorld(e.clientX, e.clientY);
        dragging.fx = p.x;
        dragging.fy = p.y;
        dragging.x = p.x;
        dragging.y = p.y;
        lastX = e.clientX;
        lastY = e.clientY;
        kick();
        showTooltip(dragging, e.clientX, e.clientY);
        return;
      }
      if (panning) {
        tx += dx;
        ty += dy;
        lastX = e.clientX;
        lastY = e.clientY;
        draw();
        return;
      }
      const hit = hitTest(e.clientX, e.clientY);
      hoverId = hit ? hit.id : null;
      canvas.style.cursor = hit ? "pointer" : "grab";
      showTooltip(hit, e.clientX, e.clientY);
      draw();
    });

    canvas.addEventListener("pointerup", (e) => {
      if (dragging) {
        const path = dragging.path;
        dragging.fx = null;
        dragging.fy = null;
        dragging = null;
        if (!moved) openNote(path);
      }
      panning = false;
      kick();
    });

    canvas.addEventListener("pointerleave", () => {
      hoverId = null;
      tooltip.hidden = true;
      draw();
    });

    canvas.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const before = {
          x: (mx - tx) / scale,
          y: (my - ty) / scale,
        };
        const factor = e.deltaY < 0 ? 1.1 : 0.9;
        scale = Math.min(4, Math.max(0.35, scale * factor));
        tx = mx - before.x * scale;
        ty = my - before.y * scale;
        draw();
      },
      { passive: false }
    );

    window.addEventListener("resize", () => {
      resize();
      draw();
    });

    return {
      setData,
      setActive,
      resetView() {
        scale = 1;
        tx = 0;
        ty = 0;
        for (const n of nodes) {
          n.vx = (Math.random() - 0.5) * 4;
          n.vy = (Math.random() - 0.5) * 4;
        }
        kick();
        draw();
      },
      destroy() {
        running = false;
        if (raf) cancelAnimationFrame(raf);
      },
    };
  }

  async function loadGraph() {
    const stats = $("graph-stats");
    try {
      const res = await fetch("/api/graph");
      const data = await res.json();
      if (!state.graph) {
        state.graph = createForceGraph($("graph-canvas"), $("graph-wrap"));
      }
      state.graph.setData(data);
      if (state.activePath) state.graph.setActive(state.activePath);
      const s = data.stats || {};
      stats.textContent = `${s.note_count ?? 0} nodes · ${s.edge_count ?? 0} links`;
    } catch (e) {
      stats.textContent = "graph failed to load";
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

    $("btn-graph").addEventListener("click", () => {
      const card = $("graph-card");
      card.hidden = false;
      card.classList.remove("collapsed");
      $("btn-graph").classList.add("active");
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
      if (state.graph) state.graph.resetView();
      else loadGraph();
    });
    $("btn-graph-toggle").addEventListener("click", () => {
      const card = $("graph-card");
      const collapsed = card.classList.toggle("collapsed");
      $("btn-graph-toggle").textContent = collapsed ? "Show" : "Hide";
      $("btn-graph").classList.toggle("active", !collapsed);
    });
    $("btn-graph-reset").addEventListener("click", () => {
      if (state.graph) state.graph.resetView();
      else loadGraph();
    });
  }

  wire();
  loadNotes().catch((e) => {
    $("note-body").textContent = "Failed to load notes: " + e;
  });
  loadAuth();
})();
