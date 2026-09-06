/* cosúil review UI — vanilla JS, no build step. */
"use strict";

const state = {
  scanId: null,
  scan: null,
  groups: [],
  total: 0,
  page: 1,
  perPage: 48,
  filters: { kind: "", status: "", sort: "bytes" },
  detail: null,      // current group detail
  groupIdx: -1,      // index within state.groups
  selected: 0,       // selected member index
  decisions: {},     // image_id -> keep|discard|undecided
  blinkTimer: null,
  polling: null,
  locs: [],          // distinct directories in the current group
  locOf: [],         // member index -> location index
  learnedFolder: null,  // folder the user consistently keeps (exact groups)
  folderConfirms: {},   // folder -> number of confirming exact groups
};

const $ = (sel) => document.querySelector(sel);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* noop */ }
    throw new Error(detail);
  }
  return res.json();
}

function fmtBytes(n) {
  if (n === null || n === undefined) return "—";
  for (const unit of ["B", "KiB", "MiB", "GiB", "TiB"]) {
    if (n < 1024 || unit === "TiB") return unit === "B" ? `${n} B` : `${n.toFixed(1)} ${unit}`;
    n /= 1024;
  }
}

function fmtDate(sec) {
  if (!sec) return "—";
  return new Date(sec * 1000).toLocaleString();
}

function showView(name) {
  for (const v of ["scan", "results", "detail"]) {
    $(`#view-${v}`).classList.toggle("hidden", v !== name);
  }
}

/* ================= scan view ================= */

async function startScan() {
  const kinds = [];
  if ($("#kinds-exact").checked) kinds.push("exact");
  if ($("#kinds-similar").checked) kinds.push("similar");
  if ($("#kinds-deep").checked) kinds.push("deep");
  const root = $("#root-input").value.trim();
  if (!root) { alert("Enter a directory to scan"); return; }
  const body = {
    root,
    kinds,
    phash_threshold: parseInt($("#threshold-input").value || "6", 10),
    include_hidden: $("#hidden-input").checked,
    fresh: $("#fresh-input").checked,
    exclude_dirs: $("#exclude-input").value.split(",").map((s) => s.trim()).filter(Boolean),
  };
  $("#btn-start").disabled = true;
  $("#scan-progress").classList.remove("hidden");
  try {
    const { scan_id } = await api("/api/scans", {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.scanId = scan_id;
    pollScan();
  } catch (err) {
    alert(`Scan failed to start: ${err.message}`);
    $("#btn-start").disabled = false;
  }
}

function pollScan() {
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    try {
      const scan = await api(`/api/scans/${state.scanId}`);
      const pct = scan.images_found
        ? Math.min(100, Math.round((scan.images_hashed / scan.images_found) * 100))
        : 0;
      $("#progress-fill").style.width = `${pct}%`;
      $("#progress-stage").textContent =
        scan.status === "done" ? "scan finished" :
        scan.status === "error" ? `error: ${scan.error || "unknown"}` : "scanning…";
      $("#progress-stats").textContent =
        `${scan.files_walked} files walked · ${scan.images_found} images · ` +
        `${scan.images_hashed} hashed · ${scan.exact_groups} exact · ` +
        `${scan.similar_groups} similar · ${scan.deep_groups} deep`;
      if (scan.status === "done" || scan.status === "error") {
        clearInterval(state.polling);
        $("#btn-start").disabled = false;
        if (scan.status === "done") openResults(scan);
      }
    } catch (e) { /* transient */ }
  }, 800);
}

/* ================= results view ================= */

function renderScanSettings(scan) {
  const cfg = scan.config || {};
  const kinds = (cfg.kinds || []).join(", ") || "—";
  const stamp = (s) => (s || "").replace("T", " ");
  const parts = [
    `started ${stamp(scan.started_at)}`,
    `tiers: ${kinds}`,
    `phash threshold ${cfg.phash_threshold ?? "?"}`,
  ];
  if ((cfg.kinds || []).includes("deep")) {
    parts.push(`cnn threshold ${cfg.cnn_threshold ?? "?"}`);
  }
  parts.push(cfg.include_hidden ? "hidden files included" : "hidden files skipped");
  if (cfg.skip_libraries !== undefined) {
    parts.push(cfg.skip_libraries ? "photos libraries skipped" : "photos libraries scanned");
  }
  if ((cfg.exclude_dirs || []).length) {
    parts.push(`excluded: ${cfg.exclude_dirs.join(", ")}`);
  }
  parts.push(`${scan.files_walked} files walked · ${scan.images_found} images`);
  if (scan.status === "done" && scan.finished_at) {
    parts.push(`finished ${stamp(scan.finished_at)}`);
  }
  return parts.join(" · ");
}

