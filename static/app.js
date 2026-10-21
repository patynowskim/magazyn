"use strict";

// ---------- stan i narzędzia ----------
const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");
let token = null;
let me = null;

try { token = localStorage.getItem("token"); } catch (e) {}
const saveToken = (t) => {
  token = t;
  try { t ? localStorage.setItem("token", t) : localStorage.removeItem("token"); } catch (e) {}
};

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v == null ? "—" : Number(v).toFixed(2));
const dt = (s) => (s ? s.replace("T", " ").replace("Z", "") : "");

// ---------- pasek statusu (jak w starym IE) ----------
// komunikaty: "Gotowe" / "Ładowanie…" (+ pasek postępu przy fetchach) / ostatni komunikat lub błąd
let pending = 0;
let sticky = null; // {text, kind, until} - ostatni komunikat z notify()
let info = "Gotowe"; // stały opis bieżącego widoku (np. "Pozycji: 12")
let hover = "";      // podpowiedź po najechaniu na link / przycisk (jak w IE)
const STICKY_MS = 5000;


// pola z liczbami widoku (np. Pozycji / Sztuk / Wartość), ustawiane przez każdy widok
let stats = [];
let online = true;
const setStats = (arr) => { stats = arr; renderStats(); };
const renderStats = () => {
  $("#status-stats").innerHTML = stats.map((s) =>
    `<span class="panel${s.cls ? ` ${s.cls}` : ""}"${s.title ? ` title="${esc(s.title)}"` : ""}>${s.label ? `<b>${esc(s.label)}</b> ` : ""}${esc(s.value)}</span>`).join("");
};
const num = (n) => Number(n || 0).toLocaleString("pl-PL");
const fail = (err) => notify(`Błąd${err.status ? ` ${err.status}` : ""}: ${err.message}`, "error");

function renderStatus() {
  const bar = $("#statusbar");
  let text = info, kind = "";
  const live = sticky && (sticky.kind === "error" || Date.now() < sticky.until);
  if (live && sticky.kind === "error") { text = sticky.text; kind = "error"; }
  else if (pending > 0) text = "Ładowanie…";
  else if (hover) text = hover;
  else if (live) { text = sticky.text; kind = sticky.kind; }
  $("#status-text").textContent = text;
  bar.className = kind;
  $("#status-progress").hidden = pending === 0;
  const conn = $("#status-conn");
  conn.textContent = online ? "● Połączono" : "○ Brak połączenia";
  conn.className = online ? "panel" : "panel warn";
}

// pola globalne: ile pozycji się kończy, ostatnia operacja (odświeżane po zmianach)
let globalTimer;
function refreshGlobal() {
  clearTimeout(globalTimer);
  globalTimer = setTimeout(async () => {
    if (!me) return;
    try {
      const [low, mv, sum] = await Promise.all([api("GET", "/low-stock", undefined, true), api("GET", "/movements?limit=1", undefined, true),
        api("GET", "/orders/summary", undefined, true)]);
      const cart = sum.cart;
      $("#status-cart").innerHTML = `<a href="#/orders" title="Koszyk: ${cart.items} poz., zamówione w drodze: ${sum.ordered.items}${sum.suggestions ? `, sugestii: ${sum.suggestions}` : ""}">🛒 ${cart.items ? `Koszyk: ${cart.items}${cart.value ? ` (${money(cart.value)})` : ""}` : "Koszyk pusty"}</a>`;
      $("#status-cart").className = sum.suggestions ? "panel warn" : "panel";
      $("#status-low").innerHTML = low.length
        ? `<a href="#/low-stock" title="${esc(low.map((c) => `${c.name} (brakuje ${c.shortage})`).join(", "))}">⚠ Kończy się: ${low.length}</a>`
        : "✓ Zapasy w normie";
      $("#status-low").className = low.length ? "panel warn" : "panel ok";
      const m = mv[0];
      $("#status-last").textContent = m ? `Ostatnio: ${KIND[m.kind] || m.kind}${m.by_user ? `, ${m.by_user}` : ""}, ${shortTime(m.ts)}` : "Brak operacji";
      $("#status-last").title = m ? `${m.qty} szt. „${m.component}” (${dt(m.ts)} UTC)${m.note ? ` — ${m.note}` : ""}` : "";
    } catch (e) { /* pola globalne są pomocnicze */ }
  }, 350);
}
function shortTime(ts) {
  const d = new Date(ts);
  const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("pl-PL", { day: "2-digit", month: "2-digit" }) + " " + d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
}
const tick = () => { $("#status-clock").textContent = new Date().toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" }); };
setInterval(tick, 15000);

// na ekranie logowania paska nie ma, więc komunikat idzie do okienka logowania
function authMessage(text, kind) {
  const box = $(".login");
  if (!box) return;
  let el = $(".auth-msg", box);
  if (!el && !text) return;   // pusty komunikat nie tworzy elementu (zmieniałby wysokość okienka)
  if (!el) { el = document.createElement("p"); el.className = "auth-msg"; box.appendChild(el); }
  el.textContent = text || "";
  el.className = `auth-msg ${text ? kind : ""}`;
}

function notify(text, kind = "ok") {
  sticky = text ? { text, kind, until: Date.now() + STICKY_MS } : null;
  if (document.body.classList.contains("auth")) authMessage(text, kind);
  renderStatus();
  if (text && kind !== "error") setTimeout(renderStatus, STICKY_MS + 50);
}

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(method, path, body, quiet = false) {
  if (!quiet) { pending++; renderStatus(); }
  try {
    const data = await apiCall(method, path, body);
    if (!online) { online = true; renderStatus(); }
    if (method !== "GET" && !path.startsWith("/auth")) refreshGlobal();   // zmiana danych -> odśwież pola globalne
    return data;
  } catch (err) {
    if (err instanceof TypeError) {
      online = false;
      throw new ApiError(0, "Brak połączenia z serwerem");
    }
    throw err;
  } finally { if (!quiet) { pending--; } renderStatus(); }
}

async function apiCall(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) {
    let msg = data?.detail ?? res.statusText;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    if (res.status === 401 && token) { saveToken(null); me = null; location.hash = "#/login"; }
    throw new ApiError(res.status, msg);
  }
  return data;
}
const GET = (p) => api("GET", p);
const POST = (p, b = {}) => api("POST", p, b);
const PATCH = (p, b) => api("PATCH", p, b);
const DEL = (p) => api("DELETE", p);
const qs = (o) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== "" && v != null) p.set(k, v);
  const s = p.toString();
  return s ? `?${s}` : "";
};

// formularz -> obiekt JSON (puste pola pomijane, liczby jako liczby, *_id jako int)
function formObj(form) {
  const o = {};
  for (const el of form.elements) {
    if (!el.name || el.type === "submit" || el.type === "button") continue;
    if (el.type === "checkbox") { o[el.name] = el.checked; continue; }
    if (el.value === "") continue;
    o[el.name] = el.type === "number" || el.name.endsWith("_id") ? Number(el.value) : el.value;
  }
  return o;
}

// wysyłka formularza: submit -> handler(obiekt) -> komunikat; błędy w #message
function onSubmit(form, handler) {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    notify("");
    try { await handler(formObj(form), form); }
    catch (err) { fail(err); }
  });
}

const options = (items, valueKey, labelFn, { blank } = {}) =>
  (blank != null ? `<option value="">${esc(blank)}</option>` : "") +
  items.map((i) => `<option value="${esc(i[valueKey])}">${esc(labelFn(i))}</option>`).join("");

// ---------- tabele ----------
// Układ jak na szkicu: zakładka z wyszukiwarką na górze po prawej, nagłówki z przyciskami sortowania
// i filtrowania, kolumna lp, wiersz dodawania na dole, zakładka z liczbą rekordów pod tabelą.
const lpCol = ["lp", () => "", "lp"];
const ICON_SORT = `<svg viewBox="0 0 10 14" width="10" height="14" aria-hidden="true"><path class="u" d="M5 1 1 6h8z"/><path class="d" d="M5 13 1 8h8z"/></svg>`;
const ICON_FILTER = `<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M1 1h10L7 6v5L5 9V6z"/></svg>`;

const theadHtml = (cols) => `<thead><tr>${[lpCol, ...cols].map((c, i) => `<th class="${c[2] || ""}" data-title="${esc(c[0])}"><div class="th"><span>${esc(c[0])}</span>${
  i > 0 && c[0] ? `<span class="th-tools"><button type="button" class="th-btn" data-sort title="Sortuj po: ${esc(c[0])}">${ICON_SORT}</button><button type="button" class="th-btn" data-filter title="Filtruj: ${esc(c[0])}">${ICON_FILTER}</button></span>` : ""}</div></th>`).join("")}</tr></thead>`;

