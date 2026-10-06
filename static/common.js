// Shared helpers: user name, escaping, dates, fetch.
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage blocked */ } },
};
let USER = store.get("opsUser") || "";

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtZ(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = n => String(n).padStart(2, "0");
  return `${p(d.getUTCDate())} ${MON[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)} ${p(d.getUTCHours())}${p(d.getUTCMinutes())}Z`;
}
function ago(iso) {
  if (!iso) return "never";
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 1440) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} d ago`;
}

async function api(url, opts = {}) {
  opts.headers = Object.assign({ "Content-Type": "application/json", "X-User": encodeURIComponent(USER) }, opts.headers || {});
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function toast(msg) {
  let t = document.querySelector(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 3500);
}

function askUser(force) {
  if (USER && !force) return;
  const name = prompt("Your name (shown next to the actions you take):", USER || "");
  if (name && name.trim()) { USER = name.trim(); store.set("opsUser", USER); }
  const chip = document.getElementById("userchip");
  if (chip) chip.textContent = "👤 " + (USER || "Set your name");
}

function initTopbar() {
  const chip = document.getElementById("userchip");
  if (chip) {
    chip.textContent = "👤 " + (USER || "Set your name");
    chip.onclick = () => askUser(true);
  }
}
