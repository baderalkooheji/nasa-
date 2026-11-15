/* Marswalk AI - assistant panel (embeddable).
 *
 *   MarswalkAssistant.mount({ apiBase: "", bridge: new MarswalkActionBridge({...}), onResponse })
 *
 * Talks to the assistant backend (/api/assistant/*), renders messages, tool timelines,
 * result cards, evidence, limitations and confirmations, executes frontend actions through
 * the bridge and reports each outcome back (actions are only marked done after the page ran them).
 * All DOM ids/classes are prefixed "mwai-" so the widget can live inside another app.
 */
(function (global) {
  "use strict";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtM = (m) => (m == null ? "–" : m >= 1000 ? (m / 1000).toFixed(2) + " km" : Math.round(m) + " m");
  const fmtMin = (m) => (m == null ? "–" : m < 90 ? Math.round(m) + " min" : `${Math.floor(m / 60)} h ${String(Math.round(m % 60)).padStart(2, "0")}`);
  const fmtDeg = (d) => (d == null ? "–" : d + "°");
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

  function richText(text) {
    // escaped text; bullets and line breaks kept; [DOC-n] citations highlighted
    return esc(text).split("\n").map((line) => {
      const l = line.replace(/\[(DOC-\d+|REF-\d+)\]/g, '<span class="mwai-cite">[$1]</span>');
      return l.startsWith("• ") ? `<div class="mwai-bullet">${l.slice(2)}</div>` : `<p>${l}</p>`;
    }).join("");
  }

  class Assistant {
    constructor(opts) {
      this.api = (opts.apiBase || "").replace(/\/$/, "");
      this.bridge = opts.bridge;
      this.onResponse = opts.onResponse || (() => {});
      this.missionLog = opts.missionLog || null;   // { selected(), save(), list(q), open(rec) } from the host page
      this.sessionId = null;
      this.info = null;
      this.busy = false;
      this.root = this._build(opts.title || "MARSWALK AI");
      (opts.container || document.body).appendChild(this.root);
      this._connect();
    }

    // ------------------------------------------------------------------ layout
    _build(title) {
      const root = el("div", "mwai-root");
      root.innerHTML = `
        <button class="mwai-orb" type="button" aria-label="Open Marswalk AI" aria-expanded="false">
          <span class="mwai-orb-core"></span><span class="mwai-orb-ring"></span>
        </button>
        <section class="mwai-panel" role="dialog" aria-label="Marswalk AI assistant" hidden>
          <header class="mwai-head" title="Drag to move · double-click to reset position">
            <div class="mwai-title"><span class="mwai-dot" aria-hidden="true"></span><b>${esc(title)}</b>
              <span class="mwai-sub" data-ref="status">connecting…</span></div>
            <div class="mwai-head-btns">
              <button type="button" class="mwai-icon mwai-logbtn" data-ref="logbtn" title="Mission log" aria-label="Mission log" aria-pressed="false" hidden>☰ Log</button>
              <button type="button" class="mwai-icon" data-ref="sources" title="Data sources" aria-label="Show data sources">◎</button>
              <button type="button" class="mwai-icon" data-ref="reset" title="New conversation" aria-label="New conversation">↺</button>
              <button type="button" class="mwai-icon" data-ref="close" title="Close" aria-label="Close assistant">✕</button>
            </div>
          </header>
          <div class="mwai-sourcebar" data-ref="sourcebar" hidden></div>
          <div class="mwai-log" data-ref="log" aria-live="polite"></div>
          <div class="mwai-records" data-ref="records" hidden>
            <div class="mwai-rec-save">
              <div class="mwai-rec-sel" data-ref="recsel"></div>
              <button type="button" class="mwai-btn mwai-primary" data-ref="recsave">Save selected location</button>
            </div>
            <input type="search" class="mwai-rec-search" data-ref="recq" placeholder="Search the mission log" aria-label="Search the mission log">
            <ul class="mwai-rec-list" data-ref="reclist"></ul>
            <button type="button" class="mwai-btn mwai-rec-back" data-ref="recback">← Back to chat</button>
          </div>
          <div class="mwai-suggest" data-ref="suggest"></div>
          <form class="mwai-form" data-ref="form">
            <input type="text" data-ref="input" placeholder="Ask Marswalk AI…" aria-label="Message to Marswalk AI" autocomplete="off" maxlength="2000">
            <button type="submit" class="mwai-send" data-ref="send" aria-label="Send">➤</button>
          </form>
        </section>`;
      const $ = (r) => root.querySelector(`[data-ref="${r}"]`);
      this.ui = { orb: root.querySelector(".mwai-orb"), panel: root.querySelector(".mwai-panel"), status: $("status"), log: $("log"),
                  suggest: $("suggest"), form: $("form"), input: $("input"), send: $("send"), sourcebar: $("sourcebar") };
      this.ui.orb.onclick = () => this.toggle(true);
      $("close").onclick = () => this.toggle(false);
      $("reset").onclick = () => this._connect(true);
      $("sources").onclick = () => { this.ui.sourcebar.hidden = !this.ui.sourcebar.hidden; };
      this.ui.form.onsubmit = (e) => { e.preventDefault(); const q = this.ui.input.value.trim(); if (q) { this.ui.input.value = ""; this.ask(q); } };
      Object.assign(this.ui, { logbtn: $("logbtn"), records: $("records"), recsel: $("recsel"), recsave: $("recsave"), recq: $("recq"), reclist: $("reclist") });
      if (this.missionLog) {
        this.ui.logbtn.hidden = false;
        this.ui.logbtn.onclick = () => this.showRecords(this.ui.records.hidden);
        $("recback").onclick = () => this.showRecords(false);
        this.ui.recsave.onclick = () => this.saveLocation();
        let t = 0;
        this.ui.recq.oninput = () => { clearTimeout(t); t = setTimeout(() => this.loadRecords(), 200); };
        this.ui.reclist.onclick = (e) => {
          const li = e.target.closest("li[data-i]");
          if (li && this._rows) this.missionLog.open(this._rows[+li.dataset.i]);
        };
      }
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !this.ui.panel.hidden) this.toggle(false); });
      this._initDrag(root.querySelector(".mwai-panel"), root.querySelector(".mwai-head"));
      return root;
    }

    // ------------------------------------------------------------------ move / resize
    _initDrag(panel, head) {
      const KEY = "mwai-panel-geometry";
      const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
      const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; } };
      const save = () => {
        if (panel.hidden) return;
        const r = panel.getBoundingClientRect();
        try { localStorage.setItem(KEY, JSON.stringify({ x: r.left, y: r.top, w: r.width, h: r.height })); } catch { /* storage unavailable */ }
      };
      const fit = () => {                                   // keep the panel fully on screen
        const r = panel.getBoundingClientRect();
        const w = Math.min(r.width, innerWidth - 16), h = Math.min(r.height, innerHeight - 16);
        Object.assign(panel.style, { width: w + "px", height: h + "px",
          left: clamp(r.left, 8, innerWidth - w - 8) + "px", top: clamp(r.top, 8, innerHeight - h - 8) + "px" });
      };
      this._place = (reset) => {
        const g = reset ? null : load();
        const w = Math.min(g ? g.w : 420, innerWidth - 16);
        const h = Math.min(g ? g.h : Math.min(620, innerHeight * 0.72), innerHeight - 16);
        Object.assign(panel.style, { width: w + "px", height: h + "px",
          left: clamp(g ? g.x : innerWidth - w - 20, 8, innerWidth - w - 8) + "px",
          top: clamp(g ? g.y : innerHeight - h - 20, 8, innerHeight - h - 8) + "px" });
      };
      let drag = null;
      head.addEventListener("pointerdown", (e) => {
        if (e.button !== 0 || e.target.closest("button")) return;
        const r = panel.getBoundingClientRect();
        drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
        head.setPointerCapture(e.pointerId);
        panel.classList.add("mwai-dragging");
      });
      head.addEventListener("pointermove", (e) => {
        if (!drag) return;
        const r = panel.getBoundingClientRect();
        panel.style.left = clamp(e.clientX - drag.dx, 8, innerWidth - r.width - 8) + "px";
        panel.style.top = clamp(e.clientY - drag.dy, 8, innerHeight - r.height - 8) + "px";
      });
      const end = () => { if (drag) { drag = null; panel.classList.remove("mwai-dragging"); save(); } };
      head.addEventListener("pointerup", end);
      head.addEventListener("pointercancel", end);
      head.addEventListener("dblclick", (e) => { if (!e.target.closest("button")) { this._place(true); save(); } });
      let t = 0;
      if (window.ResizeObserver) new ResizeObserver(() => { clearTimeout(t); t = setTimeout(save, 300); }).observe(panel);
      addEventListener("resize", () => { if (!panel.hidden) fit(); });
    }

    toggle(open) {
      if (open && this.ui.panel.hidden) this._place();
      this.ui.panel.hidden = !open;
      this.ui.orb.setAttribute("aria-expanded", String(open));
      this.root.classList.toggle("mwai-open", open);
      if (open) setTimeout(() => this.ui.input.focus(), 30);
    }

    // ------------------------------------------------------------------ mission log (records stored by the host page's backend)
    showRecords(show) {
      if (!this.missionLog) return;
      this.toggle(true);
      this.ui.records.hidden = !show;
      [this.ui.log, this.ui.suggest, this.ui.form].forEach((n) => n.classList.toggle("mwai-gone", show));
      this.ui.logbtn.setAttribute("aria-pressed", String(show));
      this.ui.logbtn.classList.toggle("mwai-on", show);
      if (show) { this.refreshSelection(); this.loadRecords(); }
    }

    refreshSelection() {
      if (!this.missionLog) return;
      const s = this.missionLog.selected();
      this.ui.recsel.innerHTML = s
        ? `<b>${esc(s.label)}</b><div class="mwai-muted">${(+s.lat).toFixed(5)}°N ${(+s.lon).toFixed(5)}°E · ${s.elevation_m != null ? esc(s.elevation_m) + " m" : "no elevation"} · slope ${s.slope_deg != null ? esc(s.slope_deg) + "°" : "n/a"}</div>`
        : `<span class="mwai-muted">Click a point on the map (or a stop), then save it here.</span>`;
      this.ui.recsave.disabled = !s;
    }

    async loadRecords() {
      const list = this.ui.reclist;
      try {
        const rows = await this.missionLog.list(this.ui.recq.value.trim());
        this._rows = rows;
        list.innerHTML = rows.length ? rows.map((r, i) => `<li data-i="${i}" title="Show on the map">
            <div class="mwai-rec-h"><b>${esc(r.label)}</b><span class="mwai-tag">${esc(r.analysis_type)}</span></div>
            <div class="mwai-rec-r">${esc(r.result)}</div>
            <div class="mwai-muted mwai-rec-t">${esc(r.created_at || "")} · ${esc(r.status)}</div></li>`).join("")
          : `<li class="mwai-rec-empty mwai-muted">${this.ui.recq.value.trim() ? "No matching entries." : "No entries yet. Select a point on the map and press Save."}</li>`;
      } catch (e) {
        list.innerHTML = `<li class="mwai-rec-empty mwai-err-t">Mission log unavailable (${esc(e.message)}).</li>`;
      }
    }

    /** Save the selected map point; used by the Save button and by chat ("save this location"). */
    async saveLocation(fromChat) {
      let rec = null, err = null;
      this.ui.recsave.disabled = true;
      try { rec = await this.missionLog.save(); } catch (e) { err = e; }
      this.refreshSelection();
      if (!this.ui.records.hidden) await this.loadRecords();
      const done = rec ? `Saved “${rec.label}” to the mission log (${rec.result}).` : `Could not save: ${err.message}`;
      if (fromChat) {
        const msg = el("div", `mwai-msg mwai-bot${rec ? "" : " mwai-status-partial"}`, `<div class="mwai-text"><p>${esc(done)}</p></div>`);
        if (rec) {
          const row = el("div", "mwai-btnrow mwai-btnrow-flat");
          const b = el("button", "mwai-btn", "Open mission log"); b.type = "button"; b.onclick = () => this.showRecords(true);
          row.appendChild(b); msg.appendChild(row);
        }
        this.ui.log.appendChild(msg); this._scroll();
      } else if (rec) {
        const first = this.ui.reclist.querySelector("li[data-i]");
        if (first) first.classList.add("mwai-rec-new");
      } else {
        this.ui.recsel.insertAdjacentHTML("beforeend", `<div class="mwai-err-t">${esc(done)}</div>`);
      }
    }

    /** Mission-log requests handled on the page (no backend round trip): "save this location", "show the log". */
    _logIntent(text) {
      if (!this.missionLog) return null;
      const t = text.toLowerCase();
      if (/\b(plan|route|eva)\b/.test(t)) return null;                       // EVA-plan logging stays with the assistant backend
      if (/\b(save|log|record|store|bookmark)\b/.test(t) && /\b(location|point|place|spot|stop|this|here|it|selected)\b/.test(t)) return "save";
      if (/\b(show|open|view|see|list|display)\b/.test(t) && /\b(log|records?|saved|entries)\b/.test(t)) return "show";
      return null;
    }

    // ------------------------------------------------------------------ backend
    async _post(path, body) {
      const r = await fetch(this.api + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      let j = null;
      try { j = await r.json(); } catch { /* not JSON */ }
      if (!r.ok) {
        const d = j && j.detail;
        const msg = (d && d.error && d.error.message) || (typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : r.statusText);
        throw Object.assign(new Error(msg || `HTTP ${r.status}`), { status: r.status });
      }
      return j;
    }

    async _connect(reset) {
      if (reset) this.ui.log.innerHTML = "";
      this.ui.status.textContent = "connecting…";
      try {
        this.info = await this._post("/api/assistant/sessions", { frontend: this.bridge ? this.bridge.handshake() : {} });
        this.sessionId = this.info.session_id;
        const p = this.info.provider;
        this.ui.status.textContent = p.name === "offline-rules" ? "offline rules mode" : `${p.name} · ${p.model}`;
        this.root.classList.remove("mwai-offline");
        this._renderSources();
        this._greet();
      } catch (e) {
        this.ui.status.textContent = "backend unreachable";
        this.root.classList.add("mwai-offline");
        this._notice("error", `Cannot reach the Marswalk AI backend (${e.message}). Start it and press ↺.`);
      }
    }

    _renderSources() {
      const st = this.info.integrations || {};
      const chip = (label, state, title) => `<span class="mwai-chip mwai-${state}" title="${esc(title || "")}">${esc(label)}</span>`;
      const t = st.terrain || {};
      const eng = st.science_engine || {};
      const mw = st.marswalk_backend || {};
      const kn = st.knowledge || {};
      this.ui.sourcebar.innerHTML = [
        chip(`Terrain: ${(t.datasets || []).join(" + ") || "none"} (${t.data_mode})`, t.data_mode === "real" ? "ok" : t.data_mode === "simulated" ? "warn" : "off"),
        chip(`Science Engine: ${eng.reachable ? "connected" : eng.configured ? "unreachable" : "not configured"}`, eng.reachable ? "ok" : "off", eng.error),
        chip(`Marswalk backend: ${mw.reachable ? (mw.endpoints || []).join(", ") : mw.configured ? "unreachable" : "not configured"}`, mw.reachable ? "ok" : "off"),
        chip(`Documents: ${kn.available ? kn.documents : "none"}`, kn.available ? "ok" : "off", kn.error),
        chip(`Mission data: ${st.mission_data}`, st.mission_data === "mock" ? "warn" : "ok"),
        chip(`LLM: ${this.info.provider.note}`, this.info.provider.name === "offline-rules" ? "warn" : "ok"),
      ].join("");
    }

    _greet() {
      const m = this.info.mission || {};
      const cp = m.current_position || {};
      const msg = el("div", "mwai-msg mwai-bot");
      msg.innerHTML = `<div class="mwai-text"><p>Marswalk AI online. Mission <b>${esc(m.name)}</b>, position ${esc(cp.name || "unknown")}.</p>
        <p class="mwai-muted">I plan and compare routes on the Jezero elevation data, analyse terrain, estimate EVA time, and control the map.
        Every figure I give comes from a mission tool; I'll tell you when data is missing.</p></div>`;
      this.ui.log.appendChild(msg);
      this._suggest(["Find me the best route to the delta", "Show me the Perseverance rover location", "Analyze the terrain around the habitat",
        ...(this.missionLog ? ["Save this location to the log", "Show the mission log"] : []), "What can you do?"]);
    }

    _suggest(list) {
      this.ui.suggest.innerHTML = "";
      (list || []).forEach((q) => {
        const b = el("button", "mwai-sugg", esc(q));
        b.type = "button";
        b.onclick = () => this.ask(q);
        this.ui.suggest.appendChild(b);
      });
    }

    _setBusy(on) {
      this.busy = on;
      this.ui.send.disabled = on;
      this.ui.input.disabled = on;
      this.root.classList.toggle("mwai-busy", on);
    }

    _scroll() { this.ui.log.scrollTop = this.ui.log.scrollHeight; }

    _notice(kind, text) {
      const n = el("div", `mwai-notice mwai-${kind}`, esc(text));
      this.ui.log.appendChild(n);
      this._scroll();
    }

    _thinking() {
      const t = el("div", "mwai-msg mwai-bot mwai-thinking",
        `<div class="mwai-text"><span class="mwai-pulse"></span><span class="mwai-pulse"></span><span class="mwai-pulse"></span>
         <span class="mwai-muted">Working on it</span></div>`);
      this.ui.log.appendChild(t);
      this._scroll();
      return t;
    }

    async ask(text) {
      if (this.busy) return;
      if (!this.sessionId) { await this._connect(); if (!this.sessionId) return; }
      this.toggle(true);
      if (!this.ui.records.hidden) this.showRecords(false);
      this.ui.log.appendChild(el("div", "mwai-msg mwai-user", `<div class="mwai-text">${esc(text)}</div>`));
      this._suggest([]);
      const li = this._logIntent(text);
      if (li === "save") { await this.saveLocation(true); return; }
      if (li === "show") { this.showRecords(true); return; }
      await this._run(() => this._post("/api/assistant/chat", { session_id: this.sessionId, message: text, client_context: this.bridge ? this.bridge.getContext() : null }));
    }

    async command(capabilityId, args) {
      if (this.busy || !this.sessionId) return;
      await this._run(() => this._post("/api/assistant/command", { session_id: this.sessionId, capability_id: capabilityId, arguments: args,
        client_context: this.bridge ? this.bridge.getContext() : null }), true);
    }

    async confirm(id, approve) {
      if (this.busy) return;
      await this._run(() => this._post("/api/assistant/confirm", { session_id: this.sessionId, confirmation_id: id, approve }), true);
    }

    async _run(fn, compact) {
      this._setBusy(true);
      const wait = this._thinking();
      try {
        const res = await fn();
        wait.remove();
        await this._render(res, compact);
        this.onResponse(res);
      } catch (e) {
        wait.remove();
        if (e.status === 404) { this.sessionId = null; this._notice("error", "The session expired. Press ↺ to start a new one."); }
        else this._notice("error", `Request failed: ${e.message}`);
      } finally {
        this._setBusy(false);
        this._scroll();
      }
    }

    // ------------------------------------------------------------------ rendering
    async _render(r, compact) {
      const msg = el("div", `mwai-msg mwai-bot mwai-status-${r.status}`);
      const badges = [];
      if (r.simulated) badges.push('<span class="mwai-badge mwai-warn">SIMULATED DATA</span>');
      if (r.provider && r.provider.mode === "rules-fallback") badges.push('<span class="mwai-badge mwai-warn">LLM fallback</span>');
      if (r.status === "partial") badges.push('<span class="mwai-badge mwai-warn">partial</span>');
      if (r.status === "error") badges.push('<span class="mwai-badge mwai-err">error</span>');
      msg.innerHTML = `${badges.length ? `<div class="mwai-badges">${badges.join("")}</div>` : ""}<div class="mwai-text">${richText(r.message)}</div>`;

      if (r.tool_calls && r.tool_calls.length && !compact) msg.appendChild(this._timeline(r));
      const actionBox = el("div", "mwai-actions");
      if (r.frontend_actions && r.frontend_actions.length) msg.appendChild(actionBox);
      (r.cards || []).forEach((c) => msg.appendChild(this._card(c)));
      (r.pending_confirmations || []).forEach((c) => msg.appendChild(this._confirmBox(c)));
      if (r.errors && r.errors.length) {
        const errs = el("div", "mwai-errors");
        r.errors.forEach((e) => errs.appendChild(el("div", "mwai-err-line",
          `<b>${esc(e.capability_id || "assistant")}</b> · ${esc(e.code)}: ${esc(e.message)}`)));
        msg.appendChild(errs);
      }
      if (r.evidence && r.evidence.length) msg.appendChild(this._fold(`Sources (${r.evidence.length})`, r.evidence.map((e) =>
        `<div class="mwai-ev"><b>${esc(e.citation_key ? `[${e.citation_key}] ` : "")}${esc(e.title)}</b>${e.section ? ` · ${esc(e.section)}` : ""}
         ${e.source_url ? `<a href="${esc(e.source_url)}" target="_blank" rel="noopener">source</a>` : ""}
         <div class="mwai-muted">${esc((e.relevant_text || "").slice(0, 260))}</div></div>`).join("")));
      const lim = [...(r.mock_components || []).map((x) => `Standalone environment: ${x}.`), ...(r.limitations || [])];
      if (lim.length) msg.appendChild(this._fold(`Limitations (${lim.length})`, lim.map((l) => `<div class="mwai-lim">${esc(l)}</div>`).join("")));
      this.ui.log.appendChild(msg);
      this._scroll();
      if (r.suggestions) this._suggest(r.suggestions);
      for (const a of r.frontend_actions || []) await this._execAction(a, actionBox);
    }

    _timeline(r) {
      const byId = Object.fromEntries((r.tool_results || []).map((t) => [t.call_id, t]));
      const box = el("details", "mwai-timeline");
      const n = r.tool_calls.length;
      const failed = (r.tool_results || []).filter((t) => !t.ok).length;
      box.innerHTML = `<summary>⚙ ${n} operation${n > 1 ? "s" : ""}${failed ? ` · <span class="mwai-err-t">${failed} failed</span>` : ""}
        <span class="mwai-muted">· ${esc(r.provider.mode)} · ${r.timing_ms} ms</span></summary>`;
      r.tool_calls.forEach((c) => {
        const t = byId[c.call_id] || {};
        const icon = t.ok ? (t.status === "pending_ack" ? "⇢" : t.status === "pending_confirmation" ? "?" : "✓") : "✕";
        box.appendChild(el("div", `mwai-step mwai-step-${t.ok ? "ok" : "bad"}`,
          `<span class="mwai-step-i">${icon}</span><code>${esc(c.capability_id)}</code>
           <span class="mwai-muted">${esc(t.status || "")}${t.duration_ms != null ? ` · ${t.duration_ms} ms` : ""}${t.provenance && t.provenance.provider ? ` · ${esc(t.provenance.provider)}` : ""}</span>
           ${t.error ? `<div class="mwai-err-t">${esc(t.error.message)}</div>` : ""}`));
      });
      return box;
    }

    _fold(title, html) {
      const d = el("details", "mwai-fold");
      d.innerHTML = `<summary>${esc(title)}</summary><div class="mwai-fold-body">${html}</div>`;
      return d;
    }

    async _execAction(action, box) {
      const chip = el("div", "mwai-act mwai-pending", `<span class="mwai-spin"></span>${esc(action.summary)}`);
      box.appendChild(chip);
      let out = { ok: false, detail: "no map connected" };
      if (this.bridge) out = await this.bridge.execute(action);
      chip.className = `mwai-act ${out.ok ? "mwai-done" : "mwai-failed"}`;
      chip.innerHTML = `${out.ok ? "✓" : "✕"} ${esc(action.summary)}${out.ok ? "" : ` <span class="mwai-muted">(${esc(out.detail)})</span>`}`;
      try {
        await this._post("/api/assistant/actions/result", { session_id: this.sessionId, action_id: action.action_id, ok: out.ok,
          detail: out.detail, type: action.type });
      } catch { /* ack failure is not fatal for the UI */ }
    }

    _confirmBox(c) {
      const box = el("div", "mwai-confirm", `<div><b>Confirmation needed</b><div class="mwai-muted">${esc(c.summary)}</div></div>`);
      const row = el("div", "mwai-btnrow");
      const yes = el("button", "mwai-btn mwai-primary", "Confirm"); yes.type = "button";
      const no = el("button", "mwai-btn", "Cancel"); no.type = "button";
      yes.onclick = () => { yes.disabled = no.disabled = true; this.confirm(c.confirmation_id, true); };
      no.onclick = () => { yes.disabled = no.disabled = true; this.confirm(c.confirmation_id, false); };
      row.append(yes, no);
      box.appendChild(row);
      return box;
    }

    _btn(a) {
      const b = el("button", `mwai-btn${a.style === "primary" ? " mwai-primary" : ""}`, esc(a.label));
      b.type = "button";
      b.onclick = () => (a.kind === "command" ? this.command(a.capability_id, a.arguments) : this.ask(a.text));
      return b;
    }

    _card(c) {
      const card = el("div", `mwai-card mwai-card-${c.type}`);
      const d = c.data || {};
      const prov = c.provenance || {};
      const dm = prov.data_mode || (d.dataset && d.dataset.data_mode);
      card.innerHTML = `<div class="mwai-card-h"><span>${esc(c.title)}</span>${dm && dm !== "n/a" ? `<span class="mwai-tag mwai-tag-${esc(dm)}">${esc(dm)}</span>` : ""}</div>`;
      const body = el("div", "mwai-card-b");
      const kv = (pairs) => `<dl class="mwai-kv">${pairs.filter((p) => p[1] != null && p[1] !== "–").map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;

      if (c.type === "route_set") {
        const rows = d.routes.map((r) => `<tr class="${r.route_id === d.recommended_route_id ? "mwai-rec" : ""}" data-rid="${esc(r.route_id)}">
          <td><b>${esc(r.route_id)}</b> ${esc(r.label)}${r.route_id === d.recommended_route_id ? ' <span class="mwai-tag mwai-tag-rec">recommended</span>' : ""}
          ${r.within_constraints === false ? ' <span class="mwai-tag mwai-tag-warn">over limit</span>' : ""}</td>
          <td>${fmtM(r.distance_m)}</td><td>${fmtDeg(r.max_slope_deg)}</td><td>${fmtDeg(r.avg_slope_deg)}</td><td>${fmtMin(r.walk_time_out_min)}</td>
          <td>${r.weighted_cost ?? "–"}</td><td class="mwai-cell-btn"></td></tr>`).join("");
        body.innerHTML = `<table class="mwai-table"><thead><tr><th>Route</th><th>Dist.</th><th>Max</th><th>Mean</th><th>Walk out</th><th>Cost</th><th></th></tr></thead>
          <tbody>${rows}</tbody></table>
          ${d.rationale && d.rationale.length ? `<ul class="mwai-why">${d.rationale.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
          <div class="mwai-foot">Slope limit ${esc(d.max_slope_deg)}° (project-defined) · ${esc((d.dataset || {}).name || "")} @ ${esc((d.dataset || {}).resolution_m || "?")} m</div>`;
        Object.entries(c.route_actions || {}).forEach(([rid, acts]) => {
          const cell = body.querySelector(`tr[data-rid="${CSS.escape(rid)}"] .mwai-cell-btn`);
          if (cell) acts.forEach((a) => cell.appendChild(this._btn({ ...a, label: "Show" })));
        });
      } else if (c.type === "comparison") {
        const best = d.best_by || {};
        const mark = (rid, k) => (best[k] === rid ? ' <span class="mwai-best">●</span>' : "");
        body.innerHTML = `<table class="mwai-table"><thead><tr><th>Route</th><th>Dist.</th><th>Gain</th><th>Max</th><th>Mean</th><th>Walk out</th><th>Cost</th></tr></thead><tbody>
          ${d.routes.map((r) => `<tr><td><b>${esc(r.route_id)}</b> ${esc(r.label)}</td><td>${fmtM(r.distance_m)}${mark(r.route_id, "shortest")}</td>
          <td>${r.elevation_gain_m ?? "–"} m${mark(r.route_id, "least_climbing")}</td><td>${fmtDeg(r.max_slope_deg)}${mark(r.route_id, "lowest_max_slope")}</td>
          <td>${fmtDeg(r.avg_slope_deg)}</td><td>${fmtMin(r.walk_time_out_min)}${mark(r.route_id, "fastest")}</td>
          <td>${r.weighted_cost ?? "–"}${mark(r.route_id, "lowest_terrain_cost")}</td></tr>`).join("")}</tbody></table>
          <div class="mwai-foot">● best in column. Cost = length-weighted 1 + (slope / 15°)².</div>`;
      } else if (c.type === "route_detail") {
        const m = d.metrics || {};
        const secs = d.steep_sections || [];
        body.innerHTML = kv([["Distance", fmtM(m.distance_m)], ["Gain / loss", `+${m.elevation_gain_m ?? "–"} / −${m.elevation_loss_m ?? "–"} m`],
          ["Max / mean slope", `${fmtDeg(m.max_slope_deg)} / ${fmtDeg(m.avg_slope_deg)}`], ["Above caution", fmtM(m.length_above_caution_m)],
          ["Walk out / back", `${fmtMin(m.walk_time_out_min)} / ${fmtMin(m.walk_time_back_min)}`],
          ["Class", d.classification ? esc(d.classification.terrain_difficulty) : null]]) +
          (secs.length ? `<div class="mwai-sub-h">Steep sections</div>${secs.slice(0, 4).map((s) =>
            `<div class="mwai-sec ${s.over_limit ? "mwai-over" : ""}">${s.from_m}–${s.to_m} m · ${s.length_m} m long · max ${s.max_slope_deg}°</div>`).join("")}` : "");
      } else if (c.type === "terrain") {
        const cls = d.classification || {};
        body.innerHTML = kv([["Elevation", d.elevation_m != null ? `${d.elevation_m} m <span class="mwai-muted">areoid</span>` : "–"],
          ["Relief", d.relief_m != null ? d.relief_m + " m" : null], ["Slope at point", fmtDeg(d.slope_at_point_deg)],
          ["Mean / P90 / max", `${fmtDeg(d.slope_mean_deg)} / ${fmtDeg(d.slope_p90_deg)} / ${fmtDeg(d.slope_max_deg)}`],
          ["Area > caution", d.area_above_caution_pct != null ? d.area_above_caution_pct + "%" : null],
          ["Class", `<span class="mwai-tag">${esc(cls.terrain_difficulty)}</span>`]]) +
          `<div class="mwai-foot">${esc((d.dataset || {}).name)} @ ${esc((d.dataset || {}).resolution_m)} m · radius ${esc(d.radius_m)} m · ${esc(cls.basis || "")}</div>`;
      } else if (c.type === "eva") {
        const total = d.suit_budget_min || d.total_min || 1;
        const seg = (cls, v, label) => (v > 0 ? `<span class="mwai-seg ${cls}" style="flex:${Math.max(v, total * 0.06)}" title="${esc(label)} ${fmtMin(v)}">${esc(label)}</span>` : "");
        body.innerHTML = `<div class="mwai-evabar">${seg("mwai-walk", d.walk_out_min, "Out")}${seg("mwai-sci", d.science_min, "Science")}
          ${seg("mwai-walk", d.walk_back_min, "Back")}${d.remaining_margin_min != null ? seg(d.remaining_margin_min >= 0 ? "mwai-res" : "mwai-bad", Math.abs(d.remaining_margin_min), d.remaining_margin_min >= 0 ? "Margin" : "Over") : ""}
          ${d.required_reserve_min ? seg("mwai-reserve", d.required_reserve_min, "Reserve") : ""}</div>` +
          kv([["Total", `<b>${fmtMin(d.total_min)}</b>`], ["Suit budget", fmtMin(d.suit_budget_min)], ["Reserve", fmtMin(d.required_reserve_min)],
            ["Margin", fmtMin(d.remaining_margin_min)], ["Status", d.status ? `<span class="mwai-tag mwai-tag-${esc(d.status)}">${esc(d.status)}</span>` : null]]) +
          `<div class="mwai-foot">Model estimate (modified Tobler, project-defined suit budget). Not a measurement.</div>`;
      } else if (c.type === "radiation") {
        const calc = d.calculations || {};
        const meas = d.measurements || {};
        body.innerHTML = kv([["Reference rate", `${d.reference_dose_rate_mgy_per_day ?? meas.reference_dose_rate_mgy_per_day} mGy/day`],
          ["Measured at", "Gale crater (Curiosity RAD)"], ["EVA duration", (d.eva_hours ?? meas.eva_duration_hours) != null ? `${d.eva_hours ?? meas.eva_duration_hours} h` : "unknown"],
          ["Est. absorbed dose", (d.estimated_absorbed_dose_mgy ?? calc.estimated_absorbed_dose_mgy) != null ? `${d.estimated_absorbed_dose_mgy ?? calc.estimated_absorbed_dose_mgy} mGy` : "–"]]) +
          `<div class="mwai-foot">Reference value from another site; not a Jezero measurement.</div>`;
      } else if (c.type === "space_weather") {
        const cls = d.classification || {};
        const meas = d.measurements || {};
        body.innerHTML = kv([["Status", `<span class="mwai-tag">${esc(cls.space_weather_status || d.status)}</span>`],
          ["Flares / SEP / CME", `${meas.inspected_flare_count ?? d.flare_count ?? "–"} / ${meas.inspected_sep_count ?? d.sep_count ?? "–"} / ${meas.inspected_cme_count ?? "–"}`],
          ["Source", esc(meas.data_source || d.source)]]) + `<div class="mwai-foot">Project-defined caution rule.</div>`;
      } else if (c.type === "environment") {
        const m = d.measurements || {};
        const t = m.air_temperature_c || {}, p = m.atmospheric_pressure_pa || {}, w = m.wind_speed_ms || {};
        body.innerHTML = kv([["Sol", m.sol], ["Air temp.", t.min != null ? `${t.min} … ${t.max} °C` : null], ["Pressure", p.min != null ? `${p.min} … ${p.max} Pa` : null],
          ["Wind max", w.max != null ? `${w.max} m/s` : null], ["Status", esc((d.classification || {}).environmental_status)]]) +
          `<div class="mwai-foot">Historical, at the rover's position. Not crater-wide, not live.</div>`;
      } else if (c.type === "location") {
        const l = d.latest_localisation || {};
        body.innerHTML = kv([["Sol", l.sol], ["Position", `${(+l.lat).toFixed(5)}°N ${(+l.lon).toFixed(5)}°E`], ["Elevation", `${(+l.elev_geoid_m).toFixed(1)} m areoid`],
          ["Driven", fmtM(l.dist_total_m)]]) + `<div class="mwai-foot">Historical localisation (NASA/JPL-Caltech MMGIS snapshot), not live.</div>`;
      } else if (c.type === "mission") {
        const cp = d.current_position || {};
        body.innerHTML = kv([["Status", `${esc(d.mission.status)} <span class="mwai-muted">(mock)</span>`], ["Position", esc(cp.name)],
          ["Destination", d.destination ? esc(d.destination.name) : "none"], ["Active route", d.active_route ? esc(d.active_route.route_id) : "none"]]);
      } else if (c.type === "places") {
        body.innerHTML = (d.unvisited_planned_stops && d.unvisited_planned_stops.length ? d.unvisited_planned_stops : d.places || []).slice(0, 5)
          .map((p) => `<div class="mwai-sec"><b>${esc(p.name)}</b> · ${fmtM(p.distance_m)}<div class="mwai-muted">${esc(p.reason || "")}</div></div>`).join("") || "No places within range.";
      }
      card.appendChild(body);
      if (c.actions && c.actions.length) {
        const row = el("div", "mwai-btnrow");
        c.actions.forEach((a) => row.appendChild(this._btn(a)));
        card.appendChild(row);
      }
      return card;
    }
  }

  global.MarswalkAssistant = { mount: (opts) => new Assistant(opts) };
})(window);