const rowsHtml = (cols, rows) => rows.map((r, i) =>
  `<tr data-i="${i}">${[lpCol, ...cols].map((c) => `<td${c[2] ? ` class="${c[2]}"` : ""}>${c[1](r, i)}</td>`).join("")}</tr>`).join("");

const wrapHtml = (cols, body, add = "", title = "") => `<div class="tablewrap">
  <div class="tabs">${title ? `<span class="tab title">${esc(title)}</span>` : ""}<label class="tab search"><input class="tsearch" type="search" placeholder="Szukaj…" autocomplete="off"></label></div>
  <table>${theadHtml(cols)}<tbody>${body}</tbody>${add ? `<tfoot>${add.replaceAll('<tr class="newrow">', '<tr class="newrow"><td class="lp plus">+</td>')}</tfoot>` : ""}</table>
  <div class="tabs bottom"><span class="tab count"></span></div></div>`;

// tabela statyczna (bez odświeżania części danych)
function table(cols, rows, empty = "Brak danych", title = "") {
  return wrapHtml(cols, rows.length ? rowsHtml(cols, rows) : emptyRow(cols, empty), "", title);
}

// komunikat o braku danych jako wiersz w środku tabeli (między tytułem a dołem)
const emptyRow = (cols, text, cls = "") => `<tr class="emptyrow ${cls}"><td colspan="${cols.length + 1}">${esc(text)}</td></tr>`;

// tabela z szkieletem: tytuły i wiersz dodawania są stałe, odświeża się tylko tbody,
// więc to, co wpisałeś (szukanie, filtry, nowy wiersz), przeżywa odświeżenie danych
const tableShell = (cols, { add = "", title = "" } = {}) => wrapHtml(cols, "", add, title);
function tableFill(root, cols, rows, empty = "Brak danych") {
  $("tbody", root).innerHTML = rows.length ? rowsHtml(cols, rows) : emptyRow(cols, empty);
  applyView($(".tablewrap", root));
}

// --- sortowanie, filtrowanie kolumn i szukanie po stronie przeglądarki ---
const gridState = (wrap) => (wrap._grid ??= { sort: null, filters: {} });
const cellVal = (tr, i) => {
  const td = tr.cells[i];
  const f = td?.querySelector("input:not([type=checkbox]), select");
  return (f ? (f.tagName === "SELECT" ? f.selectedOptions[0]?.text : f.value) : td?.textContent ?? "").trim();
};
const asNum = (s) => (/^[-+]?[\d\s.,]+$/.test(s) ? parseFloat(s.replace(/\s/g, "").replace(",", ".")) : NaN);

function applyView(wrap) {
  if (!wrap) return;
  const g = gridState(wrap);
  const tbody = $("tbody", wrap);
  const trs = [...tbody.rows].filter((tr) => !tr.classList.contains("emptyrow"));
  // sortowanie (bez sortowania: kolejność z serwera)
  const dir = g.sort?.dir === "desc" ? -1 : 1;
  trs.sort((a, b) => {
    if (!g.sort) return a.dataset.i - b.dataset.i;
    const x = cellVal(a, g.sort.i), y = cellVal(b, g.sort.i);
    const nx = asNum(x), ny = asNum(y);
    const c = !isNaN(nx) && !isNaN(ny) ? nx - ny : x.localeCompare(y, "pl", { numeric: true, sensitivity: "base" });
    return c * dir;
  });
  trs.forEach((tr) => tbody.appendChild(tr));
  // szukanie + filtry kolumn
  const words = ($(".tsearch", wrap)?.value || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
  const colFilters = Object.entries(g.filters).filter(([, v]) => v);
  let shown = 0;
  for (const tr of trs) {
    const text = [...tr.cells].map((_, i) => cellVal(tr, i)).join(" ").toLowerCase();
    const ok = words.every((w) => text.includes(w))
      && colFilters.every(([i, v]) => cellVal(tr, Number(i)).toLowerCase().includes(v.toLowerCase()));
    tr.hidden = !ok;
    if (ok) shown++;
  }
  // są wiersze, ale filtry wszystkie ukryły: komunikat w tabeli
  tbody.querySelector(".nomatch")?.remove();
  if (trs.length && !shown) tbody.insertAdjacentHTML("beforeend", emptyRow({ length: wrap.querySelector("thead tr").cells.length - 1 }, "Brak wyników dla wpisanych filtrów", "nomatch"));
  // wskaźniki w nagłówku i licznik pod tabelą
  wrap.querySelectorAll("th").forEach((th, i) => {
    th.dataset.sorted = g.sort?.i === i ? g.sort.dir : "";
    th.classList.toggle("filtered", !!g.filters[i]);
  });
  const filtered = shown !== trs.length;
  const fs = $("#status-filter");
  fs.hidden = !filtered;
  fs.textContent = filtered ? `Filtr: ${shown} z ${trs.length}` : "";
  wrap._stats?.();
  $(".count", wrap).textContent = trs.length || filtered ? `${filtered ? `${shown} z ${trs.length} rekordów` : `${trs.length} ${plRecords(trs.length)}`}` : "0 rekordów";
}
const plRecords = (n) => (n === 1 ? "rekord" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "rekordy" : "rekordów");

function closePopover() { document.querySelectorAll(".popover").forEach((p) => p.remove()); }
function openFilter(wrap, th, i) {
  closePopover();
  const g = gridState(wrap);
  const pop = document.createElement("div");
  pop.className = "popover";
  pop.innerHTML = `<input type="search" placeholder="Filtruj: ${esc(th.dataset.title)}" autocomplete="off"><button type="button" data-clearcol>Wyczyść</button>`;
  const w = wrap.getBoundingClientRect(), t = th.getBoundingClientRect();
  pop.style.left = `${Math.max(0, Math.min(t.left - w.left, w.width - 260))}px`;
  pop.style.top = `${t.bottom - w.top + 6}px`;
  wrap.appendChild(pop);
  const input = $("input", pop);
  input.value = g.filters[i] || "";
  input.focus();
  input.addEventListener("input", () => { g.filters[i] = input.value; applyView(wrap); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === "Escape") closePopover(); });
  $("[data-clearcol]", pop).onclick = () => { g.filters[i] = ""; applyView(wrap); closePopover(); };
}