async function openResults(scan) {
  state.scan = scan;
  state.page = 1;
  $("#results-title").textContent = `Scan ${scan.id} results`;
  $("#results-sub").textContent = renderScanSettings(scan);
  showView("results");
  await loadGroups();
  renderApplyBar();
}

async function loadGroups() {
  const { kind, status, sort } = state.filters;
  const params = new URLSearchParams({ page: state.page, per_page: state.perPage, sort });
  if (kind) params.set("kind", kind);
  if (status) params.set("status", status);
  const listing = await api(`/api/scans/${state.scanId}/groups?${params}`);
  state.groups = listing.groups;
  state.total = listing.total;
  renderGrid();
  const pages = Math.max(1, Math.ceil(listing.total / listing.per_page));
  $("#page-info").textContent = `${listing.total} groups · page ${state.page} / ${pages}`;
  $("#btn-prev-page").disabled = state.page <= 1;
  $("#btn-next-page").disabled = state.page >= pages;
}

function renderGrid() {
  const grid = $("#groups-grid");
  if (!state.groups.length) {
    grid.innerHTML = `<div class="sub">No duplicate groups found — nothing to review. 🎉</div>`;
    return;
  }
  grid.innerHTML = state.groups.map((g, i) => {
    const reviewed = g.status === "reviewed";
    return `
    <div class="group-card ${reviewed ? "reviewed" : ""}" data-idx="${i}">
      <img src="${g.thumb_url}" alt="" loading="lazy">
      <div class="card-body">
        <div class="name">${escapeHtml(g.name || "…")}</div>
        <div class="meta">
          <span class="badge ${g.kind}">${g.kind}×${g.member_count}</span>
          <span class="bytes" title="Disk space reclaimed when discard-marked files are moved to trash">${fmtBytes(g.reclaimable_bytes)}</span>
        </div>
      </div>
    </div>`;
  }).join("");
  grid.querySelectorAll(".group-card").forEach((card) => {
    card.addEventListener("click", () => openGroup(parseInt(card.dataset.idx, 10)));
  });
}

function renderApplyBar() {
  const stats = state.scan.decisions || {};
  const discard = stats.discard || { count: 0, bytes: 0 };
  const bar = $("#apply-bar");
  bar.classList.toggle("hidden", !discard.count);
  $("#apply-summary").innerHTML =
    `<span class="big">${discard.count} files marked for trash</span> ` +
    `<span class="sub">(${fmtBytes(discard.bytes)} reclaimable)</span>`;
}

/* ================= detail view ================= */

async function openGroup(idx) {
  const g = state.groups[idx];
  if (!g) return;
  state.groupIdx = idx;
  state.detail = await api(`/api/groups/${g.id}`);
  state.selected = state.detail.members.findIndex((m) => m.decision === "undecided");
  if (state.selected < 0) state.selected = 0;
  stopBlink();
  showView("detail");
  renderDetail();
}

function groupNav(step) {
  const next = state.groupIdx + step;
  if (next >= 0 && next < state.groups.length) {
    saveDecisions(false).then(() => openGroup(next));
  }
}

