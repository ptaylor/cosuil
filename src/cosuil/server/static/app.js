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

async function openResults(scan) {
  state.scan = scan;
  state.page = 1;
  $("#results-title").textContent = `Scan ${scan.id} results`;
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
          <span class="bytes">${fmtBytes(g.reclaimable_bytes)}</span>
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
  d.members.forEach((m) => { if (m.blake3) dupCounts[m.blake3] = (dupCounts[m.blake3] || 0) + 1; });

  $("#compare-stage").innerHTML = d.members.map((m, i) => {
    const q = m.quality_score === null ? null : Math.round((m.quality_score || 0) * 100);
    const exifBits = [];
    if (m.exif?.camera) exifBits.push(`📷 ${escapeHtml(m.exif.camera)}`);
    if (m.exif?.taken) exifBits.push(`🗓 ${escapeHtml(m.exif.taken)}`);
    const exact = m.blake3 && dupCounts[m.blake3] > 1
      ? `<span class="badge exact">exact copy</span>` : "";
    const kbd = i < 9 ? `<span class="kbd">${i + 1}</span>` : "";
    return `
    <div class="preview" data-i="${i}">
      <div class="img-wrap"><img src="${m.file_url}" alt=""></div>
      <div class="p-body">
        <div class="p-top">${kbd}<span class="p-name">${escapeHtml(m.name)}</span>${exact}</div>
        <div class="qbar"><div class="qbar-fill" style="width:${q ?? 0}%"></div></div>
        <table>
          <tr><td>quality score</td><td>${q === null ? "—" : q + "%"}</td></tr>
          <tr><td>dimensions</td><td>${m.width ?? "?"} × ${m.height ?? "?"}</td></tr>
          <tr><td>file size</td><td>${fmtBytes(m.size)}</td></tr>
          <tr><td>format</td><td>${m.format || "—"}</td></tr>
          <tr><td>modified</td><td>${fmtDate(m.mtime)}</td></tr>
          <tr><td>camera</td><td>${escapeHtml(m.exif?.camera || "—")}</td></tr>
          <tr><td>taken</td><td>${escapeHtml(m.exif?.taken || "—")}</td></tr>
          ${m.error ? `<tr><td>note</td><td>${escapeHtml(m.error)}</td></tr>` : ""}
        </table>
        <div class="actions">
          <button class="btn keep ${m.decision === "keep" ? "keep-on" : ""}" data-act="keep">Keep</button>
          <button class="btn discard ${m.decision === "discard" ? "discard-on" : ""}" data-act="discard">Discard</button>
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
    el.querySelector(".img-wrap").addEventListener("click", (e) => {
      if (e.target.closest("img")) toggleZoom(e);
    });
  });
  applyPreviewState();
}

function applyPreviewState() {
  const d = state.detail;
  d.members.forEach((m, i) => {
    const el = $(`.preview[data-i="${i}"]`);
    if (!el) return;
    el.classList.toggle("selected", i === state.selected);
    el.classList.toggle("keep", m.decision === "keep");
    el.classList.toggle("discard", m.decision === "discard");
  });
}

function setSelected(i) {
  state.selected = i;
  applyPreviewState();
}

function setDecision(i, value) {
  const m = state.detail.members[i];
  m.decision = m.decision === value ? "undecided" : value;
  applyPreviewState();
}

async function saveDecisions(advance) {
  const d = state.detail;
  const decisions = {};
  d.members.forEach((m) => { decisions[String(m.image_id)] = m.decision; });
  try { await api(`/api/groups/${d.id}/decisions`, { method: "POST", body: JSON.stringify({ decisions }) }); }
  catch (e) { alert(`Could not save decisions: ${e.message}`); return false; }
  const g = state.groups[state.groupIdx];
  if (g) g.status = "reviewed";
  const scan = await api(`/api/scans/${state.scanId}`);
  state.scan = scan;
  renderApplyBar();
  if (advance) groupNav(1);
  return true;
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
  document.querySelectorAll("#compare-stage .preview").forEach((el) => (el.style.opacity = 1));
}

function blink() {
  if (state.blinkTimer) { stopBlink(); return; }
  const previews = [...document.querySelectorAll("#compare-stage .preview")];
  if (previews.length < 2) return;
  let i = 0;
  state.blinkTimer = setInterval(() => {
    i = (i + 1) % previews.length;
    previews.forEach((el, j) => { el.style.opacity = j === i ? 1 : 0.06; });
  }, 420);
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

/* ================= wiring ================= */

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("#btn-start").addEventListener("click", startScan);
$("#btn-new-scan").addEventListener("click", () => {
  stopBlink();
  showView("scan");
});
$("#btn-back").addEventListener("click", () => {
  stopBlink();
  showView("results");
  loadGroups();
});
$("#btn-prev-group").addEventListener("click", () => groupNav(-1));
$("#btn-next-group").addEventListener("click", () => groupNav(1));
$("#btn-autosuggest").addEventListener("click", autosuggest);
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