view.addEventListener("input", (e) => { if (e.target.classList.contains("tsearch")) applyView(e.target.closest(".tablewrap")); });
view.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && e.target.classList.contains("tsearch")) { e.target.value = ""; applyView(e.target.closest(".tablewrap")); }
});
view.addEventListener("click", (e) => {
  const sortBtn = e.target.closest("[data-sort]");
  const filtBtn = e.target.closest("[data-filter]");
  if (!sortBtn && !filtBtn) return;
  const th = e.target.closest("th"), wrap = th.closest(".tablewrap");
  const i = th.cellIndex, g = gridState(wrap);
  if (sortBtn) {  // brak -> rosnąco -> malejąco -> brak
    g.sort = g.sort?.i !== i ? { i, dir: "asc" } : g.sort.dir === "asc" ? { i, dir: "desc" } : null;
    applyView(wrap);
  } else openFilter(wrap, th, i);
});
document.addEventListener("click", (e) => { if (!e.target.closest(".popover, [data-filter]")) closePopover(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopover(); });

// ---------- okno modalne (popup z animacją) ----------
function modal(title, bodyHtml, { wide = false, onClose } = {}) {
  const d = document.createElement("dialog");
  d.className = "modal";
  d.innerHTML = `<div class="modal-box${wide ? " wide" : ""}"><div class="modal-head"><h3>${esc(title)}</h3>
    <button type="button" class="modal-x" aria-label="Zamknij" title="Zamknij">×</button></div>
    <div class="modal-body">${bodyHtml}<p class="modal-msg" role="alert"></p></div></div>`;
  view.appendChild(d);   // wewnątrz #view, żeby tabele i pola w popupie miały te same style
  const close = () => {
    if (!d.isConnected || d.classList.contains("closing")) return;
    d.classList.add("closing");
    const done = () => { if (d.isConnected) { d.close(); d.remove(); onClose?.(); } };
    d.addEventListener("animationend", (e) => { if (e.target === d || e.target.classList?.contains("modal-box")) done(); });
    setTimeout(done, 600);
  };
  d.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  d.addEventListener("click", (e) => { if (e.target === d) close(); });   // klik w tło
  $(".modal-x", d).onclick = close;
  d.showModal();
  return { el: d, close, error: (m) => { $(".modal-msg", d).textContent = m || ""; } };
}

const isAdmin = () => me?.role === "admin";

// komórka edytowalna "w miejscu"; zapis idzie sam przy wpisywaniu (patrz autosave)
const cellInput = (field, id, value, type = "text", extra = "") =>
  `<input class="cell" data-save="${field}" data-id="${id}" type="${type}" value="${esc(value ?? "")}" ${extra} autocomplete="off">`;

// zapis bezpośrednio po zmianie: pola tekstowe/liczbowe z opóźnieniem (debounce) podczas pisania,
// listy i checkboxy (data-instant) od razu. save(el) rzuca wyjątek przy błędzie.
function autosave(root, save, { onError, delay = 500 } = {}) {
  const timers = new Map();
  const run = async (el) => {
    el.classList.remove("saved", "err");
    el.classList.add("saving");
    try {
      await save(el);
      el.classList.remove("saving");
      el.classList.add("saved");
      setTimeout(() => el.classList.remove("saved"), 1200);
    } catch (err) {
      el.classList.remove("saving");
      el.classList.add("err");
      fail(err);
      if (onError) await onError(el, err);
    }
  };
  root.addEventListener("input", (e) => {
    const el = e.target;
    if (!el.dataset?.save || "instant" in el.dataset) return;
    clearTimeout(timers.get(el));
    el.classList.remove("saved", "err");
    el.classList.add("saving");
    timers.set(el, setTimeout(() => run(el), delay));
  });
  root.addEventListener("change", (e) => {
    const el = e.target;
    if (el.dataset?.save && "instant" in el.dataset) run(el);
  });
}
const fieldValue = (el) => (el.type === "checkbox" ? el.checked : el.value);

// opis wiersza pod kursorem: "Komponent: … · Właściciel: … · Ilość: …"
function rowHint(tr) {
  const heads = [...tr.closest("table").tHead.rows[0].cells];
  return [...tr.cells].map((td, i) => {
    const title = heads[i]?.dataset.title;
    const v = cellVal(tr, i);
    return i > 0 && title && v && v !== "—" ? `${title}: ${v}` : "";
  }).filter(Boolean).join(" · ");
}

// podpowiedź w pasku po najechaniu na link / przycisk / wiersz
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest("a[href], button");
  const label = el && (el.dataset.hint || el.title || el.textContent.trim());
  const tr = !el && e.target.closest("tbody tr:not(.emptyrow)");
  hover = el && label ? (el.matches("a[href]") ? `Przejdź do: ${label}` : label) : tr ? rowHint(tr) : "";
  renderStatus();
});
document.addEventListener("mouseout", (e) => { if (!e.relatedTarget) { hover = ""; renderStatus(); } });

// ---------- widoki ----------
const routes = {};

routes["/setup"] = async () => {
  $("#auth").innerHTML = `<section class="login"><img class="logo" src="logo-trim.png" alt="Magazyn"><h2>Pierwsze uruchomienie: utwórz administratora</h2>
    <form id="f"><label>Login <input name="username" required autocomplete="username"></label>
    <label>Hasło (min. 8 znaków) <input name="password" type="password" minlength="8" required autocomplete="new-password"></label>
    <button>Utwórz konto</button></form><p class="auth-msg"></p></section>`;
  onSubmit($("#f"), async (d) => {
    await POST("/users", d);
    await doLogin(d.username, d.password);
  });
};

routes["/login"] = async () => {
  $("#auth").innerHTML = `<div id="vidc"><video autoplay muted loop playsinline preload="auto" poster="magv-poster.jpg" id="bgvid"><source src="magv.mp4" type="video/mp4"></video>
    <section class="login"><img class="logo" src="logo-trim.png" alt="Magazyn"><h2>Logowanie</h2>
    <form id="f"><label>Login <input name="username" required autocomplete="username"></label><br>
    <label>Hasło <input name="password" type="password" required autocomplete="current-password"></label><br>
    <button>Zaloguj</button></form><p class="auth-msg"></p></section></div>`;
  onSubmit($("#f"), (d) => doLogin(d.username, d.password));
};

// czas animacji z CSS (--anim), 0 przy prefers-reduced-motion
const animMs = () => {
  const v = getComputedStyle(document.documentElement).getPropertyValue("--anim").trim();
  return v.endsWith("ms") ? parseFloat(v) : v.endsWith("s") ? parseFloat(v) * 1000 : 0;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function doLogin(username, password) {
  const r = await POST("/auth/login", { username, password });
  saveToken(r.token);
  me = await GET("/auth/me");
  const body = document.body;
  // 1) tło i okienko logowania opadają
  body.classList.add("leaving");
  await sleep(animMs());
  // 2) treść wjeżdża z góry
  $("#auth").innerHTML = "";
  body.classList.replace("leaving", "entering");
  renderChrome();
  location.hash = "#/stock";
  await sleep(animMs() + 150);
  body.classList.remove("entering");
  notify(`Zalogowano jako ${me.username} (${me.role})`);
}

// --- stany ---
routes["/stock"] = async () => {
  const [components, people, locations] = await Promise.all([GET("/components"), GET("/people"), GET("/locations")]);
  view.innerHTML = `<h2>Stany</h2>
    <form id="addform"></form>
    <datalist id="locs">${locations.filter((l) => l.location).map((l) => `<option value="${esc(l.location)}">`).join("")}</datalist>
    <div id="action"></div><div id="list"></div>`;

  // wiersz dodawania: pola w komórkach tabeli, powiązane z formularzem #addform atrybutem form
  const addRow = `<tr class="newrow">
    <td><select class="cell" name="component_id" form="addform" required>${options(components, "id", (c) => `${c.name}${c.part_number ? ` (${c.part_number})` : ""}`, { blank: "+ komponent" })}</select></td>
    <td><select class="cell" name="owner_id" form="addform" required>${options(people, "id", (p) => p.name, { blank: "właściciel" })}</select></td>
    <td><select class="cell" name="holder_id" form="addform">${options(people, "id", (p) => p.name, { blank: "= właściciel" })}</select></td>
    <td><input class="cell" name="location" form="addform" list="locs" placeholder="lokalizacja" autocomplete="off"></td>
    <td class="num"><input class="cell" name="qty" type="number" min="1" form="addform" required placeholder="szt."></td>
    <td class="num"><input class="cell" name="unit_cost" type="number" min="0" step="any" form="addform" placeholder="cena"></td>
    <td class="num"><span id="nr-value">—</span><small id="nr-hint"></small></td>
    <td><input class="cell" name="note" form="addform" placeholder="notatka"> <button form="addform">Dodaj</button></td></tr>`;

  let rows = [];
  let previewAdd = null;   // wartość wpisywana w wierszu dodawania (podgląd w pasku)

  // dynamiczny podgląd na bieżąco podczas wpisywania
  const live = () => {
    const f = $("#addform");
    const el = (n) => f.elements.namedItem(n);
    if (!el("qty")) return;
    const qty = Number(el("qty").value) || 0;
    const cost = el("unit_cost").value === "" ? null : Number(el("unit_cost").value);
    const added = qty && cost != null ? qty * cost : null;
    $("#nr-value").textContent = added != null ? money(added) : "—";
    const comp = Number(el("component_id").value), owner = Number(el("owner_id").value);
    const holder = Number(el("holder_id").value) || owner;
    const loc = el("location").value;
    const e = comp && owner && rows.find((r) => r.component_id === comp && r.owner_id === owner && r.holder_id === holder && r.location === loc);
    let hint = "";
    if (e) {
      hint = `jest: ${e.qty} szt.`;
      if (qty) {
        let c = e.unit_cost;
        if (cost != null) c = c == null ? cost : (e.qty * c + qty * cost) / (e.qty + qty);
        hint = `→ ${e.qty + qty} szt., śr. ${money(c)}`;
      }
    }
    $("#nr-hint").textContent = hint ? ` ${hint}` : "";
    previewAdd = added;
    updateStats();
  };

  const load = async () => {
    rows = await GET("/stock");
    tableFill($("#list"), cols, rows, "Brak pozycji");
    live();
  };
  const cols = [
    ["Komponent", (r) => `<a href="#/components/${r.component_id}">${esc(r.component)}</a>${r.part_number ? ` <small>${esc(r.part_number)}</small>` : ""}`],
    ["Właściciel", (r) => esc(r.owner)],
    ["Posiadacz", (r) => esc(r.holder)],
    ["Lokalizacja", (r) => cellInput("location", r.id, r.location, "text", 'list="locs" data-instant placeholder="—"')],
    ["Ilość", (r) => cellInput("qty", r.id, r.qty, "number", 'min="0" step="1"'), "num"],
    ["Cena/szt.", (r) => cellInput("unit_cost", r.id, r.unit_cost, "number", 'min="0" step="any" placeholder="—"'), "num"],
    ["Wartość", (r) => `<span class="val" data-v="${r.value ?? ""}">${money(r.value)}</span>`, "num"],
    ["", (r) => `<span class="actions"><button data-act="transfer">Przekaż/przenieś</button>
      <button data-act="owner">Zmień właściciela</button><button data-act="consume">Zużyj</button></span>`],
  ];
  $("#list").innerHTML = tableShell(cols, { add: addRow });
  $("#list").addEventListener("input", live);

  // statystyki w pasku statusu liczone z widocznych wierszy; wartość wiersza liczy się przy każdym znaku
  const updateStats = () => {
    let n = 0, qty = 0, val = 0;
    const comps = new Set();
    for (const tr of $("#list tbody").rows) {
      const q = tr.querySelector('[data-save="qty"]'), c = tr.querySelector('[data-save="unit_cost"]'), v = tr.querySelector(".val");
      if (!q || !v) continue;
      const rowVal = c.value === "" ? null : (Number(q.value) || 0) * Number(c.value);
      v.dataset.v = rowVal ?? "";
      v.textContent = money(rowVal);
      if (tr.hidden) continue;
      n++; qty += Number(q.value) || 0; val += rowVal || 0;
      comps.add(rows[tr.dataset.i]?.component_id);
    }
    const s = [{ label: "Pozycji", value: n }, { label: "Komponentów", value: comps.size }, { label: "Sztuk", value: num(qty) }, { label: "Wartość", value: money(val) }];
    if (previewAdd != null) s.push({ label: "Po przyjęciu", value: money(val + previewAdd), cls: "preview", title: "Wartość stanu po dodaniu wpisywanego wiersza" });
    setStats(s);
  };
  $(".tablewrap", $("#list"))._stats = updateStats;
  $("#list").addEventListener("input", (e) => { if (["qty", "unit_cost"].includes(e.target.dataset?.save)) updateStats(); });
  autosave($("#list"), async (el) => {
    const r = rows.find((x) => x.id === Number(el.dataset.id));
    const field = el.dataset.save, v = el.value;
    let payload;
    if (field === "location") payload = { location: v.trim() };
    else if (field === "qty") {
      if (v === "") throw new Error("Ilość nie może być pusta");
      payload = { qty: Number(v) };
    } else payload = { unit_cost: v === "" ? null : Number(v) };
    const res = await PATCH(`/stock/${r.id}`, payload);
    Object.assign(r, payload);
    r.value = r.unit_cost != null ? r.qty * r.unit_cost : null;
    notify(`Zapisano: „${r.component}” (${r.holder}${r.location ? ` / ${r.location}` : ""}): ${r.qty} szt.${r.unit_cost != null ? ` po ${money(r.unit_cost)}` : ""}`);
    // pozycja zniknęła (0 szt.) albo scaliła się z inną: odśwież listę
    if (res.removed || res.merged) await load();
  }, { onError: () => load() });
  $("#list").addEventListener("change", live);
  $("#list").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (b) actionForm(b.dataset.act, rows[b.closest("tr").dataset.i], people, load);
  });
  onSubmit($("#addform"), async (d) => {
    await POST("/stock/receive", d);
    const comp = components.find((c) => c.id === d.component_id);
    const who = people.find((p) => p.id === (d.holder_id || d.owner_id));
    notify(`Przyjęto ${d.qty} szt. „${comp.name}”${d.location ? ` w ${d.location}` : ""} (u: ${who.name})`);
    $("#addform").reset();
    await load();
  });
  await load();
};

