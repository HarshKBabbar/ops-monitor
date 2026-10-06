// NOTAM Monitor sheet
const S = { view: "active", rows: { active: [], archive: [] }, airports: [], limit: 200, version: -1, pending: false, cfg: {} };
const $ = id => document.getElementById(id);
const tbody = document.querySelector("#sheet tbody");
const thead = document.querySelector("#sheet thead");

const LABEL = { new: "NEW · NOT REVIEWED", pending: "ACTION PENDING", done: "DONE", expiring: "EXPIRING" };
const NEED = { YES: "Yes", NO: "No action needed", EXT: "Extending" };

// ---------------------------------------------------------------- data
async function load(quiet) {
  const before = new Set(S.rows.active.map(r => r.id));
  const [act, arc, aps] = await Promise.all([
    api("/api/notams?view=active"), api("/api/notams?view=archive"), api("/api/airports")]);
  S.rows.active = act; S.rows.archive = arc; S.airports = aps;
  if (quiet && before.size) {
    const fresh = act.filter(r => !before.has(r.id) && r.is_new).length;
    if (fresh) toast(`${fresh} new NOTAM${fresh > 1 ? "s" : ""} added at the top`);
  }
  fillLocations();
  render();
}

async function loadSources() {
  const src = await api("/api/sources");
  $("sources").innerHTML = src.map(s => {
    const cls = !s.enabled ? "off" : s.ok === 1 ? "ok" : s.ok === 0 ? "err" : "";
    const txt = !s.enabled ? "not set up" : `${ago(s.last_run)}${s.message ? " · " + s.message : ""}`;
    return `<span class="src" title="${esc(s.message || "")}"><span class="dot ${cls}"></span><b>${esc(s.name)}</b> ${esc(txt)}</span>`;
  }).join("");
}

// live refresh: poll the change counter; never re-draw while someone is typing in the sheet
async function poll() {
  try {
    const { version } = await api("/api/version");
    if (version !== S.version) {
      const first = S.version === -1;
      S.version = version;
      if (!first) {
        if (editing()) S.pending = true; else await load(true);
      }
      loadSources();
    }
  } catch { /* server restarting - try again next tick */ }
}
const editing = () => {
  const a = document.activeElement;
  return (tbody.contains(a) || $("airportsView").contains(a)) && /TEXTAREA|SELECT|INPUT/.test(a.tagName);
};

// ---------------------------------------------------------------- filters
function fillLocations() {
  if (S.view === "airports") return;
  const sel = $("loc"), cur = sel.value;
  const locs = [...new Set(S.rows[S.view].map(r => r.location).filter(Boolean))].sort();
  sel.innerHTML = `<option value="">All locations (${locs.length})</option>` +
    locs.map(l => `<option ${l === cur ? "selected" : ""}>${esc(l)}</option>`).join("");
}