function renderDetail() {
  const d = state.detail;
  const kindBadge = `<span class="badge ${d.kind}">${d.kind}</span>`;
  const statusBadge = `<span class="badge ${d.status}">${d.status}</span>`;
  $("#detail-kind").innerHTML = kindBadge + " " + statusBadge;
  $("#detail-title").textContent = d.members[0]?.path || "";
  $("#detail-pos").textContent = `${state.groupIdx + 1} / ${state.total}`;
  $("#btn-prev-group").disabled = state.groupIdx <= 0;
  $("#btn-next-group").disabled = state.groupIdx >= state.total - 1;

  const dupCounts = {};
  const dupOf = {};
  d.members.forEach((m, i) => {
    if (m.blake3) {
      dupCounts[m.blake3] = (dupCounts[m.blake3] || 0) + 1;
      (dupOf[m.blake3] = dupOf[m.blake3] || []).push(i);
    }
  });

  // blink is useless when every member is the exact same bytes
  const allIdentical = d.members.length > 1 &&
    d.members.every((m) => m.blake3 && m.blake3 === d.members[0].blake3);
  $("#btn-blink").disabled = allIdentical;
  $("#btn-blink").title = allIdentical
    ? "Nothing to compare — all files are byte-identical"
    : "Overlay the images in one spot and alternate them (B)";

  // color-coded locations: each distinct directory gets a color chip
  const locs = [];
  const locOf = d.members.map((m) => {
    const key = m.rel_dir || m.dirname;
    let i = locs.indexOf(key);
    if (i < 0) { i = locs.length; locs.push(key); }
    return i;
  });
  const locCounts = locs.map(() => 0);
  locOf.forEach((i) => { locCounts[i] += 1; });
  state.locs = locs;
  state.locOf = locOf;
  const DOT = ["#e055c1", "#35c8d8", "#e8c05c", "#43d17c", "#b79cff", "#ff9aa6", "#7cc4ff", "#7ce0b0"];
  const dot = (i) => DOT[i % DOT.length];

  $("#loc-bar").innerHTML = locs.map((l, i) =>
    `<button class="loc-chip" data-loc="${i}" title="Keep all from this folder, discard the rest">` +
    `<span class="loc-dot" style="background:${dot(i)}"></span>${escapeHtml(l)} ` +
    `<span class="loc-count">(${locCounts[i]})</span></button>`
  ).join("");
  document.querySelectorAll("#loc-bar .loc-chip").forEach((chip) => {
    chip.addEventListener("click", () => keepLocation(parseInt(chip.dataset.loc, 10)));
  });

  $("#compare-stage").innerHTML = d.members.map((m, i) => {
    const q = m.quality_score === null ? null : Math.round((m.quality_score || 0) * 100);
    const exifBits = [];
    if (m.exif?.camera) exifBits.push(`📷 ${escapeHtml(m.exif.camera)}`);
    if (m.exif?.taken) exifBits.push(`🗓 ${escapeHtml(m.exif.taken)}`);
    const exactSiblings = (dupOf[m.blake3] || []).filter((j) => j !== i);
    const exact = m.blake3 && exactSiblings.length
      ? `<span class="badge exact" title="Byte-identical to:\n${escapeHtml(exactSiblings.map((j) => d.members[j].path).join("\n"))}">exact copy</span>`
      : "";
    const kbd = i < 9 ? `<span class="kbd">${i + 1}</span>` : "";
    return `
    <div class="preview" data-i="${i}">
      <div class="img-wrap"><img src="${m.file_url}" alt=""></div>
      <div class="p-body">
        <div class="p-top">${kbd}<span class="loc-dot" style="background:${dot(locOf[i])}" title="${escapeHtml(m.rel_dir || m.dirname)}"></span><span class="p-name">${escapeHtml(m.name)}</span>${exact}</div>
        <div class="qbar"><div class="qbar-fill" style="width:${q ?? 0}%"></div></div>
        <table>
          <tr><td>quality score</td><td>${q === null ? "—" : q + "%"}</td></tr>
          <tr><td>dimensions</td><td>${m.width ?? "?"} × ${m.height ?? "?"}</td></tr>
          <tr><td>file size</td><td>${fmtBytes(m.size)}</td></tr>
          <tr><td>format</td><td>${m.format || "—"}</td></tr>
          <tr><td>location</td><td class="loc-txt" title="${escapeHtml(m.dirname)}">${escapeHtml(m.rel_dir || m.dirname)}</td></tr>
          <tr><td>modified</td><td>${fmtDate(m.mtime)}</td></tr>
          <tr><td>camera</td><td>${escapeHtml(m.exif?.camera || "—")}</td></tr>
          <tr><td>taken</td><td>${escapeHtml(m.exif?.taken || "—")}</td></tr>
          ${m.warning ? `<tr><td>note</td><td>${escapeHtml(m.warning)}</td></tr>` : ""}
          ${m.error ? `<tr><td>note</td><td>${escapeHtml(m.error)}</td></tr>` : ""}
        </table>
        <div class="actions">
          <button class="btn keep ${m.decision === "keep" ? "keep-on" : ""}" data-act="keep">Keep</button>
          <button class="btn discard ${m.decision === "discard" ? "discard-on" : ""}" data-act="discard">Discard</button>
          <button class="btn copy-path" data-i="${i}" title="Copy the full path to this photo">Copy path</button>
        </div>
      </div>
    </div>`;
  }).join("");

  const stage = $("#compare-stage");
  stage.querySelectorAll(".preview").forEach((el) => {
    const i = parseInt(el.dataset.i, 10);
    el.addEventListener("click", (e) => {
      if (e.target.closest(".actions")) return;
      setSelected(i);
    });
    el.querySelector(".keep").addEventListener("click", () => setDecision(i, "keep"));
    el.querySelector(".discard").addEventListener("click", () => setDecision(i, "discard"));
    el.querySelector(".copy-path").addEventListener("click", () => copyPath(i));
    el.querySelector(".img-wrap").addEventListener("click", (e) => {
      if (e.target.closest("img")) toggleZoom(e);
    });
  });
  applyPreviewState();
  renderSuggestion();
}