function actionForm(kind, r, people, reload) {
  const title = { transfer: "Przekaż / przenieś", owner: "Zmień właściciela", consume: "Zużyj" }[kind];
  const fields = {
    transfer: `<label>Nowy posiadacz <select name="to_holder_id">${options(people, "id", (p) => p.name, { blank: "(bez zmiany)" })}</select></label>
               <label>Nowa lokalizacja <input name="to_location" placeholder="(bez zmiany)" autocomplete="off"></label>`,
    owner: `<label>Nowy właściciel <select name="to_owner_id" required>${options(people.filter((p) => p.id !== r.owner_id), "id", (p) => p.name, { blank: "wybierz…" })}</select></label>`,
    consume: "",
  }[kind];
  const m = modal(title, `<dl class="modal-info">
      <dt>Komponent</dt><dd>${esc(r.component)}</dd><dt>Właściciel</dt><dd>${esc(r.owner)}</dd>
      <dt>Posiadacz</dt><dd>${esc(r.holder)}</dd><dt>Lokalizacja</dt><dd>${esc(r.location || "—")}</dd>
      <dt>Dostępne</dt><dd>${r.qty} szt.${r.unit_cost != null ? ` po ${money(r.unit_cost)}` : ""}</dd></dl>
    <form class="modal-form">${fields}
      <label>Ilość <input name="qty" type="number" min="1" max="${r.qty}" required autofocus></label>
      <p class="modal-live"></p>
      <label>Notatka <input name="note" autocomplete="off"></label>
      <div class="modal-actions"><button type="button" data-cancel>Anuluj</button><button>OK</button></div></form>`);
  const form = $("form", m.el);
  $("[data-cancel]", m.el).onclick = m.close;
  // podgląd na żywo: ile zostanie i jaka wartość schodzi / przechodzi
  const live = () => {
    const q = Number(form.elements.qty.value) || 0;
    $(".modal-live", m.el).textContent = q ? `Zostanie: ${Math.max(0, r.qty - q)} szt.${r.unit_cost != null ? ` · wartość operacji: ${money(q * r.unit_cost)}` : ""}` : "";
  };
  form.addEventListener("input", live);
  const base = {
    transfer: { path: "/stock/transfer", body: { component_id: r.component_id, owner_id: r.owner_id, from_holder_id: r.holder_id, from_location: r.location } },
    owner: { path: "/stock/change-owner", body: { component_id: r.component_id, from_owner_id: r.owner_id, holder_id: r.holder_id, location: r.location } },
    consume: { path: "/stock/consume", body: { component_id: r.component_id, owner_id: r.owner_id, holder_id: r.holder_id, location: r.location } },
  }[kind];
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    m.error("");
    const d = formObj(form);
    try { await POST(base.path, { ...base.body, ...d }); }
    catch (err) { m.error(`Błąd${err.status ? ` ${err.status}` : ""}: ${err.message}`); return; }
    m.close();
    const name = (id) => people.find((p) => p.id === id)?.name;
    const what = `${d.qty} szt. „${r.component}”`;
    notify({
      transfer: () => `Przekazano ${what}: ${r.holder}${r.location ? ` / ${r.location}` : ""} → ${name(d.to_holder_id) || r.holder}${(d.to_location ?? r.location) ? ` / ${d.to_location ?? r.location}` : ""}`,
      owner: () => `Zmieniono właściciela ${what}: ${r.owner} → ${name(d.to_owner_id)}`,
      consume: () => `Zużyto ${what} (${r.holder})`,
    }[kind]());
    await reload();
  });
}

// --- komponenty ---
const lowBadge = (c) => (c.min_qty > c.total_qty ? '<span class="low">kończy się</span>' : "");

