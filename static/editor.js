"use strict";
/* Teksting - proofreading editor. Uses helpers from app.js (h, t, api, toast, ask, fmtTC, ...). */

const FONTS = ["Arial", "Segoe UI", "Calibri", "Verdana", "Tahoma", "Trebuchet MS", "Georgia", "Times New Roman"];

class Editor {
  constructor(id) {
    this.id = id;
    this.cues = [];
    this.sel = -1;
    this.active = -1;
    this.unknown = [];
    this.disagree = [];
    this.hist = [];
    this.future = [];
    this.win = 20;           // seconds visible in the timeline
    this.dirty = false;
    this.alive = true;
    this.caret = 0;
    this.review = false;
    this.playUntil = null;
    this.panel = null;       // "find" | "style" | null
    this.peaks = null;
    this.spellTimers = {};
    this.onKey = this.onKey.bind(this);
    this.onResize = () => { this.layoutOverlay(); this.drawTimeline(true); };
    this.load();
  }

  async load() {
    const v = $("#view");
    v.className = "page editor";
    v.replaceChildren(h("p", { class: "muted loading" }, t("loading")));
    let d;
    try { d = await api("GET", "/api/projects/" + this.id); } catch (e) {
      v.replaceChildren(h("p", { class: "loading" }, t("project_missing"), " ", h("a", { href: "#/" }, t("back_all"))));
      return;
    }
    if (!this.alive) return;
    this.meta = d.meta;
    this.rules = d.rules;
    this.style = { ...d.style };
    this.pauseOnType = d.pause_on_type;
    this.disagree = d.disagree || [];
    this.rescued = d.rescued || [];
    this.autofix = d.autofix || [];
    this.workBase = d.meta.work_seconds || 0;
    this.workAcc = 0;
    this.lastInput = Date.now();
    this.onActivity = () => (this.lastInput = Date.now());
    document.addEventListener("pointerdown", this.onActivity, true);
    document.addEventListener("keydown", this.onActivity, true);
    this.workTimer = setInterval(() => this.tickWork(), 1000);
    this.sugg = new Map();   // cue object -> suggested text from "listen again"
    this.peakRate = d.peak_rate || 50;
    this.cues = d.cues.map((c) => ({ ...c }));
    this.sort();
    this.build();
    document.addEventListener("keydown", this.onKey);
    window.addEventListener("resize", this.onResize);
    this.spellAll();
    fetch(`/api/projects/${this.id}/peaks`).then((r) => r.arrayBuffer()).then((b) => {
      this.peaks = new Uint8Array(b);
      this.drawTimeline(true);
    }).catch(() => {});
    this.loop();
  }

  tickWork() {
    if (!this.alive) return;
    const active = document.visibilityState === "visible" && (Date.now() - this.lastInput < 30000 || (this.video && !this.video.paused));
    if (active) this.workAcc += 1;
    if (this.workEl) {
      const s = Math.round(this.workBase + this.workAcc);
      this.workEl.textContent = t("work_clock", { t: `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` });
      this.workEl.title = t("work_title");
    }
    if (this.workAcc >= 15) this.flushWork();
  }
  flushWork(keepalive = false) {
    const s = this.workAcc;
    if (s < 1) return;
    this.workAcc = 0;
    this.workBase += s;
    api("POST", `/api/projects/${this.id}/worktime`, { seconds: s }, keepalive ? { keepalive: true } : {}).catch(() => { this.workAcc += s; this.workBase -= s; });
  }

  async destroy() {
    clearInterval(this.workTimer);
    this.flushWork(true);
    document.removeEventListener("pointerdown", this.onActivity, true);
    document.removeEventListener("keydown", this.onActivity, true);
    this.alive = false;
    document.removeEventListener("keydown", this.onKey);
    window.removeEventListener("resize", this.onResize);
    if (this.dirty) await this.save(true);
    if (this.video) { this.video.pause(); this.video.removeAttribute("src"); this.video.load(); }
  }

  sort() { this.cues.sort((a, b) => a.start - b.start); }