function applyPreviewState() {
  const d = state.detail;
  d.members.forEach((m, i) => {
    const el = $(`.preview[data-i="${i}"]`);
    if (!el) return;
    el.classList.toggle("selected", i === state.selected);
    el.classList.toggle("keep", m.decision === "keep");
    el.classList.toggle("discard", m.decision === "discard");
    // keep the buttons in sync so toggles are visible
    el.querySelector(".keep")?.classList.toggle("keep-on", m.decision === "keep");
    el.querySelector(".discard")?.classList.toggle("discard-on", m.decision === "discard");
  });
}

function setSelected(i) {
  state.selected = i;
  applyPreviewState();
}

function setDecision(i, value) {
  const d = state.detail;
  const m = d.members[i];
  if (value === "keep") {
    if (m.decision === "keep") {
      m.decision = "undecided";  // toggle off (siblings stay as they are)
    } else {
      // keeping one photo auto-keeps its folder-mates (same location)
      const key = m.rel_dir || m.dirname;
      d.members.forEach((other) => {
        if ((other.rel_dir || other.dirname) === key) other.decision = "keep";
      });
    }
  } else {
    m.decision = m.decision === value ? "undecided" : value;
  }
  applyPreviewState();
}

function keepLocation(locIdx) {
  const d = state.detail;
  d.members.forEach((m, i) => {
    m.decision = state.locOf[i] === locIdx ? "keep" : "discard";
  });
  applyPreviewState();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    // fallback for browsers without clipboard permissions
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }
}

async function copyPath(i) {
  const m = state.detail.members[i];
  const ok = await copyText(m.path);
  const btn = document.querySelector(`.preview[data-i="${i}"] .copy-path`);
  if (ok && btn) {
    const old = btn.textContent;
    btn.textContent = "✓ copied";
    setTimeout(() => { btn.textContent = old; }, 1200);
  } else if (!ok) {
    alert(`Path (copy manually):\n${m.path}`);
  }
}

async function saveDecisions(advance) {
  const d = state.detail;
  const decisions = {};
  d.members.forEach((m) => { decisions[String(m.image_id)] = m.decision; });
  try { await api(`/api/groups/${d.id}/decisions`, { method: "POST", body: JSON.stringify({ decisions }) }); }
  catch (e) { alert(`Could not save decisions: ${e.message}`); return false; }
  learnFromGroup(d);
  renderSuggestion();
  const g = state.groups[state.groupIdx];
  const hasDecisions = Object.values(decisions).some((v) => v !== "undecided");
  if (g) g.status = hasDecisions ? "reviewed" : "pending";
  d.status = g ? g.status : d.status;
  // keep the header badge in sync
  const badges = document.querySelectorAll("#detail-kind .badge");
  if (badges.length >= 2) {
    badges[1].className = `badge ${d.status}`;
    badges[1].textContent = d.status;
  }
  const scan = await api(`/api/scans/${state.scanId}`);
  state.scan = scan;
  renderApplyBar();
  if (advance) groupNav(1);
  return true;
}