routes["/components"] = async () => {
  const admin = isAdmin();
  view.innerHTML = `<h2>Komponenty</h2>
    <form id="addform"></form>
    <div id="list"></div>`;
  let rows = [];
  const cols = [
    ["Nazwa", (c) => (admin ? cellInput("name", c.id, c.name, "text", "required") : `<a href="#/components/${c.id}">${esc(c.name)}</a>`)],
    ["Nr katalogowy", (c) => (admin ? cellInput("part_number", c.id, c.part_number) : esc(c.part_number))],
    ["Opis", (c) => (admin ? cellInput("description", c.id, c.description) : esc(c.description))],
    ["Razem szt.", (c) => c.total_qty, "num"],
    ["Próg", (c) => (admin ? cellInput("min_qty", c.id, c.min_qty || "", "number", 'min="0" placeholder="0"') : c.min_qty || ""), "num"],
    ["Wartość", (c) => money(c.total_value), "num"],
    ["", (c) => `<span class="lowcell">${lowBadge(c)}</span> <a class="btn" href="#/components/${c.id}">szczegóły</a>`],
  ];
  // wiersz dodawania na końcu tabeli (tylko admin)
  const addRow = `<tr class="newrow">
    <td><input class="cell" name="name" form="addform" required placeholder="+ nowy komponent" autocomplete="off"></td>
    <td><input class="cell" name="part_number" form="addform" placeholder="nr katalogowy" autocomplete="off"></td>
    <td><input class="cell" name="description" form="addform" placeholder="opis" autocomplete="off"></td>
    <td class="num">0</td>
    <td class="num"><input class="cell" name="min_qty" type="number" min="0" form="addform" placeholder="próg"></td>
    <td class="num">—</td>
    <td><span id="nc-low"></span> <button form="addform">Dodaj</button></td></tr>`.replace(/<(input|select) /g, '<$1 class="cell" ');
  const live = () => {
    const m = $("#addform").elements.namedItem("min_qty");
    if (m) $("#nc-low").innerHTML = Number(m.value) > 0 ? '<span class="low">kończy się</span>' : "";
  };
  const load = async () => {
    rows = await GET("/components");
    const low = rows.filter((c) => c.min_qty > c.total_qty).length;
    setStats([{ label: "Komponentów", value: rows.length }, { label: "Sztuk", value: num(rows.reduce((s, c) => s + c.total_qty, 0)) },
      { label: "Wartość", value: money(rows.reduce((s, c) => s + c.total_value, 0)) }, ...(low ? [{ label: "Kończy się", value: low, cls: "warn" }] : [])]);
    tableFill($("#list"), cols, rows, "Brak komponentów");
    live();
  };
  $("#list").innerHTML = tableShell(cols, { add: admin ? addRow : "" });
  if (admin) {
    $("#list").addEventListener("input", live);
    onSubmit($("#addform"), async (d) => { await POST("/components", d); notify(`Dodano komponent „${d.name}”`); $("#addform").reset(); await load(); });
    autosave($("#list"), async (el) => {
      const c = rows.find((r) => r.id === Number(el.dataset.id));
      const field = el.dataset.save;
      let v = el.value;
      if (field === "name" && !v.trim()) throw new Error("Nazwa nie może być pusta");
      if (field === "min_qty") v = Number(v) || 0;
      else if (field !== "name" && v === "") v = null;
      await PATCH(`/components/${c.id}`, { [field]: v });
      c[field] = v;
      if (field === "min_qty") el.closest("tr").querySelector(".lowcell").innerHTML = lowBadge(c);
      notify(`Zapisano „${c.name}”`);
    });
  }
  await load();
};

// szczegóły komponentu: popup nad bieżącą stroną (edycja w miejscu, gdzie leży, historia)
async function openComponent(id, { fromRoute = false } = {}) {
  const [c, moves] = await Promise.all([GET(`/components/${id}`), GET("/movements" + qs({ component_id: id, limit: 50 }))]);
  const admin = isAdmin();
  const prevStats = stats;
  let dirty = false;
  const showStats = () => setStats([
    { label: c.name, value: "", cls: "title" },
    { label: "Sztuk", value: num(c.total_qty) }, { label: "Wartość", value: money(c.total_value) },
    { label: "Lokalizacji", value: new Set(c.stock.map((r) => r.location)).size }, { label: "Próg", value: c.min_qty || "—" },
    ...(c.min_qty > c.total_qty ? [{ label: "", value: "KOŃCZY SIĘ", cls: "warn" }] : [{ label: "", value: "stan w normie", cls: "ok" }]),
  ]);
  const field = (label, name, value, type = "text", extra = "") =>
    `<label>${label}${admin ? cellInput(name, c.id, value, type, extra) : `<span class="ro">${esc(value) || "—"}</span>`}</label>`;
  const m = modal(c.name, `<section class="plate" id="cedit">
      ${field("Nazwa", "name", c.name, "text", "required")}
      ${field("Nr katalogowy", "part_number", c.part_number)}
      ${field("Opis", "description", c.description)}
      ${field("Próg alarmowy", "min_qty", c.min_qty || "", "number", 'min="0" placeholder="0"')}</section>
    ${table([
      ["Właściciel", (r) => esc(r.owner)], ["Posiadacz", (r) => esc(r.holder)], ["Lokalizacja", (r) => esc(r.location)],
      ["Ilość", (r) => r.qty, "num"], ["Cena/szt.", (r) => money(r.unit_cost), "num"], ["Wartość", (r) => money(r.value), "num"],
    ], c.stock, "Brak na stanie", "Gdzie jest")}
    ${movementsTable(moves, false, "Ostatnie operacje")}`, {
    wide: true,
    onClose: () => {
      setStats(prevStats);
      if (fromRoute && location.hash === `#/components/${id}`) history.replaceState(null, "", "#/components");
      if (dirty) router();   // odśwież stronę pod spodem, jeśli coś zmieniono
    },
  });
  showStats();
  m.el.querySelectorAll(".tablewrap").forEach(applyView);
  if (admin) autosave($("#cedit", m.el), async (el) => {
    const f = el.dataset.save;
    let v = el.value;
    if (f === "name" && !v.trim()) throw new Error("Nazwa nie może być pusta");
    if (f === "min_qty") v = Number(v) || 0;
    else if (f !== "name" && v === "") v = null;
    await PATCH(`/components/${id}`, { [f]: v });
    c[f] = v;
    dirty = true;
    if (f === "name") $(".modal-head h3", m.el).textContent = v;
    showStats();   // "KOŃCZY SIĘ" i próg w pasku aktualizują się od razu
    notify(`Zapisano „${c.name}”`);
  });
}

// bezpośredni adres #/components/ID: lista komponentów + popup
routes["/components/:id"] = async ({ id }) => {
  await routes["/components"]();
  await openComponent(id, { fromRoute: true });
};

// sugestie z "kończy się" -> koszyk (ilość = brakująca minus to, co już zamówione)
async function addSuggestions(ids) {
  const r = await POST("/orders/suggestions/add", ids ? { ids } : {});
  notify(r.added ? `Dodano do koszyka: ${r.added} ${r.added === 1 ? "pozycję" : "pozycji"}` : "Nic do dodania: wszystko już w koszyku lub zamówione");
  return r.added;
}

routes["/low-stock"] = async () => {
  const rows = await GET("/low-stock");
  setStats(rows.length ? [{ label: "Kończy się", value: rows.length, cls: "warn" }, { label: "Brakuje łącznie", value: num(rows.reduce((s, c) => s + c.shortage, 0)) }] : [{ label: "", value: "Nic się nie kończy", cls: "ok" }]);
  const cols = [
    ["Komponent", (c) => `<a href="#/components/${c.id}">${esc(c.name)}</a>`],
    ["Nr katalogowy", (c) => esc(c.part_number)],
    ["Jest", (c) => c.total_qty, "num"], ["Próg", (c) => c.min_qty, "num"], ["Brakuje", (c) => c.shortage, "num"],
    ["", (c) => `<button data-tocart="${c.id}">Do koszyka</button>`],
  ];
  const bulk = `<tr class="newrow bulk"><td class="lp"></td><td colspan="${cols.length}">
    <button data-addall>Dodaj wszystkie do koszyka</button> <a class="btn" href="#/orders">Przejdź do zamówień</a></td></tr>`;
  view.innerHTML = `<h2>Kończące się</h2><div id="list"></div>`;
  $("#list").innerHTML = tableShell(cols, { add: rows.length ? bulk : "", title: "Sugestie" });
  tableFill($("#list"), cols, rows, "Nic się nie kończy");
  $("#list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-tocart], [data-addall]");
    if (!b) return;
    try { await addSuggestions(b.dataset.tocart ? [Number(b.dataset.tocart)] : null); } catch (err) { fail(err); }
  });
};

// ---------- zamówienia: sugestie -> koszyk -> zamówione -> przyjęte na stan ----------
function receiveForm(o, people, reload) {
  const m = modal("Przyjmij na stan", `<dl class="modal-info"><dt>Komponent</dt><dd>${esc(o.component)}</dd>
      <dt>Zamówiono</dt><dd>${o.qty} szt.${o.unit_cost != null ? ` po ${money(o.unit_cost)}` : ""}</dd>${o.supplier ? `<dt>Dostawca</dt><dd>${esc(o.supplier)}</dd>` : ""}</dl>
    <form class="modal-form">
      <label>Właściciel <select name="owner_id" required>${options(people, "id", (p) => p.name, { blank: "wybierz…" })}</select></label>
      <label>Posiadacz <select name="holder_id">${options(people, "id", (p) => p.name, { blank: "= właściciel" })}</select></label>
      <label>Lokalizacja <input name="location" list="locs" autocomplete="off"></label>
      <label>Dostarczona ilość <input name="qty" type="number" min="1" value="${o.qty}" required></label>
      <label>Cena za sztukę <input name="unit_cost" type="number" min="0" step="any" value="${o.unit_cost ?? ""}"></label>
      <div class="modal-actions"><button type="button" data-cancel>Anuluj</button><button>Przyjmij</button></div></form>`);
  const form = $("form", m.el);
  form.elements.owner_id.value = String(me.person_id);
  $("[data-cancel]", m.el).onclick = m.close;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    m.error("");
    const d = formObj(form);
    try { await POST(`/orders/${o.id}/receive`, d); }
    catch (err) { m.error(`Błąd${err.status ? ` ${err.status}` : ""}: ${err.message}`); return; }
    m.close();
    notify(`Przyjęto na stan: ${d.qty} szt. „${o.component}”${d.location ? ` (${d.location})` : ""}`);
    await reload();
  });
}