  // =================================================================== layout
  build() {
    const v = $("#view");
    this.video = h("video", { preload: "auto", playsinline: true, src: `/api/projects/${this.id}/media` });
    this.overlay = h("div", { class: "overlay", "aria-hidden": "true" });
    this.stageMsg = h("div", { class: "stage-msg", hidden: true });
    this.isAudio = /\.(mp3|wav|m4a|aac|flac|ogg)$/i.test(this.meta.name);
    this.stage = h("div", { class: "stage" + (this.isAudio ? " audio" : "") }, this.video,
      this.isAudio ? h("div", { class: "audio-note" }, t("audio_only")) : null, this.overlay, this.stageMsg);
    this.video.addEventListener("error", () => { this.stageMsg.hidden = false; this.stageMsg.textContent = t("video_unplayable"); });
    this.video.addEventListener("play", () => (this.playBtn.innerHTML = ICON.pause));
    this.video.addEventListener("pause", () => (this.playBtn.innerHTML = ICON.play));
    this.video.addEventListener("loadedmetadata", () => { this.layoutOverlay(); this.drawTimeline(true); });

    this.playBtn = h("button", { class: "icon-btn play", type: "button", "aria-label": t("play_pause"), "data-tip": t("tip_play"), onclick: () => this.toggle(), html: ICON.play });
    this.clock = h("span", { class: "clock" });
    const speed = h("select", { class: "speed", "aria-label": t("speed"), "data-tip": t("tip_speed"), onchange: (e) => (this.video.playbackRate = Number(e.target.value)) },
      [0.75, 1, 1.25, 1.5].map((r) => h("option", { value: r, selected: r === 1 }, num(r, 2) + "×")));
    this.undoBtn = h("button", { class: "icon-btn undo", type: "button", disabled: true, onclick: () => this.undo(), "aria-label": t("undo"), "data-tip": t("tip_undo"), html: ICON.undo });
    this.redoBtn = h("button", { class: "icon-btn undo", type: "button", disabled: true, onclick: () => this.redo(), "aria-label": t("redo"), "data-tip": t("tip_redo"), html: ICON.redo });
    const transport = h("div", { class: "transport" },
      h("button", { class: "icon-btn", type: "button", "aria-label": t("seek_back"), "data-tip": t("tip_back5"), onclick: () => this.seek(this.video.currentTime - 5), html: ICON.rew }),
      this.playBtn,
      h("button", { class: "icon-btn", type: "button", "aria-label": t("seek_fwd"), "data-tip": t("tip_fwd5"), onclick: () => this.seek(this.video.currentTime + 5), html: ICON.fwd }),
      this.clock, speed,
      h("span", { class: "grow" }),
      h("button", { class: "btn small quiet", type: "button", "data-tip": t("tip_new_cue"), onclick: () => this.insertAtPlayhead() }, t("new_cue_here")),
      this.undoBtn, this.redoBtn);

    this.wave = h("canvas", { class: "wave", "aria-hidden": "true" });
    this.tlLayer = h("div", { class: "tl-layer" });
    this.tipDrag = t("tip_drag").replace(/"/g, "&quot;");
    this.tl = h("div", { class: "timeline", role: "slider", "aria-label": t("timeline"), tabindex: "-1" }, this.wave, this.tlLayer);
    this.tl.addEventListener("pointerdown", (e) => this.tlDown(e));
    this.tl.addEventListener("wheel", (e) => this.tlWheel(e), { passive: false });

    // header
    this.status = h("span", { class: "savestate", "aria-live": "polite" }, t("saved"));
    this.workEl = h("span", { class: "workclock" });
    this.reviewBtn = h("button", { class: "btn small primary", type: "button", onclick: () => this.startReview(), "data-tip": t("tip_review") });
    this.exportState = h("span", { class: "exportstate", "aria-live": "polite" });
    const exportMenu = this.menu(t("export"), [
      [t("download_srt"), () => this.download(), t("tip_download_srt")],
      [t("save_next_to_video"), () => this.saveNextToVideo(), t("tip_save_srt")],
      [t("burn_video"), () => this.burn(), t("tip_burn")],
    ], "btn small");
    const moreMenu = this.menu("⋯", [
      [t("shortcuts"), () => this.showKeys()],
      [t("resegment"), () => this.resegment(), t("tip_resegment")],
    ], "btn small quiet", t("more"));
    // the rest of the queue keeps transcribing while you proofread: show how far it is
    this.queueEl = h("a", { class: "ed-queue", href: "#/", hidden: true, "data-tip": t("tip_queue") });
    const head = h("div", { class: "ed-head" },
      h("a", { class: "back", href: "#/", html: ICON.back + `<span>${t("back_all")}</span>` }),
      h("h1", { title: this.meta.path }, this.meta.name),
      this.status, this.workEl, this.exportState, this.queueEl,
      h("div", { class: "ed-actions" },
        this.reviewBtn,
        h("button", { class: "btn small", type: "button", onclick: () => this.togglePanel("find"), "data-tip": t("tip_find") }, t("find_replace")),
        h("button", { class: "btn small", type: "button", "data-tip": t("tip_style"), onclick: () => this.togglePanel("style") }, t("style")),
        exportMenu, moreMenu));

    // right column
    this.reviewBar = h("div", { class: "reviewbar", hidden: true });
    this.panelBox = h("div", { class: "panelbox" });
    this.list = h("div", { class: "cuelist", role: "list" });
    const left = h("div", { class: "ed-left" }, this.stage, transport, this.tl);
    const right = h("div", { class: "ed-right" }, this.reviewBar, this.panelBox, this.list);
    v.replaceChildren(head, h("div", { class: "ed-body" }, left, right));
    this.renderList();
    if (this.cues.length) this.select(0, { seek: false, scroll: false });
    this.applyStyle();
    this.sync(S.state);
  }

  menu(label, items, cls, aria) {
    const pop = h("div", { class: "menu-pop", role: "menu", hidden: true });
    const btn = h("button", { class: cls + " menu-btn", type: "button", "aria-haspopup": "menu", "aria-label": aria || label,
      onclick: (e) => { e.stopPropagation(); const open = pop.hidden; $$(".menu-pop").forEach((p) => (p.hidden = true)); pop.hidden = !open; } }, label);
    items.forEach(([text, fn, tip]) => pop.append(h("button", { type: "button", role: "menuitem", "data-tip": tip || null, onclick: () => { pop.hidden = true; fn(); } }, text)));
    document.addEventListener("click", () => (pop.hidden = true));
    return h("div", { class: "menu" }, btn, pop);
  }

  // sync with server state (export progress)
  syncQueue(st) {
    if (!this.queueEl) return;
    const cur = st.jobs.find((j) => RUNNING.includes(j.status) && j.id !== this.id);
    const waiting = st.jobs.filter((j) => j.status === "queued").length;
    let text = "";
    if (cur) {
      const pct = Math.floor(live(cur).p * 100);
      text = cur.status === "downloading" ? t("eq_downloading", { pct }) : t("eq_working", { name: cur.name, pct });
      if (waiting) text += " · " + t("eq_more", { n: waiting });
    } else if (waiting && st.paused) text = t("eq_paused");
    if (this.queueEl.textContent !== text) this.queueEl.textContent = text;
    this.queueEl.hidden = !text;
    // a video in the queue just finished: offer to open it
    if (this.lastRunning && (!cur || cur.id !== this.lastRunning)) {
      const done = st.jobs.find((j) => j.id === this.lastRunning && j.status === "done");
      if (done) toast(t("eq_ready", { name: done.name }), { label: t("eq_open"), run: () => (location.hash = "#/edit/" + done.id) });
    }
    this.lastRunning = cur ? cur.id : null;
  }

  sync(st) {
    if (!st) return;
    this.syncQueue(st);
    if (!this.exportState) return;
    const m = st.jobs.find((j) => j.id === this.id);
    const x = m && m.export;
    this.exportState.replaceChildren();
    if (!x) return;
    if (x.status === "queued") this.exportState.append(t("x_queued"));
    else if (x.status === "running") this.exportState.append(t("x_running", { pct: Math.floor((x.progress || 0) * 100) }), " ",
      h("button", { class: "link", type: "button", onclick: () => api("POST", `/api/projects/${this.id}/burn/cancel`) }, t("stop")));
    else if (x.status === "done") {
      if (this.lastExportAt !== x.at && this.lastExportAt !== undefined) toast(t("x_done_toast"), { label: t("show_in_folder"), run: () => api("POST", "/api/open-folder", { path: x.path }) });
      this.exportState.append(h("button", { class: "link", type: "button", onclick: () => api("POST", "/api/open-folder", { path: x.path }) }, t("x_done")));
    } else if (x.status === "error") this.exportState.append(h("span", { class: "bad" }, t("x_error", { msg: x.message || "" })));
    this.lastExportAt = x.at || null;
  }

  // ================================================================= analysis
  disagreeFor(i) {
    const c = this.cues[i];
    return this.disagree.filter((d) => d.s < c.end && d.e > c.start);
  }
  flags(i) {
    const c = this.cues[i], R = this.rules, out = [];
    const lines = c.text.split("\n");
    const chars = lines.join("").length;
    const dur = c.end - c.start;
    if (!c.text.trim()) out.push(["bad", t("f_empty")]);
    if (lines.length > 2) out.push(["bad", t("f_lines")]);
    const longest = Math.max(...lines.map((l) => l.length));
    if (longest > R.max_chars) out.push(["bad", t("f_long", { n: longest, max: R.max_chars })]);
    const nx = this.cues[i + 1];
    if (nx && c.end > nx.start + 0.001) out.push(["bad", t("f_overlap")]);
    if (dur > 0 && chars / dur > R.max_cps + 0.5) out.push(["warn", t("f_fast", { n: num(chars / dur, 0) })]);
    if (dur < R.min_dur - 0.01) out.push(["warn", t("f_short")]);
    if (dur > R.max_dur + 0.01) out.push(["warn", t("f_slow")]);
    const pv = this.cues[i - 1];
    if (pv && c.start - pv.end > 3.3) out.push(["info", t("f_gap", { n: num(c.start - pv.end, 0) })]);
    if (this.rescued.some((r) => r.s < c.end && r.e > c.start)) out.push(["warn", t("f_rescued")]);
    return out;
  }
  issueCount(i) { return this.flags(i).length + (this.unknown[i] || []).length + this.disagreeFor(i).length + (this.sugg.has(this.cues[i]) ? 1 : 0); }
  needsReview(i) { return !this.cues[i].checked && this.issueCount(i) > 0; }
  reviewLeft() { return this.cues.reduce((n, _, i) => n + (this.needsReview(i) ? 1 : 0), 0); }

  // ===================================================================== list
  renderList() {
    this.list.replaceChildren(...this.cues.map((c, i) => this.row(i)));
    this.updateCounts();
  }

  row(i) {
    const ta = h("textarea", { rows: 2, spellcheck: "false" });
    ta.value = this.cues[i].text;
    const el = h("div", { class: "cue", role: "listitem" },
      h("div", { class: "cue-gutter", onclick: () => this.select(+el.dataset.i, { focus: true }) },
        h("span", { class: "num" }), h("span", { class: "time" })),
      h("div", { class: "cue-body" }, ta, h("div", { class: "issues" }), h("div", { class: "tools" })),
      h("span", { class: "okmark", title: t("checked"), "aria-hidden": "true" }));
    el._ta = ta;
    ta.addEventListener("focus", () => { el._orig = ta.value; if (this.sel !== +el.dataset.i) this.select(+el.dataset.i, { focus: false, scroll: false }); });
    ta.addEventListener("blur", () => { if (el._orig != null && el._orig !== ta.value) this.offerGlossary(el._orig, ta.value); el._orig = null; });
    ta.addEventListener("input", () => {
      const k = +el.dataset.i;
      if (this.pauseOnType && !this.video.paused) { this.video.pause(); this.playUntil = null; }
      this.cues[k].text = ta.value;
      this.cues[k].checked = false;
      this.caret = ta.selectionStart;
      this.refreshRow(k);
      this.spellLater(k);
      this.changed(false);
    });
    for (const ev of ["keyup", "click", "select"]) ta.addEventListener(ev, () => (this.caret = ta.selectionStart));
    this.fillRow(el, i);
    return el;
  }

  fillRow(el, i) {
    const c = this.cues[i];
    el.dataset.i = i;
    el._ta.setAttribute("aria-label", t("cue_text", { n: i + 1 }));
    $(".num", el).textContent = i + 1;
    $(".time", el).textContent = fmtTC(c.start, 0).replace(/^0:/, "");
    const chips = [];
    for (const [lvl, label] of this.flags(i)) chips.push(h("span", { class: "chip " + lvl }, label));
    for (const w of this.unknown[i] || []) chips.push(h("button", { class: "chip warn word", type: "button", "data-tip": t("tip_word", { w }), onclick: () => this.addWord(w) }, w, h("span", { class: "plus" }, " +")));
    for (const d of this.disagreeFor(i)) {
      const canUse = d.a && c.text.includes(d.a);
      chips.push(h("span", { class: "chip alt", "data-tip": t("tip_alt", { a: d.a || "–" }) },
        t("alt_heard", { b: d.b || t("alt_nothing") }),
        canUse ? h("button", { class: "chip-act", type: "button", onclick: () => this.useAlt(i, d) }, t("alt_use")) : null));
    }
    for (const f of this.autofix.filter((f) => f.t >= c.start - 0.05 && f.t < c.end)) {
      chips.push(h("span", { class: "chip auto", "data-tip": t("autofix_title") }, t("autofix", { a: f.a, b: f.b }),
        h("button", { class: "chip-act", type: "button", onclick: () => this.revertFix(+el.dataset.i, f) }, t("undo"))));
    }
    const sg = this.sugg.get(c);
    if (sg != null) {
      chips.unshift(h("span", { class: "chip sugg" + (sg === "…" ? " busy" : "") },
        sg === "…" ? t("listening") : (sg ? t("suggestion", { s: sg.replace(/\n/g, " ") }) : t("heard_nothing")),
        sg && sg !== "…" ? h("button", { class: "chip-act", type: "button", onclick: () => this.useSuggestion(+el.dataset.i) }, t("alt_use")) : null,
        sg !== "…" ? h("button", { class: "chip-act", type: "button", onclick: () => { this.sugg.delete(c); this.refreshRow(+el.dataset.i); } }, t("dismiss")) : null));
    }
    $(".issues", el).replaceChildren(...chips);
    el.classList.toggle("flagged", chips.length > 0);
    el.classList.toggle("checked", !!c.checked);
    el.classList.toggle("pos-top", c.pos === "top");
    if (i === this.sel) this.fillTools(el, i);
  }

  refreshRow(i) {
    for (const k of [i - 1, i, i + 1]) {
      const el = this.list.children[k];
      if (el && this.cues[k]) this.fillRow(el, k);
    }
    this.updateCounts();
    if (i === this.active) this.showOverlay(true);
    this.drawTimeline(true);
  }

  updateCounts() {
    const n = this.reviewLeft();
    this.reviewBtn.textContent = n ? t("review_n", { n }) : t("review_none");
    this.reviewBtn.classList.toggle("primary", n > 0);
    if (this.review) this.renderReviewBar();
  }

  fillTools(el, i) {
    const c = this.cues[i];
    const timeField = (key) => {
      const inp = h("input", { class: "tc-input", value: fmtTC(c[key], 2), "aria-label": t(key), spellcheck: "false", "data-tip": t("time_hint") });
      inp.addEventListener("change", () => {
        const v = parseTC(inp.value);
        if (v == null) { inp.value = fmtTC(c[key], 2); return; }
        this.setTime(i, key, v);
      });
      inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); inp.dispatchEvent(new Event("change")); }
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          this.setTime(i, key, c[key] + (e.key === "ArrowUp" ? 1 : -1) * (e.shiftKey ? 1 : 0.1));
          const again = $(`.tools .tc-input[data-k="${key}"]`, this.list.children[i]);
          if (again) again.focus();
        }
      });
      inp.dataset.k = key;
      return inp;
    };
    $(".tools", el).replaceChildren(
      h("div", { class: "trow" },
        timeField("start"), h("span", { class: "muted" }, "–"), timeField("end"),
        h("button", { class: "link small", type: "button", "data-tip": t("tip_start_here"), onclick: () => this.setTime(i, "start", this.video.currentTime) }, t("start_here")),
        h("button", { class: "link small", type: "button", "data-tip": t("tip_end_here"), onclick: () => this.setTime(i, "end", this.video.currentTime) }, t("end_here"))),
      h("div", { class: "trow" },
        h("button", { class: "btn tiny", type: "button", "data-tip": t("tip_wrap"), onclick: () => this.wrap(i) }, t("wrap")),
        h("button", { class: "btn tiny", type: "button", "data-tip": t("tip_split"), onclick: () => this.split(i) }, t("split")),
        h("button", { class: "btn tiny", type: "button", "data-tip": t("tip_merge"), disabled: i >= this.cues.length - 1, onclick: () => this.merge(i) }, t("merge")),
        h("button", { class: "btn tiny", type: "button", "data-tip": t("tip_pos"), onclick: () => this.togglePos(i) }, c.pos === "top" ? t("pos_bottom") : t("pos_top")),
        h("button", { class: "btn tiny", type: "button", "data-tip": t("listen_title"), onclick: () => this.listen(i) }, t("listen")),
        h("button", { class: "btn tiny quiet danger-text", type: "button", onclick: () => this.remove(i) }, t("delete")),
        h("span", { class: "grow" }),
        h("button", { class: "icon-btn tiny undo-inline", type: "button", "aria-label": t("undo"), "data-tip": t("tip_undo"), disabled: !this.hist.length, onclick: () => this.undo(), html: ICON.undo }),
        h("button", { class: "btn tiny " + (c.checked ? "" : "approve"), type: "button", "data-tip": t("tip_approve"), onclick: () => this.approve(i) }, c.checked ? t("uncheck") : t("approve"))));
  }

  select(i, { seek = true, focus = false, scroll = true } = {}) {
    if (i < 0 || i >= this.cues.length) return;
    const prev = this.list.children[this.sel];
    if (prev) { prev.classList.remove("sel"); $(".tools", prev).replaceChildren(); }
    this.sel = i;
    const el = this.list.children[i];
    el.classList.add("sel");
    this.fillTools(el, i);
    if (seek && this.video.paused) this.seek(this.cues[i].start + 0.001);
    if (scroll) el.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    if (focus) el._ta.focus({ preventScroll: true });
    this.drawTimeline(true);
  }

  // =============================================================== operations
  snapshot() {
    this.hist.push(JSON.stringify({ cues: this.cues, disagree: this.disagree, sel: this.sel }));
    if (this.hist.length > 200) this.hist.shift();
    this.future = [];
    this.updateUndo();
  }
  updateUndo() {
    this.undoBtn.disabled = !this.hist.length;
    this.redoBtn.disabled = !this.future.length;
    const u = $(".tools .undo-inline", this.list);
    if (u) u.disabled = !this.hist.length;
  }
  restore(s) {
    const o = JSON.parse(s);
    this.cues = o.cues;
    this.disagree = o.disagree;
    this.spellAll();
    this.renderList();
    const i = Math.min(Math.max(o.sel ?? this.sel, 0), this.cues.length - 1);
    if (i >= 0) this.select(i, { seek: false, focus: true });
    this.changed();
  }
  undo() {
    const s = this.hist.pop();
    if (!s) { toast(t("nothing_to_undo")); return; }
    this.future.push(JSON.stringify({ cues: this.cues, disagree: this.disagree, sel: this.sel }));
    this.restore(s);
    this.updateUndo();
    toast(t("undone"), { label: t("redo"), run: () => this.redo() });
  }
  redo() {
    const s = this.future.pop();
    if (!s) return;
    this.hist.push(JSON.stringify({ cues: this.cues, disagree: this.disagree, sel: this.sel }));
    this.restore(s);
    this.updateUndo();
  }
  undoable(msg) { toast(msg, { label: t("undo"), run: () => this.undo() }); }
  changed(structural = true) {
    this.dirty = true;
    this.status.textContent = t("saving");
    this.status.className = "savestate busy";
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.save(), 800);
    if (structural) this.drawTimeline(true);
  }
  async save(keepalive = false) {
    clearTimeout(this.saveTimer);
    this.dirty = false;
    try {
      await api("PUT", `/api/projects/${this.id}/cues`, { cues: this.cues }, keepalive ? { keepalive: true } : {});
      if (!this.alive) return;
      this.status.textContent = t("saved_at", { t: new Date().toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" }) });
      this.status.className = "savestate";
    } catch (e) {
      this.dirty = true;
      if (!this.alive) return;
      this.status.textContent = t("save_failed");
      this.status.className = "savestate bad";
      this.saveTimer = setTimeout(() => this.save(), 3000);
    }
  }
  setText(i, text) {
    this.cues[i].text = text;
    const el = this.list.children[i];
    if (el) el._ta.value = text;
  }

  setTime(i, key, v) {
    const c = this.cues[i];
    v = Math.max(0, Math.round(v * 1000) / 1000);
    if (key === "start") v = Math.min(v, c.end - 0.2);
    else v = Math.max(v, c.start + 0.2);
    if (Math.abs(c[key] - v) < 0.0005) { this.fillTools(this.list.children[i], i); return; }
    this.snapshot();
    c[key] = v;
    c.checked = false;
    this.refreshRow(i);
    this.changed();
  }
  approve(i) {
    const c = this.cues[i];
    c.checked = !c.checked;
    this.refreshRow(i);
    this.changed(false);
    if (c.checked && this.review) this.reviewNext();
  }
  togglePos(i) {
    this.snapshot();
    const c = this.cues[i];
    if (c.pos === "top") delete c.pos; else c.pos = "top";
    this.refreshRow(i);
    this.showOverlay(true);
    this.changed();
  }
  async wrap(i) {
    const r = await api("POST", "/api/wrap", { text: this.cues[i].text });
    if (r.text === this.cues[i].text) return;
    this.snapshot();
    this.setText(i, r.text);
    this.refreshRow(i);
    this.changed();
  }
  async split(i) {
    const c = this.cues[i];
    const flat = c.text;
    let pos = this.caret;
    if (!(pos > 0 && pos < flat.length)) {
      pos = flat.includes("\n") ? flat.indexOf("\n") : Math.floor(flat.length / 2);
      const sp = flat.indexOf(" ", pos);
      if (!flat.includes("\n") && sp > 0) pos = sp;
    }
    const a = flat.slice(0, pos).replace(/\s+/g, " ").trim();
    const b = flat.slice(pos).replace(/\s+/g, " ").trim();
    if (!a || !b) { toast(t("split_hint")); return; }
    const tNow = this.video.currentTime;
    let mid = c.start + (c.end - c.start) * (a.length / (a.length + b.length));
    if (tNow > c.start + 0.3 && tNow < c.end - 0.3) mid = tNow;
    this.snapshot();
    const [ra, rb] = await Promise.all([api("POST", "/api/wrap", { text: a }), api("POST", "/api/wrap", { text: b })]);
    const extra = c.pos ? { pos: c.pos } : {};
    this.cues.splice(i, 1, { start: c.start, end: Math.max(c.start + 0.2, mid - 0.04), text: ra.text, ...extra },
      { start: Math.min(c.end - 0.2, mid + 0.04), end: c.end, text: rb.text, ...extra });
    this.unknown.splice(i, 1, [], []);
    this.renderList();
    this.spellLater(i); this.spellLater(i + 1);
    this.select(i + 1, { focus: true, seek: false });
    this.changed();
    this.undoable(t("split_done"));
  }
  async merge(i) {
    const a = this.cues[i], b = this.cues[i + 1];
    if (!b) return;
    this.snapshot();
    const r = await api("POST", "/api/wrap", { text: (a.text + " " + b.text).replace(/\s+/g, " ").trim() });
    this.cues.splice(i, 2, { start: a.start, end: b.end, text: r.text, ...(a.pos ? { pos: a.pos } : {}) });
    this.unknown.splice(i, 2, []);
    this.renderList();
    this.spellLater(i);
    this.select(i, { focus: true, seek: false });
    this.changed();
    this.undoable(t("merge_done"));
  }
  remove(i) {
    this.snapshot();
    this.cues.splice(i, 1);
    this.unknown.splice(i, 1);
    this.renderList();
    if (this.cues.length) this.select(Math.min(i, this.cues.length - 1), { seek: false });
    this.changed();
    this.undoable(t("deleted"));
  }
  insertAtPlayhead() {
    const tm = Math.round(this.video.currentTime * 1000) / 1000;
    if (this.cues.some((c) => tm >= c.start && tm < c.end)) { toast(t("insert_occupied")); return; }
    const next = this.cues.find((c) => c.start > tm);
    const end = Math.min(tm + 2, next ? next.start - 0.08 : tm + 2);
    if (end - tm < 0.5) { toast(t("insert_no_room")); return; }
    this.snapshot();
    const cue = { start: tm, end: Math.round(end * 1000) / 1000, text: "" };
    this.cues.push(cue);
    this.sort();
    const i = this.cues.indexOf(cue);
    this.unknown.splice(i, 0, []);
    this.renderList();
    this.select(i, { focus: true, seek: false });
    this.changed();
    this.listen(i);
  }
  async listen(i) {
    const c = this.cues[i];
    if (!c) return;
    this.sugg.set(c, "…");
    this.refreshRow(i);
    try {
      const r = await api("POST", `/api/projects/${this.id}/listen`, { start: c.start, end: c.end });
      this.sugg.set(c, r.text || "");
    } catch (e) {
      this.sugg.delete(c);
      toast(t("listen_failed") + " " + e.message);
    }
    const k = this.cues.indexOf(c);
    if (k >= 0) this.refreshRow(k);
  }
  revertFix(i, f) {
    const c = this.cues[i];
    this.snapshot();
    if (c.text.includes(f.b)) this.setText(i, c.text.replace(f.b, f.a));
    this.autofix = this.autofix.filter((x) => x !== f);
    c.checked = false;
    this.refreshRow(i);
    this.spellLater(i);
    this.changed();
  }
  useSuggestion(i) {
    const c = this.cues[i];
    const s = this.sugg.get(c);
    if (!s) return;
    this.snapshot();
    this.setText(i, s);
    this.sugg.delete(c);
    c.checked = false;
    this.refreshRow(i);
    this.spellLater(i);
    this.changed();
    this.undoable(t("suggestion_used"));
  }
  useAlt(i, d) {
    this.snapshot();
    let text = this.cues[i].text.replace(d.a, d.b).replace(/ {2,}/g, " ").replace(/ +\n/g, "\n").trim();
    this.setText(i, text);
    this.disagree = this.disagree.filter((x) => x !== d);
    this.cues[i].checked = false;
    this.refreshRow(i);
    this.spellLater(i);
    this.changed();
    this.undoable(t("alt_used", { b: d.b || "–" }));
  }
  // A word corrected to something similar? Offer to put the new spelling in the glossary.
  offerGlossary(before, after) {
    const tok = (s) => s.match(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu) || [];
    let A = tok(before), B = tok(after);
    while (A.length && B.length && A[0] === B[0]) { A.shift(); B.shift(); }
    while (A.length && B.length && A[A.length - 1] === B[B.length - 1]) { A.pop(); B.pop(); }
    if (!A.length || A.length !== B.length || A.length > 3) return;
    const known = new Set((S.state?.glossary || "").split(/\s+/).map((w) => w.toLowerCase()).filter(Boolean));
    this.glossAsked = this.glossAsked || new Set();
    for (let k = 0; k < A.length; k++) {
      const a = A[k], b = B[k];
      if (a.toLowerCase() === b.toLowerCase() || b.length < 3 || known.has(b.toLowerCase()) || this.glossAsked.has(b.toLowerCase())) continue;
      if (similarity(a.toLowerCase(), b.toLowerCase()) < 0.5) continue;
      this.glossAsked.add(b.toLowerCase());
      toast(t("gloss_q", { w: b }), [
        { label: t("gloss_yes"), run: () => this.addWord(b) },
        { label: t("gloss_no"), run: () => {} },
      ], 12000);
      return;
    }
  }
  async addWord(w) {
    const r = await api("POST", "/api/glossary", { word: w });
    if (S.state) S.state.glossary = r.glossary;
    toast(t("word_added", { w }));
    this.spellAll();
  }
  async resegment() {
    if (!(await ask(t("reseg_q"), t("reseg_body"), t("reseg_ok"), { danger: true }))) return;
    this.snapshot();
    const r = await api("POST", `/api/projects/${this.id}/resegment`);
    this.cues = r.cues;
    this.autofix = r.autofix || [];
    this.spellAll();
    this.renderList();
    this.select(0, { seek: false });
    this.changed();
    this.undoable(t("reseg_done"));
  }

  // ================================================================== review
  startReview() {
    if (!this.reviewLeft()) { toast(t("review_nothing")); return; }
    this.review = true;
    this.closePanel();
    this.reviewBar.hidden = false;
    const from = this.sel >= 0 && this.needsReview(this.sel) ? this.sel : this.nextFlagged(this.sel);
    this.goReview(from);
  }
  stopReview() {
    this.review = false;
    this.reviewBar.hidden = true;
    this.playUntil = null;
    this.video.pause();
    if (this.sel >= 0) this.fillTools(this.list.children[this.sel], this.sel);
  }
  nextFlagged(from) {
    const n = this.cues.length;
    for (let k = 1; k <= n; k++) {
      const j = (from + k + n) % n;
      if (this.needsReview(j)) return j;
    }
    return -1;
  }
  reviewNext() {
    const j = this.nextFlagged(this.sel);
    if (j < 0) { this.stopReview(); toast(t("review_done")); return; }
    this.goReview(j);
  }
  goReview(i) {
    if (i < 0) { this.stopReview(); return; }
    this.select(i, { seek: false, focus: true });
    this.playCue(i);
    this.renderReviewBar();
  }
  playCue(i) {
    const c = this.cues[i];
    this.playUntil = c.end + 0.15;
    this.video.currentTime = Math.max(0, c.start - 0.25);
    this.video.play().catch(() => {});
  }
  renderReviewBar() {
    const n = this.reviewLeft();
    this.reviewBar.replaceChildren(
      h("div", { class: "rb-text" }, h("strong", null, t("review_left", { n })), h("span", { class: "muted" }, t("review_help"))),
      h("div", { class: "rb-actions" },
        h("button", { class: "btn tiny", type: "button", "data-tip": t("tip_replay"), onclick: () => this.playCue(this.sel) }, t("replay")),
        h("button", { class: "btn tiny", type: "button", "data-tip": t("tip_skip"), onclick: () => this.reviewNext() }, t("skip")),
        h("button", { class: "btn tiny quiet", type: "button", onclick: () => this.stopReview() }, t("review_stop"))));
  }

  // ============================================================ find/replace
  togglePanel(name) { if (this.panel === name) this.closePanel(); else this.openPanel(name); }
  closePanel() {
    this.panel = null;
    this.panelBox.replaceChildren();
    this.list.hidden = false;
    this.showOverlay(true);
  }
  openPanel(name) {
    if (this.review && name) this.stopReview();
    this.panel = name;
    if (name === "find") this.buildFind();
    if (name === "style") this.buildStyle();
  }
  findRegex() {
    const q = this.fq.value;
    if (!q) return null;
    const esc = q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(this.fw.checked ? `(?<![\\p{L}\\p{N}])${esc}(?![\\p{L}\\p{N}])` : esc, "giu");
  }
  findAll() {
    const re = this.findRegex();
    const out = [];
    if (!re) return out;
    this.cues.forEach((c, i) => { for (const m of c.text.matchAll(re)) out.push({ i, idx: m.index, len: m[0].length }); });
    return out;
  }
  buildFind() {
    this.list.hidden = false;
    this.fq = h("input", { type: "search", placeholder: t("find_ph"), "aria-label": t("find") });
    this.fr = h("input", { type: "text", placeholder: t("replace_ph"), "aria-label": t("replace") });
    this.fw = h("input", { type: "checkbox", checked: true });
    this.fcount = h("span", { class: "muted fcount" });
    this.fpos = -1;
    const update = () => { const n = this.findAll().length; this.fcount.textContent = this.fq.value ? t("find_n", { n }) : ""; this.fpos = -1; };
    this.fq.addEventListener("input", update);
    this.fw.addEventListener("change", update);
    this.fq.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); this.findStep(e.shiftKey ? -1 : 1); } if (e.key === "Escape") this.closePanel(); });
    this.panelBox.replaceChildren(h("div", { class: "panel find" },
      h("div", { class: "frow" }, this.fq, this.fcount),
      h("div", { class: "frow" }, this.fr),
      h("div", { class: "frow" },
        h("label", { class: "check small" }, this.fw, h("span", null, t("whole_words"))),
        h("span", { class: "grow" }),
        h("button", { class: "btn tiny", type: "button", onclick: () => this.findStep(-1) }, t("prev")),
        h("button", { class: "btn tiny", type: "button", onclick: () => this.findStep(1) }, t("next")),
        h("button", { class: "btn tiny", type: "button", onclick: () => this.replaceOne() }, t("replace_one")),
        h("button", { class: "btn tiny primary", type: "button", onclick: () => this.replaceAll() }, t("replace_all")),
        h("button", { class: "btn tiny quiet", type: "button", onclick: () => this.closePanel() }, t("close")))));
    const sel = window.getSelection().toString().trim();
    if (sel && sel.length < 40) { this.fq.value = sel; update(); }
    this.fq.focus();
  }
  findStep(dir) {
    const all = this.findAll();
    if (!all.length) return;
    this.fpos = (this.fpos + dir + all.length) % all.length;
    const m = all[this.fpos];
    this.select(m.i, { focus: false });
    const ta = this.list.children[m.i]._ta;
    ta.focus({ preventScroll: true });
    ta.setSelectionRange(m.idx, m.idx + m.len);
    this.fcount.textContent = t("find_k_of_n", { k: this.fpos + 1, n: all.length });
  }
  replaceOne() {
    const all = this.findAll();
    if (!all.length) return;
    if (this.fpos < 0 || this.fpos >= all.length) { this.findStep(1); return; }
    const m = all[this.fpos];
    this.snapshot();
    const c = this.cues[m.i];
    this.setText(m.i, c.text.slice(0, m.idx) + this.fr.value + c.text.slice(m.idx + m.len));
    c.checked = false;
    this.refreshRow(m.i);
    this.spellLater(m.i);
    this.changed();
    this.fpos -= 1;
    this.findStep(1);
  }
  replaceAll() {
    const re = this.findRegex();
    if (!re) return;
    const n = this.findAll().length;
    if (!n) { toast(t("find_none")); return; }
    this.snapshot();
    this.cues.forEach((c, i) => {
      const nt = c.text.replace(re, this.fr.value);
      if (nt !== c.text) { this.setText(i, nt); c.checked = false; }
    });
    this.spellAll();
    $$(".cue", this.list).forEach((el) => this.fillRow(el, +el.dataset.i));
    this.updateCounts();
    this.changed();
    this.fcount.textContent = t("find_n", { n: 0 });
    this.undoable(n === 1 ? t("replaced_1") : t("replaced_n", { n }));
  }

  // =================================================================== style
  buildStyle() {
    const st = this.style;
    this.list.hidden = true;
    const set = (k, v) => { st[k] = v; this.applyStyle(); this.saveStyleLater(); };
    const range = (k, min, max, step, fmt) => {
      const out = h("output", null, fmt(st[k]));
      const inp = h("input", { type: "range", min, max, step, value: st[k], "aria-label": t("st_" + k) });
      inp.addEventListener("input", () => { set(k, Number(inp.value)); out.textContent = fmt(Number(inp.value)); });
      return h("label", { class: "srow" }, h("span", null, t("st_" + k)), inp, out);
    };
    const check = (k) => {
      const inp = h("input", { type: "checkbox", checked: !!st[k] });
      inp.addEventListener("change", () => set(k, inp.checked));
      return h("label", { class: "check small" }, inp, h("span", null, t("st_" + k)));
    };
    const font = h("select", { "aria-label": t("st_font") }, FONTS.map((f) => h("option", { value: f, selected: st.font === f }, f)));
    font.addEventListener("change", () => set("font", font.value));
    const color = h("input", { type: "color", value: st.color, "aria-label": t("st_color") });
    color.addEventListener("input", () => set("color", color.value));
    const posSeg = h("div", { class: "seg", role: "group", "aria-label": t("st_position") },
      ["bottom", "top"].map((p) => h("button", { type: "button", class: st.position === p ? "on" : "", onclick: (e) => {
        set("position", p); $$("button", posSeg).forEach((b) => b.classList.toggle("on", b === e.currentTarget)); } }, t("pos_" + p + "_short"))));
    this.panelBox.replaceChildren(h("div", { class: "panel style" },
      h("div", { class: "panel-head" }, h("h2", null, t("style_title")),
        h("button", { class: "btn small primary", type: "button", onclick: () => this.closePanel() }, t("done"))),
      h("p", { class: "muted small" }, t("style_help")),
      h("label", { class: "srow" }, h("span", null, t("st_font")), font),
      range("size", 2.5, 8, 0.1, (v) => num(v) + " %"),
      h("div", { class: "srow" }, h("span", null, t("st_color")), color, check("bold")),
      h("div", { class: "srow" }, h("span", null, t("st_box_label")), check("box"), check("outline")),
      range("box_opacity", 0.2, 1, 0.05, (v) => Math.round(v * 100) + " %"),
      h("div", { class: "srow" }, h("span", null, t("st_position")), posSeg),
      range("margin", 2, 20, 0.5, (v) => num(v) + " %"),
      h("p", { class: "muted small" }, t("style_top_hint")),
      h("button", { class: "link small", type: "button", onclick: () => { Object.assign(st, STYLE_DEFAULT); this.applyStyle(); this.saveStyleLater(); this.buildStyle(); } }, t("style_reset"))));
    this.showOverlay(true);
  }
  saveStyleLater() {
    clearTimeout(this.styleTimer);
    this.styleTimer = setTimeout(() => saveSettings({ style: this.style }).catch(() => {}), 500);
  }
  videoBox() {
    const W = this.stage.clientWidth, H = this.stage.clientHeight;
    const vw = this.video.videoWidth, vh = this.video.videoHeight;
    if (!vw || !vh || this.isAudio) return { x: 0, y: 0, w: W, h: H };
    const s = Math.min(W / vw, H / vh);
    return { x: (W - vw * s) / 2, y: (H - vh * s) / 2, w: vw * s, h: vh * s };
  }
  applyStyle() {
    const st = this.style;
    const o = this.overlay;
    o.style.setProperty("--sub-font", `"${st.font}", Arial, sans-serif`);
    o.style.setProperty("--sub-weight", st.bold ? 700 : 400);
    o.style.setProperty("--sub-color", st.color);
    o.style.setProperty("--sub-bg", st.box ? `rgba(0,0,0,${st.box_opacity})` : "transparent");
    o.style.setProperty("--sub-shadow", st.outline ? "0 0 2px #000,0 0 2px #000,1px 1px 1px #000,-1px -1px 1px #000,1px -1px 1px #000,-1px 1px 1px #000" : "none");
    this.layoutOverlay();
  }
  layoutOverlay() {
    if (!this.overlay) return;
    const b = this.videoBox();
    const st = this.style;
    const c = this.cues[this.active];
    const top = (c && c.pos === "top") || (!c && st.position === "top") || (c && !c.pos && st.position === "top");
    const o = this.overlay.style;
    o.left = b.x + "px";
    o.width = b.w + "px";
    o.fontSize = Math.max(8, (st.size / 100) * b.h) + "px";
    const m = b.y + (st.margin / 100) * b.h;
    if (top) { o.top = m + "px"; o.bottom = "auto"; } else { o.bottom = m + "px"; o.top = "auto"; }
  }

  // ================================================================ spelling
  async spellAll() {
    const texts = this.cues.map((c) => c.text);
    try {
      const r = await api("POST", "/api/spell", { texts, language: this.meta.language });
      if (!this.alive || texts.length !== this.cues.length) return;
      this.unknown = r.unknown;
      $$(".cue", this.list).forEach((el) => this.fillRow(el, +el.dataset.i));
      this.updateCounts();
    } catch (_) { /* spell check unavailable */ }
  }
  spellLater(i) {
    const c = this.cues[i];
    if (!c) return;
    clearTimeout(this.spellTimers[i]);
    this.spellTimers[i] = setTimeout(async () => {
      try {
        const r = await api("POST", "/api/spell", { texts: [c.text], language: this.meta.language });
        const k = this.cues.indexOf(c);
        if (k < 0) return;
        this.unknown[k] = r.unknown[0];
        const el = this.list.children[k];
        if (el) this.fillRow(el, k);
        this.updateCounts();
      } catch (_) { /* ignore */ }
    }, 500);
  }

  // ================================================================ playback
  toggle() {
    this.playUntil = null;
    if (this.video.paused) this.video.play().catch(() => {}); else this.video.pause();
  }
  seek(tm) {
    const d = this.video.duration || this.meta.duration || 1e9;
    this.video.currentTime = Math.min(Math.max(0, tm), d);
    this.drawTimeline(true);
  }
  activeAt(tm) {
    let lo = 0, hi = this.cues.length - 1, found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (this.cues[mid].start <= tm) { found = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return found >= 0 && tm < this.cues[found].end ? found : -1;
  }
  showOverlay(force) {
    const i = this.activeAt(this.video.currentTime);
    if (i === this.active && !force) return;
    if (this.list.children[this.active]) this.list.children[this.active].classList.remove("now");
    this.active = i;
    let text = "";
    if (i >= 0) {
      text = this.cues[i].text;
      const el = this.list.children[i];
      if (el) el.classList.add("now");
      const typing = document.activeElement && document.activeElement.tagName === "TEXTAREA";
      if (!this.video.paused && !typing && !this.review && i !== this.sel) this.select(i, { seek: false });
    } else if (this.panel === "style") text = t("style_sample");
    this.overlay.replaceChildren(...text.split("\n").filter((l) => l.trim()).map((l) => h("span", null, l)));
    this.layoutOverlay();
  }
  loop() {
    if (!this.alive) return;
    const tm = this.video.currentTime;
    if (this.playUntil != null && tm >= this.playUntil && !this.video.paused) { this.video.pause(); this.playUntil = null; }
    if (tm !== this.lastT || !this.video.paused) {
      this.lastT = tm;
      this.clock.textContent = `${fmtTC(tm)} / ${fmtTC(this.video.duration || this.meta.duration || 0)}`;
      this.showOverlay(false);
      this.drawTimeline(false);
    }
    requestAnimationFrame(() => this.loop());
  }

  // ================================================================ timeline
  drawTimeline(force) {
    if (!this.tl) return;
    const tm = this.drag ? this.drag.center : this.video.currentTime;
    const W = this.win;
    const t0 = Math.max(0, tm - W / 2);
    const key = `${t0.toFixed(2)}|${this.sel}|${this.active}|${this.tl.clientWidth}`;
    if (!force && key === this.tlKey) return;
    this.tlKey = key;
    this.tlT0 = t0;
    const pct = (s) => ((s - t0) / W) * 100;
    const parts = [];
    for (let s = Math.ceil(t0); s <= t0 + W; s++) {
      const major = s % 5 === 0;
      parts.push(`<i class="tick${major ? " major" : ""}" style="left:${pct(s)}%">${major ? `<b>${fmtTC(s, 0).replace(/^0:/, "")}</b>` : ""}</i>`);
    }
    this.cues.forEach((c, i) => {
      if (c.end < t0 || c.start > t0 + W) return;
      const cls = ["blk", i === this.sel ? "sel" : "", i === this.active ? "active" : "", this.needsReview(i) ? "flag" : "", c.checked ? "ok" : ""].join(" ");
      const txt = c.text.split("\n")[0].replace(/[<&]/g, (ch) => (ch === "<" ? "&lt;" : "&amp;"));
      parts.push(`<div class="${cls}" data-i="${i}" style="left:${pct(c.start)}%;width:${pct(c.end) - pct(c.start)}%"><span>${txt}</span>${i === this.sel ? `<em class="hd l" data-e="start" data-tip="${this.tipDrag}"></em><em class="hd r" data-e="end" data-tip="${this.tipDrag}"></em>` : ""}</div>`);
    });
    parts.push(`<div class="playhead" style="left:${pct(this.video.currentTime)}%"></div>`);
    this.tlLayer.innerHTML = parts.join("");
    this.drawWave(t0, W);
  }
  drawWave(t0, W) {
    const c = this.wave;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, hgt = c.clientHeight;
    if (!w || !hgt) return;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(hgt * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(hgt * dpr); }
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, hgt);
    if (!this.waveColor) {
      const cs = getComputedStyle(this.tl);
      this.waveColor = cs.getPropertyValue("--wave").trim() || "#9fb3c5";
      this.waveSel = cs.getPropertyValue("--wave-sel").trim() || "#1f4f78";
    }
    const sc = this.cues[this.sel];
    if (sc) {
      ctx.fillStyle = this.waveSel;
      ctx.globalAlpha = 0.12;
      ctx.fillRect(((sc.start - t0) / W) * w, 0, ((sc.end - sc.start) / W) * w, hgt);
      ctx.globalAlpha = 1;
    }
    const p = this.peaks;
    if (!p || !p.length) return;
    const R = this.peakRate;
    const mid = hgt / 2;
    for (let x = 0; x < w; x += 2) {
      const a = Math.floor((t0 + (x / w) * W) * R);
      const b = Math.max(a + 1, Math.floor((t0 + ((x + 2) / w) * W) * R));
      let v = 0;
      for (let k = a; k < b && k < p.length; k++) if (p[k] > v) v = p[k];
      if (!v) continue;
      const tSec = t0 + (x / w) * W;
      ctx.fillStyle = sc && tSec >= sc.start && tSec <= sc.end ? this.waveSel : this.waveColor;
      const bh = Math.max(1, (v / 255) * (hgt - 4));
      ctx.fillRect(x, mid - bh / 2, 1.4, bh);
    }
  }
  tlDown(e) {
    const rect = this.tl.getBoundingClientRect();
    const toTime = (x) => this.tlT0 + ((x - rect.left) / rect.width) * this.win;
    const hd = e.target.closest(".hd");
    const blk = e.target.closest(".blk");
    if (hd) {
      e.preventDefault();
      const i = this.sel, key = hd.dataset.e;
      this.snapshot();
      this.drag = { center: this.tlT0 + this.win / 2 };
      this.tl.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const c = this.cues[i];
        let v = Math.round(toTime(ev.clientX) * 100) / 100;
        const pv = this.cues[i - 1], nx = this.cues[i + 1];
        if (key === "start") v = Math.min(Math.max(v, pv ? pv.end + 0.04 : 0), c.end - 0.2);
        else v = Math.max(Math.min(v, nx ? nx.start - 0.04 : 1e9), c.start + 0.2);
        c[key] = v;
        c.checked = false;
        this.fillRow(this.list.children[i], i);
        this.drawTimeline(true);
      };
      const up = () => {
        this.tl.removeEventListener("pointermove", move);
        this.drag = null;
        this.refreshRow(i);
        this.changed();
      };
      this.tl.addEventListener("pointermove", move);
      this.tl.addEventListener("pointerup", up, { once: true });
      return;
    }
    // click = jump there (or pick the subtitle); press and pull sideways = scroll the timeline
    if (e.button !== 0) return;
    e.preventDefault();
    const x0 = e.clientX, c0 = this.tlT0 + this.win / 2, wasPlaying = !this.video.paused;
    let pulling = false, last = 0;
    this.tl.setPointerCapture(e.pointerId);
    const move = (ev) => {
      if (!pulling && Math.abs(ev.clientX - x0) < 4) return;
      if (!pulling) {
        pulling = true;
        if (wasPlaying) this.video.pause();
        this.tl.classList.add("pulling");
      }
      const dur = this.video.duration || 1e9;
      const center = Math.min(dur, Math.max(0, c0 - ((ev.clientX - x0) / rect.width) * this.win));
      this.drag = { center };
      this.drawTimeline(true);
      if (performance.now() - last > 80) { last = performance.now(); this.video.currentTime = center; }
    };
    const up = (ev) => {
      this.tl.removeEventListener("pointermove", move);
      this.tl.classList.remove("pulling");
      if (pulling) {
        this.seek(this.drag.center);
        this.drag = null;
        if (wasPlaying) this.video.play();
      } else if (blk) this.select(+blk.dataset.i, { focus: true });
      else this.seek(toTime(ev.clientX));
    };
    this.tl.addEventListener("pointermove", move);
    this.tl.addEventListener("pointerup", up, { once: true });
  }
  tlWheel(e) {
    e.preventDefault();
    if (e.ctrlKey) { // zoom in/out around the playhead
      this.win = Math.min(120, Math.max(6, this.win * (e.deltaY > 0 ? 1.2 : 1 / 1.2)));
      this.drawTimeline(true);
      return;
    }
    const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    const px = e.deltaMode === 1 ? d * 30 : d;
    const dur = this.video.duration || 1e9;
    this.seek(Math.min(dur, Math.max(0, this.video.currentTime + (px / this.tl.clientWidth) * this.win)));
  }

  // ================================================================ keyboard
  showKeys() {
    const d = $("#dialog");
    $("#dlg-title").textContent = t("shortcuts");
    $("#dlg-body").innerHTML = t("keys_list_html");
    $("#dlg-ok").textContent = t("close");
    $("#dlg-ok").className = "btn primary";
    $("#dlg-cancel").hidden = true;
    d.addEventListener("close", () => ($("#dlg-cancel").hidden = false), { once: true });
    d.showModal();
  }
  onKey(e) {
    const tag = e.target.tagName;
    const typing = tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT";
    if ($("#dialog").open) return;
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.code === "Space") { e.preventDefault(); this.toggle(); return; }
    if (ctrl && e.code === "KeyF") { e.preventDefault(); this.openPanel("find"); return; }
    if (ctrl && e.code === "KeyS") { e.preventDefault(); this.save(); return; }
    if (ctrl && !e.shiftKey && e.code === "KeyZ" && !typing) { e.preventDefault(); this.undo(); return; }
    if (ctrl && (e.code === "KeyY" || (e.shiftKey && e.code === "KeyZ")) && !typing) { e.preventDefault(); this.redo(); return; }
    if (e.key === "Escape") {
      if (tag === "TEXTAREA") { e.target.blur(); return; }  // first Esc: leave the text field so plain keys work
      if (this.review) this.stopReview(); else if (this.panel) this.closePanel();
      return;
    }
    if (this.review && e.key === "Enter" && !e.shiftKey && (tag === "TEXTAREA" || !typing)) {
      e.preventDefault();
      if (this.sel >= 0) { this.cues[this.sel].checked = false; this.approve(this.sel); }
      return;
    }
    if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); this.select(Math.min(this.sel + 1, this.cues.length - 1), { focus: true }); return; }
    if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); this.select(Math.max(this.sel - 1, 0), { focus: true }); return; }
    if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); this.seek(this.video.currentTime - 5); return; }
    if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); this.seek(this.video.currentTime + 5); return; }
    if (e.altKey && e.code === "KeyR") { e.preventDefault(); if (this.sel >= 0) this.playCue(this.sel); return; }
    if (e.altKey && e.code === "KeyI") { e.preventDefault(); if (this.sel >= 0) this.setTime(this.sel, "start", this.video.currentTime); return; }
    if (e.altKey && e.code === "KeyO") { e.preventDefault(); if (this.sel >= 0) this.setTime(this.sel, "end", this.video.currentTime); return; }
    if (e.altKey && e.code === "KeyK") { e.preventDefault(); if (this.sel >= 0) this.approve(this.sel); return; }
    if (e.altKey && e.code === "KeyG") { e.preventDefault(); this.review ? this.stopReview() : this.startReview(); return; }
    if (typing || e.altKey || ctrl) return;
    // plain keys, only outside text fields
    if (e.code === "Space") { e.preventDefault(); this.toggle(); return; }
    if (e.key === "ArrowLeft") { e.preventDefault(); this.seek(this.video.currentTime - 5); return; }
    if (e.key === "ArrowRight") { e.preventDefault(); this.seek(this.video.currentTime + 5); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); this.select(Math.max(this.sel - 1, 0), { focus: false }); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); this.select(Math.min(this.sel + 1, this.cues.length - 1), { focus: false }); return; }
    if (e.key === "Enter" && this.sel >= 0) { e.preventDefault(); this.list.children[this.sel]._ta.focus(); return; }
    if (e.key === "?") { e.preventDefault(); this.showKeys(); }
  }

  // ================================================================== export
  async download() {
    if (this.dirty) await this.save();
    const a = h("a", { href: `/api/projects/${this.id}/srt`, download: "" });
    document.body.append(a);
    a.click();
    a.remove();
  }
  async saveNextToVideo() {
    if (this.dirty) await this.save();
    try {
      const r = await api("POST", `/api/projects/${this.id}/save-srt`);
      toast(r.backup ? t("saved_srt_backup", { p: r.path }) : t("saved_srt", { p: r.path }),
        { label: t("show_in_folder"), run: () => api("POST", "/api/open-folder", { path: r.path }) });
    } catch (e) { toast(t("save_srt_failed") + " " + e.message); }
  }
  async burn() {
    if (this.isAudio) { toast(t("burn_audio")); return; }
    if (this.dirty) await this.save();
    if (this.reviewLeft() && !(await ask(t("burn_unchecked_q"), t("burn_unchecked_body", { n: this.reviewLeft() }), t("burn_anyway")))) return;
    try {
      S.state = await api("POST", `/api/projects/${this.id}/burn`);
      this.sync(S.state);
      toast(t("burn_started"));
    } catch (e) { toast(e.message); }
  }
}

const STYLE_DEFAULT = { font: "Arial", size: 4.6, bold: false, color: "#ffffff", box: true, box_opacity: 0.75, outline: false, position: "bottom", margin: 6 };

function similarity(a, b) {  // 1 - edit distance / longest length
  const m = a.length, n = b.length;
  if (!m || !n) return 0;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    prev = cur;
  }
  return 1 - prev[n] / Math.max(m, n);
}