function folderOf(m) {
  return m.rel_dir || m.dirname;
}

// Learn from exact groups: if every kept photo lives in one folder and the
// group also has photos elsewhere, count it as a confirmation for that folder.
function learnFromGroup(d) {
  if (d.kind !== "exact") return;
  const kept = d.members.filter((m) => m.decision === "keep");
  if (!kept.length) return;
  const folder = folderOf(kept[0]);
  if (!kept.every((m) => folderOf(m) === folder)) return;
  if (!d.members.some((m) => folderOf(m) !== folder)) return;
  state.folderConfirms[folder] = (state.folderConfirms[folder] || 0) + 1;
  if (state.folderConfirms[folder] >= 5 && state.learnedFolder !== folder) {
    state.learnedFolder = folder;
  }
}

function renderSuggestion() {
  const bar = $("#suggestion-bar");
  const d = state.detail;
  const folder = state.learnedFolder;
  const inF = folder ? d.members.filter((m) => folderOf(m) === folder).length : 0;
  const outF = d.members.length - inF;
  if (folder && d.kind === "exact" && inF > 0 && outF > 0) {
    bar.classList.remove("hidden");
    bar.innerHTML =
      `<span>Keep <b>all photos in ${escapeHtml(folder)}</b> (${inF}) and discard <b>everything else</b> (${outF})?</span>` +
      `<button id="suggestion-apply" class="btn primary">Apply</button>` +
      `<button id="suggestion-dismiss" class="btn ghost" title="Stop suggesting this folder">Don't suggest again</button>`;
    $("#suggestion-apply").addEventListener("click", () => {
      const f = state.learnedFolder;
      d.members.forEach((m) => { m.decision = folderOf(m) === f ? "keep" : "discard"; });
      applyPreviewState();
    });
    $("#suggestion-dismiss").addEventListener("click", () => {
      state.learnedFolder = null;
      state.folderConfirms = {};
      renderSuggestion();
    });
  } else {
    bar.classList.add("hidden");
  }
}

function autosuggest() {
  const d = state.detail;
  let best = 0;
  d.members.forEach((m, i) => {
    if ((m.quality_score ?? -1) > (d.members[best].quality_score ?? -1)) best = i;
  });
  d.members.forEach((m, i) => { m.decision = i === best ? "keep" : "discard"; });
  applyPreviewState();
}

async function resetDecisions() {
  const d = state.detail;
  d.members.forEach((m) => { m.decision = "undecided"; });
  // clearing decisions returns the group to 'pending'
  const decisions = {};
  d.members.forEach((m) => { decisions[String(m.image_id)] = "undecided"; });
  try {
    await api(`/api/groups/${d.id}/decisions`, { method: "POST", body: JSON.stringify({ decisions }) });
  } catch (e) { /* non-fatal */ }
  const g = state.groups[state.groupIdx];
  if (g) g.status = "pending";
  d.status = "pending";
  renderDetail();
  const scan = await api(`/api/scans/${state.scanId}`);
  state.scan = scan;
  renderApplyBar();
}

/* ---- compare tools ---- */

function toggleZoom(e) {
  const stage = $("#compare-stage");
  const zoomed = stage.classList.toggle("zoomed") || false;
  stage.querySelectorAll(".preview").forEach((el) => {
    el.classList.toggle("zoomed", !!zoomed);
    if (zoomed && e) {
      const rect = el.querySelector(".img-wrap").getBoundingClientRect();
      const x = e.clientX - rect.left, y = e.clientY - rect.top;
      el.querySelector("img").style.transformOrigin =
        `${(x / rect.width) * 100}% ${(y / rect.height) * 100}%`;
    } else if (!zoomed) {
      el.querySelector("img").style.transformOrigin = "center";
    }
  });
}

function stopBlink() {
  if (state.blinkTimer) { clearInterval(state.blinkTimer); state.blinkTimer = null; }
  const stage = $("#compare-stage");
  stage.classList.remove("blinking");
  stage.querySelectorAll(".preview").forEach((el) => (el.style.opacity = ""));
}