routes["/orders"] = async () => {
  const [components, people, locations] = await Promise.all([GET("/components"), GET("/people"), GET("/locations")]);
  view.innerHTML = `<h2>Zamówienia</h2><form id="addform"></form>
    <datalist id="locs">${locations.filter((l) => l.location).map((l) => `<option value="${esc(l.location)}">`).join("")}</datalist>
    <div id="sugg"></div><div id="cart"></div><div id="ordered"></div><div id="done"></div>`;
  let all = [], suggRows = [], cartRows = [], orderedRows = [], doneRows = [];
  const comp = (o) => `<a href="#/components/${o.component_id}">${esc(o.component)}</a>${o.part_number ? ` <small>${esc(o.part_number)}</small>` : ""}`;
  const when = (ts) => (ts ? esc(shortTime(ts)) : "");

  // sugestie z "kończy się"
  const suggCols = [
    ["Komponent", (s) => comp({ ...s, component: s.component })],
    ["W magazynie", (s) => s.in_stock, "num"], ["Próg", (s) => s.min_qty, "num"], ["Brakuje", (s) => s.shortage, "num"],
    ["Już zamówione", (s) => s.on_order || "", "num"],
    ["Zamów", (s) => `<input class="cell sq" type="number" min="1" value="${s.suggested_qty}" data-cost="${s.unit_cost ?? ""}">`, "num"],
    ["Cena/szt.", (s) => money(s.unit_cost), "num"],
    ["Wartość", (s) => `<span class="val">${money(s.value)}</span>`, "num"],
    ["", (s) => `<button data-tocart>Do koszyka</button>`],
  ];
  // koszyk (edytowalny w miejscu)
  const cartCols = [
    ["Komponent", comp],
    ["Ilość", (o) => cellInput("qty", o.id, o.qty, "number", 'min="1"'), "num"],
    ["Cena/szt.", (o) => cellInput("unit_cost", o.id, o.unit_cost, "number", 'min="0" step="any" placeholder="—"'), "num"],
    ["Wartość", (o) => `<span class="val">${money(o.value)}</span>`, "num"],
    ["Dostawca", (o) => cellInput("supplier", o.id, o.supplier, "text", 'placeholder="—"')],
    ["Notatka", (o) => cellInput("note", o.id, o.note, "text", 'placeholder="—"')],
    ["Dodano", (o) => esc(`${shortTime(o.created_at)}${o.by_user ? ` · ${o.by_user}` : ""}`)],
    ["", () => `<span class="actions"><button data-ordered>Zamówione</button><button data-del>Usuń</button></span>`],
  ];
  const cartAdd = `<tr class="newrow">
      <td><select class="cell" name="component_id" form="addform" required>${options(components, "id", (c) => `${c.name}${c.part_number ? ` (${c.part_number})` : ""}`, { blank: "+ komponent do koszyka" })}</select></td>
      <td class="num"><input class="cell" name="qty" type="number" min="1" form="addform" required placeholder="szt."></td>
      <td class="num"><input class="cell" name="unit_cost" type="number" min="0" step="any" form="addform" placeholder="cena"></td>
      <td class="num"><span id="nc-val">—</span></td>
      <td><input class="cell" name="supplier" form="addform" placeholder="dostawca" autocomplete="off"></td>
      <td><input class="cell" name="note" form="addform" placeholder="notatka" autocomplete="off"></td>
      <td></td><td><button form="addform">Dodaj</button></td></tr>
    <tr class="newrow bulk"><td class="lp"></td><td colspan="8"><button data-markall>Oznacz cały koszyk jako zamówiony</button>
      <button data-copy>Kopiuj listę</button></td></tr>`;
  const suggAdd = `<tr class="newrow bulk"><td class="lp"></td><td colspan="9"><button data-addall>Dodaj wszystkie sugestie do koszyka</button></td></tr>`;
  const orderedCols = [
    ["Komponent", comp], ["Ilość", (o) => o.qty, "num"], ["Cena/szt.", (o) => money(o.unit_cost), "num"], ["Wartość", (o) => money(o.value), "num"],
    ["Dostawca", (o) => esc(o.supplier)], ["Notatka", (o) => esc(o.note)], ["Zamówiono", (o) => when(o.ordered_at)],
    ["", () => `<span class="actions"><button data-receive>Przyjmij na stan</button><button data-back>Do koszyka</button></span>`],
  ];
  const doneCols = [
    ["Komponent", comp], ["Ilość", (o) => o.qty, "num"], ["Cena/szt.", (o) => money(o.unit_cost), "num"], ["Wartość", (o) => money(o.value), "num"],
    ["Dostawca", (o) => esc(o.supplier)], ["Notatka", (o) => esc(o.note)], ["Przyjęto", (o) => when(o.received_at)],
  ];
  $("#sugg").innerHTML = tableShell(suggCols, { add: suggAdd, title: "Sugestie: kończy się" });
  $("#cart").innerHTML = tableShell(cartCols, { add: cartAdd, title: "Koszyk" });
  $("#ordered").innerHTML = tableShell(orderedCols, { title: "Zamówione, czekają na dostawę" });
  $("#done").innerHTML = tableShell(doneCols, { title: "Przyjęte (ostatnie 50)" });

  const sum = (rows, f) => rows.reduce((s, r) => s + (f(r) || 0), 0);
  const updateStats = () => {
    let n = 0, qty = 0, val = 0;
    for (const tr of $("#cart tbody").rows) {
      const q = tr.querySelector('[data-save="qty"]'), c = tr.querySelector('[data-save="unit_cost"]'), v = tr.querySelector(".val");
      if (!q || !v) continue;
      const rowVal = c.value === "" ? null : (Number(q.value) || 0) * Number(c.value);
      v.textContent = money(rowVal);
      if (tr.hidden) continue;
      n++; qty += Number(q.value) || 0; val += rowVal || 0;
    }
    setStats([{ label: "Koszyk", value: `${n} poz.` }, { label: "Sztuk", value: num(qty) }, { label: "Wartość koszyka", value: money(val) },
      { label: "Zamówione", value: money(sum(orderedRows, (o) => o.value)), title: "Wartość zamówień czekających na dostawę" },
      { label: "Sugestii", value: suggRows.length, cls: suggRows.length ? "warn" : "ok" }]);
  };
  $("#cart .tablewrap")._stats = updateStats;

  const load = async () => {
    [all, suggRows] = await Promise.all([GET("/orders?limit=500"), GET("/orders/suggestions")]);
    cartRows = all.filter((o) => o.status === "cart").reverse();
    orderedRows = all.filter((o) => o.status === "ordered").reverse();
    doneRows = all.filter((o) => o.status === "received").slice(0, 50);
    tableFill($("#sugg"), suggCols, suggRows, "Brak sugestii: nic nie jest poniżej progu albo już jest zamówione");
    tableFill($("#cart"), cartCols, cartRows, "Koszyk jest pusty");
    tableFill($("#ordered"), orderedCols, orderedRows, "Brak zamówień w drodze");
    tableFill($("#done"), doneCols, doneRows, "Brak przyjętych zamówień");
    updateStats();
  };
  const run = (fn) => async (...a) => { try { await fn(...a); } catch (err) { fail(err); } };
  const rowOf = (e, rows) => rows[e.target.closest("tr").dataset.i];

  // wartość na żywo w sugestiach, koszyku i wierszu dodawania
  $("#sugg").addEventListener("input", (e) => {
    if (!e.target.classList.contains("sq")) return;
    const cost = e.target.dataset.cost === "" ? null : Number(e.target.dataset.cost);
    $(".val", e.target.closest("tr")).textContent = money(cost == null ? null : (Number(e.target.value) || 0) * cost);
  });
  $("#cart").addEventListener("input", (e) => {
    if (["qty", "unit_cost"].includes(e.target.dataset?.save)) updateStats();
    if (e.target.form?.id === "addform" || e.target.getAttribute?.("form") === "addform") {
      const f = $("#addform"), q = Number(f.elements.namedItem("qty").value) || 0, c = f.elements.namedItem("unit_cost").value;
      $("#nc-val").textContent = q && c !== "" ? money(q * Number(c)) : "—";
    }
  });

  $("#sugg").addEventListener("click", run(async (e) => {
    if (e.target.closest("[data-addall]")) { await addSuggestions(null); return load(); }
    const b = e.target.closest("[data-tocart]");
    if (!b) return;
    const s = rowOf(e, suggRows), qty = Number($(".sq", b.closest("tr")).value) || s.suggested_qty;
    await POST("/orders", { component_id: s.component_id, qty, ...(s.unit_cost != null ? { unit_cost: s.unit_cost } : {}), note: "sugestia: kończy się" });
    notify(`Do koszyka: ${qty} szt. „${s.component}”`);
    await load();
  }));
  $("#cart").addEventListener("click", run(async (e) => {
    if (e.target.closest("[data-markall]")) {
      const r = await POST("/orders/mark-ordered", {});
      notify(r.updated ? `Oznaczono jako zamówione: ${r.updated}` : "Koszyk jest pusty");
      return load();
    }
    if (e.target.closest("[data-copy]")) {
      const lines = cartRows.map((o) => `${o.qty} × ${o.component}${o.part_number ? ` (${o.part_number})` : ""}${o.unit_cost != null ? ` @ ${money(o.unit_cost)}` : ""}${o.supplier ? ` — ${o.supplier}` : ""}`);
      await navigator.clipboard.writeText(`${lines.join("\n")}\nRazem: ${money(sum(cartRows, (o) => o.value))}`);
      notify(`Skopiowano listę zakupów (${cartRows.length} poz.)`);
      return;
    }
    if (e.target.closest("[data-del]")) {
      const o = rowOf(e, cartRows);
      await DEL(`/orders/${o.id}`);
      notify(`Usunięto z koszyka: „${o.component}”`);
      return load();
    }
    if (e.target.closest("[data-ordered]")) {
      const o = rowOf(e, cartRows);
      await PATCH(`/orders/${o.id}`, { status: "ordered" });
      notify(`Zamówione: ${o.qty} szt. „${o.component}”`);
      return load();
    }
  }));
  $("#ordered").addEventListener("click", run(async (e) => {
    if (e.target.closest("[data-receive]")) return receiveForm(rowOf(e, orderedRows), people, load);
    if (e.target.closest("[data-back]")) {
      const o = rowOf(e, orderedRows);
      await PATCH(`/orders/${o.id}`, { status: "cart" });
      notify(`Z powrotem w koszyku: „${o.component}”`);
      return load();
    }
  }));
  autosave($("#cart"), async (el) => {
    const o = cartRows.find((x) => x.id === Number(el.dataset.id));
    const f = el.dataset.save, v = el.value;
    let payload;
    if (f === "qty") {
      if (!v || Number(v) < 1) throw new Error("Ilość musi być większa od 0");
      payload = { qty: Number(v) };
    } else if (f === "unit_cost") payload = { unit_cost: v === "" ? null : Number(v) };
    else payload = { [f]: v.trim() || null };
    await PATCH(`/orders/${o.id}`, payload);
    Object.assign(o, payload);
    o.value = o.unit_cost != null ? o.qty * o.unit_cost : null;
    notify(`Zapisano w koszyku: „${o.component}”`);
  });
  onSubmit($("#addform"), async (d) => {
    await POST("/orders", d);
    const c = components.find((x) => x.id === d.component_id);
    notify(`Do koszyka: ${d.qty} szt. „${c.name}”`);
    $("#addform").reset();
    $("#nc-val").textContent = "—";
    await load();
  });
  await load();
};