function filtered() {
  const q = $("q").value.trim().toUpperCase();
  const loc = $("loc").value, st = $("st").value, crit = $("crit").checked;
  return S.rows[S.view].filter(r => {
    if (loc && r.location !== loc) return false;
    if (crit && !r.critical) return false;
    if (st === "unreviewed" && r.status !== "new") return false;
    if (st === "pending" && r.status !== "pending") return false;
    if (st === "done" && r.status !== "done") return false;
    if (st === "expiring" && r.status !== "expiring") return false;
    if (st === "new_only" && !r.is_new) return false;
    if (q) {
      const hay = `${r.notam_id} ${r.location} ${r.airport_name} ${r.text} ${r.schedule} ${r.action_taken} ${r.prev_notam} ${r.tags.join(" ")}`.toUpperCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

// ---------------------------------------------------------------- render
function counters() {
  const a = S.rows.active, c = s => a.filter(r => r.status === s).length;
  const d = S.cfg.expiring_days || 7;
  const items = [
    ["blue", a.length, "Active NOTAMs", ""],
    ["", c("new"), "New · not reviewed", "unreviewed"],
    ["yellow", c("pending"), "Action pending", "pending"],
    ["green", c("done"), "Done", "done"],
    ["red", c("expiring"), `Expiring < ${d} day${d > 1 ? "s" : ""}`, "expiring"],
    ["", a.filter(r => r.critical).length, "Performance-critical", "crit"],
  ];
  $("counters").innerHTML = items.map(([cls, n, l, f]) =>
    `<div class="counter ${cls}" data-f="${f}"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
  $("cntActive").textContent = `(${a.length})`;
  $("cntArchive").textContent = `(${S.rows.archive.length})`;
  $("cntAirports").textContent = `(${S.airports.length || "all"})`;
}

function validTo(r) {
  if (!r.valid_to) return r.valid_to_text || "—";
  return fmtZ(r.valid_to) + (r.valid_to_text === "EST" ? " <b>EST</b>" : "");
}
function tags(r) {
  const crit = new Set(r.crit_tags);
  return r.tags.map(t => `<span class="tag ${crit.has(t) ? "crit" : ""}">${esc(t)}</span>`).join("");
}
function textCell(r) {
  const sched = r.schedule ? `<span class="sched">${esc(r.schedule)}</span>\n` : "";
  return `<div class="ntext clamp">${sched}${esc(r.text)}</div>`;
}
function idCell(r) {
  const ref = r.notam_type === "R" ? `<div class="muted">replaces ${esc(r.ref_id)}</div>` : "";
  return `<span class="nid" data-open>${esc(r.notam_id)}</span>${r.is_new && S.view === "active" ? '<span class="badge-new">NEW</span>' : ""}${ref}` +
    `<div class="muted">${esc(r.sources)}</div>`;
}
function locCell(r) {
  return `<b>${esc(r.location || "—")}</b><small>${esc(r.airport_name || "")}</small>`;
}
function byLine(r) {
  return r.action_by ? `<div class="muted">${esc(r.action_by)} · ${ago(r.action_at)}</div>` : "";
}
function statusLabel(r) {
  if (r.status === "done") return { NO: "NO ACTION REQD", EXT: "EXTENDED" }[r.action_needed] || "DONE";
  if (r.status === "pending" && r.action_needed === "EXT") return "EXTENDING · ADD PREV NO.";
  return LABEL[r.status];
}

function rowActive(r) {
  const opt = (v, l) => `<option value="${v}" ${(r.action_needed || "") === v ? "selected" : ""}>${l}</option>`;
  const prev = r.action_needed === "EXT"
    ? `<input type="text" class="prev" data-prev maxlength="12" placeholder="Previous NOTAM no." value="${esc(r.prev_notam)}">` : "";
  return `<tr class="s-${r.status}" data-id="${r.id}">
    <td><span class="status">${statusLabel(r)}</span></td>
    <td>${idCell(r)}</td>
    <td class="loc">${locCell(r)}</td>
    <td class="when">${fmtZ(r.valid_from)}</td>
    <td class="when">${validTo(r)}</td>
    <td>${textCell(r)}</td>
    <td>${tags(r)}</td>
    <td><select data-need>${opt("", "— select —")}${opt("YES", "Yes")}${opt("NO", "No action needed")}${opt("EXT", "Extending")}</select>${prev}</td>
    <td><textarea data-taken placeholder="${r.action_needed === "NO" ? "Remarks (optional)" : "Describe the action taken…"}">${esc(r.action_taken)}</textarea>${byLine(r)}</td>
    <td><button class="del" title="Delete from sheet">🗑</button></td>
  </tr>`;
}

function rowArchive(r) {
  const need = (NEED[r.action_needed] || "—") + (r.prev_notam ? `<div class="muted">prev ${esc(r.prev_notam)}</div>` : "");
  return `<tr class="s-archived" data-id="${r.id}">
    <td>${idCell(r)}</td>
    <td class="loc">${locCell(r)}</td>
    <td class="when">${fmtZ(r.valid_from)}</td>
    <td class="when">${validTo(r)}</td>
    <td>${textCell(r)}</td>
    <td>${need}</td>
    <td style="max-width:260px;white-space:pre-wrap">${esc(r.action_taken) || "—"}${byLine(r)}</td>
    <td><b>${esc(r.archive_reason || "")}</b></td>
    <td class="when">${fmtZ(r.archived_at)}</td>
    <td><button class="del" title="Delete permanently">🗑</button></td>
  </tr>`;
}

function render() {
  counters();
  const ap = S.view === "airports";
  $("airportsView").hidden = !ap;
  ["toolbar", "sources", "wrap", "legend"].forEach(id => $(id).hidden = ap);
  $("noAirports").hidden = ap || S.airports.length > 0;
  if (ap) return renderAirports();

  const rows = filtered();
  if (S.view === "active") {
    thead.innerHTML = `<tr><th>Status</th><th>NOTAM</th><th>Location</th><th>Valid From</th><th>Valid To</th>
      <th>NOTAM Text</th><th>Tags</th><th>Action Needed?</th><th>Action Taken</th><th></th></tr>`;
  } else {
    thead.innerHTML = `<tr><th>NOTAM</th><th>Location</th><th>Valid From</th><th>Valid To</th><th>NOTAM Text</th>
      <th>Action Needed?</th><th>Action Taken</th><th>Reason</th><th>Moved On</th><th></th></tr>`;
  }
  const shown = rows.slice(0, S.limit);
  tbody.innerHTML = shown.map(S.view === "active" ? rowActive : rowArchive).join("") ||
    `<tr><td colspan="11" class="empty">${S.rows[S.view].length ? "No NOTAMs match the filters." :
      S.view === "active" ? "No NOTAMs for the selected airports yet." : "Nothing cancelled or expired yet."}</td></tr>`;
  $("more").innerHTML = rows.length > S.limit
    ? `<span class="muted">Showing ${S.limit} of ${rows.length}</span> <button class="btn small" id="btnMore">Show more</button>` : "";
}

function renderAirports() {
  $("apBody").innerHTML = S.airports.map(a => `<tr>
      <td><b>${esc(a.icao)}</b></td><td>${esc(a.name)}</td><td>${a.active}</td>
      <td class="muted">${esc(a.added_by || "")}</td><td class="muted">${fmtZ(a.added_at)}</td>
      <td><button class="del" data-icao="${esc(a.icao)}" title="Remove airport">🗑</button></td></tr>`).join("") ||
    `<tr><td colspan="6" class="empty">No airports yet. Add them one by one above, or upload an Excel sheet with ICAO codes.</td></tr>`;
}

// ---------------------------------------------------------------- NOTAM actions
function localStatus(r) {
  if (r.status === "expiring") return "expiring";
  const n = r.action_needed;
  if (n === "NO" || (n === "YES" && (r.action_taken || "").trim()) || (n === "EXT" && (r.prev_notam || "").trim())) return "done";
  return n === "YES" || n === "EXT" ? "pending" : "new";
}

async function save(id, body, focusPrev) {
  await api(`/api/notams/${id}/action`, { method: "POST", body: JSON.stringify(body) });
  const r = S.rows.active.find(x => x.id === id);
  if (r) {
    Object.assign(r, body, { action_by: USER, action_at: new Date().toISOString() });
    if (body.prev_notam !== undefined) r.prev_notam = body.prev_notam.toUpperCase();
    r.status = localStatus(r);
    const tr = tbody.querySelector(`tr[data-id="${id}"]`);
    if (tr) {
      tr.outerHTML = rowActive(r);
      if (focusPrev) tbody.querySelector(`tr[data-id="${id}"] [data-prev]`)?.focus();
    }
    counters();
  }
}

tbody.addEventListener("change", async e => {
  if (!e.target.matches("[data-need]")) return;
  askUser();
  const id = +e.target.closest("tr").dataset.id;
  await save(id, { action_needed: e.target.value }, e.target.value === "EXT");
});

tbody.addEventListener("focusout", async e => {
  const t = e.target;
  if (!t.matches("[data-taken], [data-prev]")) return;
  const id = +t.closest("tr").dataset.id;
  const r = S.rows.active.find(x => x.id === id);
  const val = t.value.trim();
  const field = t.matches("[data-prev]") ? "prev_notam" : "action_taken";
  const same = field === "prev_notam" ? val.toUpperCase() === (r?.prev_notam || "") : val === (r?.action_taken || "");
  if (!r || same) return afterEdit();
  askUser();
  const body = { [field]: val };
  if (field === "action_taken" && val && !r.action_needed) body.action_needed = "YES";   // writing an action implies action was needed
  await save(id, body);
  afterEdit();
});
tbody.addEventListener("keydown", e => {
  if (e.target.matches("[data-taken], [data-prev]") && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.target.blur(); }
});
function afterEdit() { if (S.pending) { S.pending = false; load(true); } }

tbody.addEventListener("click", async e => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  const id = +tr.dataset.id;
  if (e.target.closest(".del")) {
    const r = S.rows[S.view].find(x => x.id === id);
    if (!confirm(`Delete NOTAM ${r.notam_id} (${r.location}) from the sheet?\nIt will not be imported again.`)) return;
    askUser();
    await api(`/api/notams/${id}`, { method: "DELETE" });
    S.rows[S.view] = S.rows[S.view].filter(x => x.id !== id);
    render();
    toast(`${r.notam_id} deleted`);
  } else if (e.target.closest("[data-open]")) {
    openDetail(id);
  } else if (e.target.closest(".ntext")) {
    e.target.closest(".ntext").classList.toggle("clamp");
  }
});

async function openDetail(id) {
  const r = await api(`/api/notams/${id}`);
  $("detail").innerHTML = `
    <h3>${esc(r.notam_id)} · ${esc(r.location)} <span class="muted" style="font-weight:400">${esc(r.airport_name || "")}</span></h3>
    <div class="kv">
      <div>FIR</div><div>${esc(r.fir)}</div>
      <div>Type</div><div>${{ N: "New", R: "Replace " + esc(r.ref_id), C: "Cancel " + esc(r.ref_id) }[r.notam_type] || ""}</div>
      <div>Valid</div><div>${fmtZ(r.valid_from)} → ${validTo(r)}</div>
      ${r.schedule ? `<div>Schedule</div><div>${esc(r.schedule)}</div>` : ""}
      ${r.q_code ? `<div>Q-code</div><div>${esc(r.q_code)}</div>` : ""}
      ${r.lower_lim || r.upper_lim ? `<div>Limits</div><div>${esc(r.lower_lim)} – ${esc(r.upper_lim)}</div>` : ""}
      <div>Source</div><div>${esc(r.sources)} · first seen ${fmtZ(r.first_seen)}</div>
      <div>Perf. critical</div><div>${r.crit_tags.length ? "Yes: " + esc(r.crit_tags.join(", ")) : "No"}</div>
      ${r.action_needed ? `<div>Action needed</div><div>${esc(NEED[r.action_needed])}${r.prev_notam ? " · previous NOTAM " + esc(r.prev_notam) : ""}</div>` : ""}
      ${r.archive_reason ? `<div>Archived</div><div>${esc(r.archive_reason)}</div>` : ""}
    </div>
    <pre>${esc(r.text)}</pre>
    <div class="hist"><b>History</b>${r.history.map(h => `<div><span class="muted">${fmtZ(h.at)} · ${esc(h.by)}</span>: ${esc(h.what)}</div>`).join("") || '<div class="muted">No changes yet.</div>'}</div>
    <div class="row"><button class="btn" data-close>Close</button></div>`;
  $("detailBox").classList.add("show");
}

// ---------------------------------------------------------------- airports
async function addAirports(list) {
  askUser();
  const res = await api("/api/airports", { method: "POST", body: JSON.stringify({ airports: list }) });
  await load(false);
  return res.added;
}

$("apAdd").onclick = async () => {
  const icao = $("apIcao").value.trim().toUpperCase();
  if (!/^[A-Z]{4}$/.test(icao)) { toast("Enter a 4-letter ICAO code, e.g. VIDP"); return; }
  if (S.airports.some(a => a.icao === icao)) { toast(`${icao} is already in the list`); return; }
  await addAirports([{ icao, name: $("apName").value.trim() }]);
  $("apIcao").value = ""; $("apName").value = ""; $("apIcao").focus();
  toast(`${icao} added`);
};
$("apIcao").addEventListener("keydown", e => { if (e.key === "Enter") $("apAdd").click(); });
$("apName").addEventListener("keydown", e => { if (e.key === "Enter") $("apAdd").click(); });

$("apBody").addEventListener("click", async e => {
  const b = e.target.closest("[data-icao]");
  if (!b || !confirm(`Remove ${b.dataset.icao} from the monitored airports?`)) return;
  await api(`/api/airports/${b.dataset.icao}`, { method: "DELETE" });
  await load(false);
  toast(`${b.dataset.icao} removed`);
});

$("apClear").onclick = async () => {
  if (!S.airports.length || !confirm(`Remove all ${S.airports.length} airports? The sheets will then show every airport.`)) return;
  await api("/api/airports", { method: "DELETE" });
  await load(false);
};

$("apUpload").onclick = () => $("apFile").click();
$("apFile").onchange = async () => {
  const f = $("apFile").files[0];
  $("apFile").value = "";
  if (!f) return;
  const r = await fetch(`/api/airports/detect?filename=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
  if (!r.ok) { toast((await r.json()).detail || "Could not read the file"); return; }
  const found = await r.json();
  if (!found.length) { toast("No ICAO airport codes (like VIDP) found in that file"); return; }
  const have = new Set(S.airports.map(a => a.icao));
  $("detectFile").textContent = f.name;
  $("detectList").innerHTML = found.map(a => `<label>
      <input type="checkbox" value="${esc(a.icao)}" data-name="${esc(a.name)}" ${have.has(a.icao) ? "disabled" : "checked"}>
      <b style="width:48px">${esc(a.icao)}</b> <span style="flex:1">${esc(a.name)}</span>
      <span class="muted">${have.has(a.icao) ? "already in list" : a.has_notams ? "has NOTAMs" : ""}</span></label>`).join("");
  $("detectAll").checked = true;
  $("detectBox").classList.add("show");
};
$("detectAll").onchange = () => $("detectList").querySelectorAll("input:not(:disabled)").forEach(c => c.checked = $("detectAll").checked);
$("detectGo").onclick = async () => {
  const list = [...$("detectList").querySelectorAll("input:checked:not(:disabled)")].map(c => ({ icao: c.value, name: c.dataset.name }));
  $("detectBox").classList.remove("show");
  if (!list.length) return;
  const n = await addAirports(list);
  toast(`${n} airport${n === 1 ? "" : "s"} added`);
};

// ---------------------------------------------------------------- toolbar
function setView(v) {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x.dataset.view === v));
  S.view = v; S.limit = 200;
  fillLocations(); render();
}
document.querySelectorAll(".tab").forEach(t => t.onclick = () => setView(t.dataset.view));
$("goAirports").onclick = () => setView("airports");
["q", "loc", "st", "crit"].forEach(id => $(id).addEventListener("input", () => { S.limit = 200; render(); }));
$("counters").addEventListener("click", e => {
  const c = e.target.closest(".counter");
  if (!c) return;
  const f = c.dataset.f;
  if (S.view !== "active") setView("active");
  $("crit").checked = f === "crit";
  $("st").value = f === "crit" ? "" : f;
  render();
});
$("more").addEventListener("click", e => { if (e.target.id === "btnMore") { S.limit += 300; render(); } });
$("btnExport").onclick = () => { location.href = `/api/export?view=${S.view}`; };
$("btnFetch").onclick = async () => {
  await api("/api/fetch", { method: "POST" });
  toast("Checking all sources… new NOTAMs will appear automatically.");
  setTimeout(loadSources, 4000);
};
$("btnTestMail").onclick = async () => {
  try { toast((await api("/api/test-mail", { method: "POST" })).message); }
  catch (e) { const m = e.message.match(/"detail":"(.*)"}$/); toast(m ? m[1] : e.message); }
};
$("btnPaste").onclick = () => { $("pasteBox").classList.add("show"); $("pasteText").focus(); };
$("pasteGo").onclick = async () => {
  const text = $("pasteText").value;
  if (!text.trim()) return;
  const res = await api("/api/notams/paste", { method: "POST", body: JSON.stringify({ text, fir: $("pasteFir").value }) });
  $("pasteBox").classList.remove("show");
  $("pasteText").value = "";
  toast(res.found ? `${res.found} NOTAM(s) read, ${res.added} new added` : "No NOTAM found in the pasted text. Check the format.");
  load(false);
};
document.addEventListener("click", e => {
  if (e.target.matches("[data-close]") || e.target.classList.contains("overlay")) {
    document.querySelectorAll(".overlay").forEach(o => o.classList.remove("show"));
  }
});

// ---------------------------------------------------------------- start
(async () => {
  initTopbar();
  S.cfg = await api("/api/config");
  $("expD").textContent = S.cfg.expiring_days;
  $("retD").textContent = S.cfg.retention_days;
  await load(false);
  await poll();
  setInterval(poll, 15000);
  setInterval(() => { if (!editing()) load(true); }, 5 * 60000);   // re-check colours (expiring) and "x min ago"
})();