function blink() {
  if ($("#btn-blink").disabled) return;  // byte-identical group: nothing to blink
  if (state.blinkTimer) { stopBlink(); return; }
  const stage = $("#compare-stage");
  const previews = [...stage.querySelectorAll(".preview")];
  if (previews.length < 2) return;
  // overlay mode: stack all previews on top of each other and cycle them
  stage.classList.add("blinking");
  previews.forEach((el, j) => { el.style.opacity = j === 0 ? 1 : 0; });
  let i = 0;
  state.blinkTimer = setInterval(() => {
    i = (i + 1) % previews.length;
    previews.forEach((el, j) => { el.style.opacity = j === i ? 1 : 0; });
  }, 450);
}

/* ---- keyboard ---- */

document.addEventListener("keydown", (e) => {
  if (!$("#view-detail").classList.contains("hidden")) {
    if (e.target.matches("input, select, textarea")) return;
    const d = state.detail;
    const num = parseInt(e.key, 10);
    if (num >= 1 && num <= d.members.length) { setSelected(num - 1); e.preventDefault(); return; }
    switch (e.key) {
      case "ArrowLeft": setSelected(Math.max(0, state.selected - 1)); e.preventDefault(); break;
      case "ArrowRight": setSelected(Math.min(d.members.length - 1, state.selected + 1)); e.preventDefault(); break;
      case "ArrowUp": groupNav(-1); e.preventDefault(); break;
      case "ArrowDown": groupNav(1); e.preventDefault(); break;
      case "k": case "K": setDecision(state.selected, "keep"); break;
      case "x": case "X": setDecision(state.selected, "discard"); break;
      case "c": case "C": copyPath(state.selected); break;
      case "r": case "R": resetDecisions(); break;
      case "z": case "Z": toggleZoom(); break;
      case "b": case "B": blink(); break;
      case "s": case "S": saveDecisions(false); break;
      case "Enter": saveDecisions(true); e.preventDefault(); break;
      case "Escape": stopBlink(); break;
    }
  }
});

/* ================= browse modal ================= */

let browseCurrent = "/";

async function openBrowse(path) {
  browseCurrent = path || browseCurrent;
  $("#modal-browse").classList.remove("hidden");
  await renderBrowse();
}

async function renderBrowse() {
  const data = await api(`/api/browse?path=${encodeURIComponent(browseCurrent)}`);
  browseCurrent = data.path;
  $("#browse-path").textContent = data.path;
  const list = $("#browse-list");
  const up = `<div class="item up" data-path="${data.parent}">../</div>`;
  list.innerHTML = up + data.dirs.map((d) =>
    `<div class="item" data-path="${d.path}">${escapeHtml(d.name)}/</div>`).join("");
  list.querySelectorAll(".item").forEach((el) => {
    el.addEventListener("click", async () => {
      browseCurrent = el.dataset.path;
      await renderBrowse();
    });
  });
}

/* ================= apply modal ================= */

async function applyDecisions() {
  if (!confirm("Move every file marked 'discard' to the OS trash? This is recoverable from the trash.")) return;
  $("#btn-apply").disabled = true;
  try {
    const result = await api(`/api/scans/${state.scanId}/apply`, { method: "POST" });
    $("#apply-result").innerHTML =
      `<div class="ok">✔ Moved ${result.moved} files (${fmtBytes(result.bytes)}) to the trash.</div>` +
      (result.errors.length
        ? `<div class="err">${result.errors.length} files could not be moved.</div>` : "") +
      `<div class="sub">Report: ${escapeHtml(result.report_file)}</div>`;
    $("#modal-apply").classList.remove("hidden");
  } catch (err) {
    alert(`Apply failed: ${err.message}`);
  } finally {
    $("#btn-apply").disabled = false;
  }
}

/* ================= scan history ================= */