routes["/locations"] = async () => {
  const rows = await GET("/locations");
  setStats([{ label: "Lokalizacji", value: rows.length }, { label: "Sztuk", value: num(rows.reduce((s, l) => s + (l.total_qty || 0), 0)) },
    { label: "Wartość", value: money(rows.reduce((s, l) => s + (l.total_value || 0), 0)) }]);
  view.innerHTML = `<h2>Lokalizacje</h2>` + table([
    ["Lokalizacja", (l) => (l.location ? `<a href="#/stock?location=${encodeURIComponent(l.location)}">${esc(l.location)}</a>` : "<em>(bez lokalizacji)</em>")],
    ["Pozycji", (l) => l.items, "num"], ["Sztuk", (l) => l.total_qty, "num"], ["Wartość", (l) => money(l.total_value), "num"],
  ], rows);
};

const KIND = { receive: "przyjęcie", transfer: "przekazanie", change_owner: "zmiana właściciela", consume: "zużycie", adjust: "korekta" };
function movementsTable(rows, withComponent = true, title = "") {
  return table([
    ["Czas", (m) => dt(m.ts)],
    ["Operacja", (m) => KIND[m.kind] || esc(m.kind)],
    ...(withComponent ? [["Komponent", (m) => esc(m.component)]] : []),
    ["Ilość", (m) => m.qty, "num"],
    ["Właściciel", (m) => esc(m.owner) + (m.new_owner ? ` → ${esc(m.new_owner)}` : "")],
    ["Od", (m) => esc([m.from_holder, m.from_location].filter(Boolean).join(" / "))],
    ["Do", (m) => esc([m.to_holder, m.to_location].filter(Boolean).join(" / "))],
    ["Cena/szt.", (m) => money(m.unit_cost), "num"],
    ["Kto", (m) => esc(m.by_user)],
    ["Notatka", (m) => esc(m.note)],
  ], rows, "Brak operacji", title);
}

routes["/movements"] = async () => {
  const rows = await GET("/movements?limit=300");
  const today = rows.filter((m) => new Date(m.ts).toDateString() === new Date().toDateString()).length;
  setStats([{ label: "Operacji", value: rows.length }, { label: "Dzisiaj", value: today },
    { label: "Wartość przyjęć", value: money(rows.filter((m) => m.kind === "receive").reduce((s, m) => s + (m.value || 0), 0)), title: "Suma wartości operacji typu przyjęcie" }]);
  view.innerHTML = `<h2>Historia</h2>` + movementsTable(rows);
};

// --- osoby ---
routes["/people"] = async () => {
  const admin = isAdmin();
  view.innerHTML = `<h2>Osoby</h2>
    <form id="addform"></form>
    <div id="list"></div>`;
  let rows = [];
  const addRow = `<tr class="newrow"><td><input class="cell" name="name" form="addform" required placeholder="+ nowa osoba (bez konta)" autocomplete="off"> <button form="addform">Dodaj</button></td></tr>`
    .replace(/<(input|select) /g, '<$1 class="cell" ');
  const load = async () => {
    rows = await GET("/people");
    setStats([{ label: "Osób", value: rows.length }]);
    tableFill($("#list"), cols, rows, "Brak osób");
  };
  const cols = [["Nazwa", (p) => (admin ? cellInput("name", p.id, p.name, "text", "required") : esc(p.name))]];
  $("#list").innerHTML = tableShell(cols, { add: admin ? addRow : "" });
  if (admin) {
    onSubmit($("#addform"), async (d) => { await POST("/people", d); notify(`Dodano osobę „${d.name}”`); $("#addform").reset(); await load(); });
    autosave($("#list"), async (el) => {
      const name = el.value.trim();
      if (!name) throw new Error("Nazwa nie może być pusta");
      await PATCH(`/people/${el.dataset.id}`, { name });
      notify(`Zmieniono nazwę osoby na „${name}”`);
    });
  }
  await load();
};

// --- użytkownicy (admin) ---
routes["/users"] = async () => {
  if (!isAdmin()) return (view.innerHTML = "<p>Brak uprawnień</p>");
  view.innerHTML = `<h2>Użytkownicy</h2>
    <form id="addform"></form>
    <div id="action"></div><div id="list"></div>`;
  let rows = [];
  const load = async () => {
    rows = await GET("/users");
    setStats([{ label: "Użytkowników", value: rows.length }, { label: "Aktywnych", value: rows.filter((u) => u.active).length },
      { label: "Adminów", value: rows.filter((u) => u.role === "admin" && u.active).length }]);
    tableFill($("#list"), cols, rows, "Brak użytkowników");
    live();
  };
  const cols = [
      ["Login", (u) => esc(u.username)], ["Osoba", (u) => esc(u.person)],
      ["Rola", (u) => `<select class="cell" data-save="role" data-instant data-id="${u.id}">
        <option value="user"${u.role === "user" ? " selected" : ""}>user</option><option value="admin"${u.role === "admin" ? " selected" : ""}>admin</option></select>`],
      ["Aktywny", (u) => `<input type="checkbox" data-save="active" data-instant data-id="${u.id}"${u.active ? " checked" : ""}>`],
      ["", (u) => `<span class="actions"><button data-act="password">Ustaw hasło</button></span>`],
  ];
  const addRow = `<tr class="newrow">
    <td><input class="cell" name="username" form="addform" required placeholder="+ login" autocomplete="off"></td>
    <td><input class="cell" name="person_name" form="addform" placeholder="osoba (= login)" autocomplete="off"></td>
    <td><select class="cell" name="role" form="addform"><option value="user">user</option><option value="admin">admin</option></select></td>
    <td>tak</td>
    <td><input class="cell" name="password" type="password" minlength="8" required form="addform" placeholder="hasło (min. 8)" autocomplete="new-password">
      <small id="nu-hint"></small> <button form="addform">Utwórz</button></td></tr>`;
  // na żywo: podpowiedź osoby = login, licznik znaków hasła
  const live = () => {
    const f = $("#addform");
    const el = (n) => f.elements.namedItem(n);
    if (!el("username")) return;
    el("person_name").placeholder = el("username").value ? `osoba (= ${el("username").value})` : "osoba (= login)";
    const n = el("password").value.length;
    $("#nu-hint").textContent = n ? ` ${n}/8` : "";
    $("#nu-hint").className = n && n < 8 ? "weak" : "";
  };
  $("#list").innerHTML = tableShell(cols, { add: addRow });
  $("#list").addEventListener("input", live);
  onSubmit($("#addform"), async (d) => { await POST("/users", d); notify(`Utworzono użytkownika „${d.username}” (${d.role})`); $("#addform").reset(); await load(); });
  $("#list").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const u = rows[b.closest("tr").dataset.i];
    notify("");
    try {
      {
        const m = modal(`Nowe hasło: ${u.username}`, `<form class="modal-form">
          <label>Hasło (min. 8 znaków) <input name="password" type="password" minlength="8" required autofocus autocomplete="new-password"></label>
          <div class="modal-actions"><button type="button" data-cancel>Anuluj</button><button>Ustaw</button></div></form>`);
        $("[data-cancel]", m.el).onclick = m.close;
        $("form", m.el).addEventListener("submit", async (ev) => {
          ev.preventDefault();
          try { await PATCH(`/users/${u.id}`, formObj(ev.target)); }
          catch (err) { m.error(`Błąd${err.status ? ` ${err.status}` : ""}: ${err.message}`); return; }
          m.close();
          notify(`Zmieniono hasło użytkownika ${u.username}`);
        });
      }
    } catch (err) { fail(err); }
  });
  // rola i aktywność zapisują się od razu po zmianie; przy błędzie (np. ostatni admin) lista wraca do stanu z serwera
  autosave($("#list"), async (el) => {
    const u = rows.find((r) => r.id === Number(el.dataset.id));
    const field = el.dataset.save;
    const v = fieldValue(el);
    await PATCH(`/users/${u.id}`, { [field]: v });
    u[field] = v;
    notify(field === "role" ? `${u.username}: rola ${v}` : `${u.username}: konto ${v ? "aktywowane" : "dezaktywowane"}`);
  }, { onError: () => load() });
  await load();
};

// --- konto ---
function openAccount({ fromRoute = false } = {}) {
  const m = modal("Moje konto", `<dl class="modal-info"><dt>Login</dt><dd>${esc(me.username)}</dd>
      <dt>Rola</dt><dd>${me.role === "admin" ? "administrator" : "użytkownik (tylko stany)"}</dd></dl>
    <form class="modal-form"><h3 class="section">Zmiana hasła</h3>
      <label>Stare hasło <input name="old_password" type="password" required autofocus autocomplete="current-password"></label>
      <label>Nowe hasło (min. 8 znaków) <input name="new_password" type="password" minlength="8" required autocomplete="new-password"></label>
      <div class="modal-actions"><button type="button" data-cancel>Zamknij</button><button>Zmień hasło</button></div></form>`, {
    onClose: () => { if (fromRoute && location.hash === "#/account") history.replaceState(null, "", "#/stock"); },
  });
  $("[data-cancel]", m.el).onclick = m.close;
  $("form", m.el).addEventListener("submit", async (e) => {
    e.preventDefault();
    m.error("");
    try {
      const r = await POST("/auth/password", formObj(e.target));
      saveToken(r.token);
    } catch (err) { m.error(`Błąd${err.status ? ` ${err.status}` : ""}: ${err.message}`); return; }
    m.close();
    notify("Twoje hasło zostało zmienione");
  });
}
routes["/account"] = async () => {
  await routes["/stock"]();
  openAccount({ fromRoute: true });
};

// linki do komponentów i konta otwierają popup zamiast przechodzić na inną stronę
document.addEventListener("click", (e) => {
  const a = e.target.closest('a[href^="#/components/"], a[href="#/account"]');
  if (!a || !me) return;
  e.preventDefault();
  const href = a.getAttribute("href");
  if (href === "#/account") openAccount();
  else openComponent(href.split("/")[2]).catch(fail);
});

// ---------- nawigacja i routing ----------
const NAV = [
  ["/stock", "Stany"], ["/components", "Komponenty"], ["/low-stock", "Kończące się"], ["/orders", "Zamówienia"],
  ["/locations", "Lokalizacje"], ["/movements", "Historia"], ["/people", "Osoby"], ["/users", "Użytkownicy", true],
];

function renderChrome() {
  document.body.classList.toggle("auth", !me);
  $("#header").hidden = !me;
  $("#status-user").textContent = me ? `👤 ${me.username} (${me.role})` : "Niezalogowany";
  tick();
  if (me) refreshGlobal();
  if (!me) return;
  $("#nav").innerHTML = NAV.filter((n) => !n[2] || isAdmin())
    .map(([p, label]) => `<a href="#${p}" data-path="${p}">${label}</a>`).join("");
  $("#userbox").innerHTML = `<a href="#/account">${esc(me.username)}</a> <a id="logout">Wyloguj</a>`;
  $("#logout").onclick = async () => {
    try { await POST("/auth/logout"); } catch (e) {}
    saveToken(null); me = null; flash = { text: "Wylogowano", kind: "ok" }; renderChrome(); location.hash = "#/login";
  };
}

function match(path) {
  for (const pattern of Object.keys(routes)) {
    const names = [];
    const re = new RegExp("^" + pattern.replace(/:(\w+)/g, (_, n) => (names.push(n), "([^/]+)")) + "$");
    const m = path.match(re);
    if (m) return [pattern, Object.fromEntries(names.map((n, i) => [n, decodeURIComponent(m[i + 1])]))];
  }
  return [null, {}];
}

let navSeq = 0;
let flash = null; // komunikat do pokazania na ekranie logowania po przekierowaniu
async function router() {
  const seq = ++navSeq;
  const [rawPath, rawQuery = ""] = (location.hash.slice(1) || "/stock").split("?");
  notify("");
  setStats([]);
  try {
    if (!me && token) { try { me = await GET("/auth/me"); } catch (e) { me = null; } }
    renderChrome();
    if (!me) {
      const { initialized } = await GET("/auth/status");
      const target = initialized ? "/login" : "/setup";
      if (rawPath !== target) { location.hash = "#" + target; return; }
    } else if (rawPath === "/login" || rawPath === "/setup") { location.hash = "#/stock"; return; }
    const [pattern, params] = match(rawPath);
    if (!pattern) { view.innerHTML = "<p>Nie ma takiej strony</p>"; return; }
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", rawPath.startsWith(a.dataset.path)));
    const authScreen = pattern === "/login" || pattern === "/setup";
    $("#auth").innerHTML = authScreen ? $("#auth").innerHTML : "";
    if (!authScreen) view.innerHTML = "<p class='loading'>Ładowanie…</p>";
    await routes[pattern](params);
    if (seq !== navSeq) return;
    if (authScreen && flash) { authMessage(flash.text, flash.kind); flash = null; }
    view.querySelectorAll(".tablewrap").forEach(applyView);
    // restart animacji wejścia treści przy zmianie widoku
    view.classList.remove("swap");
    void view.offsetWidth;
    view.classList.add("swap");
    // ?location=… z widoku lokalizacji wypełnia filtr stanów
    if (pattern === "/stock" && rawQuery) {
      const loc = new URLSearchParams(rawQuery).get("location");
      const wrap = $(".tablewrap");
      const col = wrap && [...wrap.querySelectorAll("th")].findIndex((th) => th.dataset.title === "Lokalizacja");
      if (loc && col > 0) { gridState(wrap).filters[col] = loc; applyView(wrap); }
    }
  } catch (err) {
    if (seq === navSeq) { view.innerHTML = ""; fail(err); }
  }
}

window.addEventListener("hashchange", router);
// pierwsze wyrenderowanie bez animacji (klasa "boot" wyłącza transitions)
router().finally(() => requestAnimationFrame(() => requestAnimationFrame(() => document.body.classList.remove("boot"))));