async function refreshScansList() {
  const data = await api("/api/scans");
  const scans = data.scans || [];
  $("#scans-list").innerHTML = scans.length
    ? scans.map((s) => {
        const groups = s.exact_groups + s.similar_groups + s.deep_groups;
        return `
    <div class="scan-row">
      <div class="scan-info">
        <span class="scan-root" title="${escapeHtml(s.root)}">${escapeHtml(s.root)}</span>
        <span class="sub"><span class="badge ${s.status}">${s.status}</span> ${s.started_at} · ${s.images_found} images · ${groups} groups</span>
      </div>
      <div class="scan-actions">
        <button class="btn" data-inspect="${s.id}" ${s.status === "done" ? "" : "disabled"}>Inspect</button>
        <button class="btn" data-rescan="${s.id}">Rescan</button>
        <button class="btn ghost danger" data-delete="${s.id}" ${s.status === "done" ? "" : "disabled"}>Delete</button>
      </div>
    </div>`;
      })
      .join("")
    : `<div class="sub">No scans yet — enter a directory above and scan it.</div>`;
  document.querySelectorAll("[data-inspect]").forEach((b) =>
    b.addEventListener("click", () => openScanById(parseInt(b.dataset.inspect, 10)))
  );
  document.querySelectorAll("[data-rescan]").forEach((b) =>
    b.addEventListener("click", () => rescanScan(parseInt(b.dataset.rescan, 10)))
  );
  document.querySelectorAll("[data-delete]").forEach((b) =>
    b.addEventListener("click", async () => {
      const id = parseInt(b.dataset.delete, 10);
      if (!confirm(`Delete scan ${id}? Its groups, decisions and image records will be removed.\nFiles on disk are never touched.`)) return;
      try {
        await api(`/api/scans/${id}`, { method: "DELETE" });
        refreshScansList();
      } catch (err) {
        alert(`Delete failed: ${err.message}`);
      }
    })
  );
}

async function openScanById(scanId) {
  const scan = await api(`/api/scans/${scanId}`);
  if (scan.status === "error") {
    alert(`Scan ${scanId} failed: ${scan.error || "unknown error"}`);
    return;
  }
  state.scanId = scanId;
  if (scan.status !== "done") {
    $("#scan-progress").classList.remove("hidden");
    $("#btn-start").disabled = true;
    pollScan();
    return;
  }
  await openResults(scan);
}

async function rescanScan(scanId) {
  $("#btn-start").disabled = true;
  $("#scan-progress").classList.remove("hidden");
  try {
    const { scan_id } = await api(`/api/scans/${scanId}/rescan`, { method: "POST" });
    state.scanId = scan_id;
    pollScan();
  } catch (err) {
    alert(`Rescan failed: ${err.message}`);
    $("#btn-start").disabled = false;
  }
}

/* ================= wiring ================= */

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("#btn-start").addEventListener("click", startScan);
$("#btn-home").addEventListener("click", () => {
  stopBlink();
  showView("scan");
  refreshScansList();
});
$("#btn-results-home").addEventListener("click", () => {
  stopBlink();
  showView("scan");
  refreshScansList();
});
$("#btn-back").addEventListener("click", () => {
  stopBlink();
  showView("results");
  loadGroups();
});
$("#btn-prev-group").addEventListener("click", () => groupNav(-1));
$("#btn-next-group").addEventListener("click", () => groupNav(1));
$("#btn-autosuggest").addEventListener("click", autosuggest);
$("#btn-reset").addEventListener("click", resetDecisions);
$("#btn-zoom").addEventListener("click", () => toggleZoom());
$("#btn-blink").addEventListener("click", blink);
$("#btn-apply").addEventListener("click", applyDecisions);
$("#apply-close").addEventListener("click", () => $("#modal-apply").classList.add("hidden"));
$("#btn-prev-page").addEventListener("click", () => { state.page--; loadGroups(); });
$("#btn-next-page").addEventListener("click", () => { state.page++; loadGroups(); });
for (const id of ["filter-kind", "filter-status", "filter-sort"]) {
  $(`#${id}`).addEventListener("change", async () => {
    state.filters.kind = $("#filter-kind").value;
    state.filters.status = $("#filter-status").value;
    state.filters.sort = $("#filter-sort").value;
    state.page = 1;
    await loadGroups();
  });
}
$("#btn-browse").addEventListener("click", () => openBrowse($("#root-input").value.trim() || "/"));
$("#browse-cancel").addEventListener("click", () => $("#modal-browse").classList.add("hidden"));
$("#browse-select").addEventListener("click", () => {
  $("#root-input").value = browseCurrent;
  $("#modal-browse").classList.add("hidden");
});

refreshScansList();
