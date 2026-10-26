"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const gbp = (n) => (n == null ? "–" : "£" + Number(n).toFixed(2));
const COND = { NM: "Near Mint", LP: "Lightly Played", MP: "Moderately Played", HP: "Heavily Played", DMG: "Damaged", "?": "Condition not stated" };
const VARIANT = { firstEdition: "1st Edition", holo: "Holo", normal: "Normal", reverse: "Reverse Holo" };
const state = { items: [], poke: localGet("poke", "All") };

function localGet(key, fallback) {
  try { return localStorage.getItem("ph." + key) ?? fallback; } catch { return fallback; }
}
function localSet(key, value) {
  try { localStorage.setItem("ph." + key, value); } catch { /* private mode */ }
}
async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}
function ago(iso) {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 1440) return `${Math.round(mins / 60)} h ago`;
  return `${Math.round(mins / 1440)} days ago`;
}
function until(iso) {
  if (!iso) return "";
  const mins = Math.max(0, Math.round((new Date(iso) - Date.now()) / 60000));
  return mins <= 0 ? "any moment" : `in ${mins} min`;
}
const img = (url, size = "low") => (url ? `${url}/${size}.webp` : "");

// ---------------------------------------------------------------- tabs
document.querySelectorAll("nav button").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));
function showTab(tab) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll("main > section").forEach((s) => (s.hidden = s.id !== "tab-" + tab));
  localSet("tab", tab);
  if (tab === "checklist" && !state.items.length) loadCards();
  if (tab === "settings") loadPrefs();
}

// ---------------------------------------------------------------- status
async function loadStatus() {
  const s = await api("/api/status");
  const last = s.last_run;
  let text = s.busy ? `⏳ ${s.busy}…` : last ? `Last scan ${ago(last.finished_at || last.started_at)}` : "No scans yet";
  if (!s.busy && s.next_scan_at) text += ` · next ${until(s.next_scan_at)}`;
  text += ` · eBay calls today ${s.calls_today}/${s.call_budget}`;
  $("#status").textContent = text;

  const warn = [];
  if (!s.ebay_ready) warn.push("eBay keys are not set yet, so no searching is happening. Add EBAY_CLIENT_ID and EBAY_CLIENT_SECRET on the server.");
  if (!s.telegram_ready) warn.push("Telegram isn't set up, so deals only show here (no phone alerts).");
  if (!s.catalog_updated_at) warn.push("Downloading the Charizard & Mew card list. This takes a minute or two the first time.");
  if (s.error) warn.push("Last problem: " + s.error);
  $("#setup-warning").hidden = !warn.length;
  $("#setup-warning").innerHTML = warn.map(esc).join("<br>");

  $("#progress").innerHTML = (s.progress || []).map((p) => {
    const pct = p.total ? Math.round((100 * (p.owned || 0)) / p.total) : 0;
    return `<div class="progress"><b>${esc(p.pokemon)}</b> master set: ${p.owned || 0} / ${p.total} (${pct}%)
      <div class="track"><div class="fill" style="width:${pct}%"></div></div></div>`;
  }).join("");
  $("#system").textContent = [
    `eBay keys: ${s.ebay_ready ? "set ✅" : "missing ❌"}`,
    `Telegram: ${s.telegram_ready ? "set ✅" : "missing ❌"}`,
    `Scan every: ${s.interval_minutes} min`,
    `Card list updated: ${ago(s.catalog_updated_at)}`,
    `Exchange rates: €1 = £${s.fx?.EUR ?? "?"}, $1 = £${s.fx?.USD ?? "?"}`,
    last ? `Last scan: ${last.listings_seen} listings checked, ${last.deals} deals, ${last.alerts} alerts sent` : "",
    s.error ? `Last problem: ${s.error}` : "",
  ].filter(Boolean).join("\n");
  return s;
}

// ---------------------------------------------------------------- deals
async function loadDeals() {
  const { deals, watch } = await api("/api/deals");
  $("#deal-count").textContent = deals.length || "";
  $("#deals").innerHTML = deals.length ? deals.map(dealCard).join("") :
    `<div class="empty">No deals right now. The bot checks eBay every 15 minutes and will ping you on Telegram when it finds one.</div>`;
  const rows = watch.filter(Boolean);
  $("#watch-wrap").hidden = !rows.length;
  $("#watch").innerHTML = rows.map((w) => `
    <div class="row">
      <img loading="lazy" src="${esc(img(w.card_image))}" alt="" onerror="this.style.visibility='hidden'">
      <div style="flex:1">
        <b>${esc(w.card_name)}</b> <span class="meta">${esc(w.set_name)} #${esc(w.local_id)} · ${VARIANT[w.variant] || w.variant}</span><br>
        ${w.url ? `Cheapest: <a href="${esc(w.url)}" target="_blank" rel="noopener">${gbp(w.total)}</a> (${esc(w.condition)})` : "No listings seen yet"}
        · market ${gbp(w.market_gbp)}${w.target_price ? ` · alert at ${gbp(w.target_price)}` : ""}
      </div>
    </div>`).join("");
}

function dealCard(d) {
  const off = d.discount_pct != null ? `<span class="badge off">${Math.round(d.discount_pct)}% under</span>` : "";
  const target = d.target_price && d.total <= d.target_price ? `<span class="badge">≤ your ${gbp(d.target_price)} target</span>` : "";
  return `
  <article class="deal ${d.priority ? "priority" : ""}" data-listing="${esc(d.listing_id)}" data-item="${esc(d.match_item_id)}">
    <div class="top">
      <img loading="lazy" src="${esc(d.image || img(d.card_image))}" alt="" onerror="this.style.visibility='hidden'">
      <div>
        <div class="name">${d.priority ? "⭐ " : ""}${esc(d.card_name)}</div>
        <div class="meta">${esc(d.set_name)} #${esc(d.local_id)} · ${VARIANT[d.variant] || esc(d.variant)}</div>
        <div><span class="badge">${esc(COND[d.condition] || d.condition)}</span>${off}${target}</div>
        <div class="price">${gbp(d.total)}</div>
        <div class="vs">delivered · fair ${gbp(d.fair_gbp)} · NM market ${gbp(d.market_gbp)}</div>
        <div class="meta">Seller ${esc(d.seller || "?")} (${d.feedback_score ?? "?"}) · seen ${ago(d.last_seen)}</div>
      </div>
    </div>
    <div class="title">${esc(d.title)}</div>
    ${d.warning ? `<div class="warn">⚠️ ${esc(d.warning)}</div>` : ""}
    <div class="bar">
      <a class="btn primary" href="${esc(d.url)}" target="_blank" rel="noopener">Open on eBay</a>
      <button data-act="got">We got it ✓</button>
      <button data-act="dismiss">Not interested</button>
    </div>
  </article>`;
}

$("#deals").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const card = btn.closest(".deal");
  if (btn.dataset.act === "got") await api(`/api/items/${encodeURIComponent(card.dataset.item)}`, { owned: true });
  else await api(`/api/listings/${encodeURIComponent(card.dataset.listing)}/dismiss`, {});
  state.items = [];
  await loadDeals();
});

// ---------------------------------------------------------------- checklist
async function loadCards() {
  const { items } = await api("/api/cards");
  state.items = items;
  renderChecklist();
}

function renderPokeFilter() {
  const names = ["All", ...new Set(state.items.map((i) => i.pokemon))];
  $("#poke-filter").innerHTML = names.map((n) => `<button type="button" class="${n === state.poke ? "on" : ""}" data-p="${esc(n)}">${esc(n)}</button>`).join("");
}
$("#poke-filter").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.poke = b.dataset.p;
  localSet("poke", state.poke);
  renderChecklist();
});
["#search", "#hide-owned", "#only-priority", "#show-hidden"].forEach((sel) => $(sel).addEventListener("input", renderChecklist));

function renderChecklist() {
  renderPokeFilter();
  if (!state.items.length) {
    $("#checklist").innerHTML = `<div class="empty">The card list is still downloading. Check back in a minute.</div>`;
    return;
  }
  const q = $("#search").value.trim().toLowerCase();
  const hideOwned = $("#hide-owned").checked;
  const onlyPriority = $("#only-priority").checked;
  const showHidden = $("#show-hidden").checked;

  const cards = new Map();
  for (const it of state.items) {
    if (state.poke !== "All" && it.pokemon !== state.poke) continue;
    if (q && !`${it.name} ${it.set_name} ${it.local_id}`.toLowerCase().includes(q)) continue;
    if (!cards.has(it.card_id)) cards.set(it.card_id, { ...it, variants: [] });
    cards.get(it.card_id).variants.push(it);
  }
  const sets = new Map();
  for (const card of cards.values()) {
    let variants = card.variants.filter((v) => showHidden || !v.hidden);
    if (hideOwned) variants = variants.filter((v) => !v.owned);
    if (onlyPriority) variants = variants.filter((v) => v.priority);
    if (!variants.length) continue;
    card.shown = variants;
    if (!sets.has(card.set_id)) sets.set(card.set_id, { name: card.set_name, date: card.release_date, cards: [] });
    sets.get(card.set_id).cards.push(card);
  }
  if (!sets.size) {
    $("#checklist").innerHTML = `<div class="empty">${hideOwned ? "Nothing left to find here. Untick “Hide cards we have” to see everything." : "No cards match."}</div>`;
    return;
  }
  $("#checklist").innerHTML = [...sets.values()].map((set) => `
    <div class="set">
      <h3>${esc(set.name)} <span>${set.date ? set.date.slice(0, 4) : ""}</span></h3>
      <div class="cards">${set.cards.map(tile).join("")}</div>
    </div>`).join("");
}

function tile(card) {
  const allOwned = card.variants.every((v) => v.owned || v.hidden);
  return `
  <div class="tile ${allOwned ? "done" : ""}">
    <img loading="lazy" src="${esc(img(card.image))}" alt="" onerror="this.style.visibility='hidden'">
    <div class="name">${esc(card.name)}</div>
    <div class="num">#${esc(card.local_id)}${card.set_official ? "/" + card.set_official : ""}${card.rarity ? " · " + esc(card.rarity) : ""}</div>
    ${card.shown.map(variantRow).join("")}
  </div>`;
}

function variantRow(v) {
  return `
  <div class="variant" data-id="${esc(v.id)}">
    <label><input type="checkbox" data-f="owned" ${v.owned ? "checked" : ""}> ${VARIANT[v.variant] || esc(v.variant)}</label>
    <span class="mk" title="Near Mint market price (${esc(v.price_source || "no price")})">${gbp(v.market_gbp)}</span>
    <button class="star ${v.priority ? "on" : ""}" data-f="priority" title="Priority">★</button>
    <div class="target">alert at £<input type="number" min="0" step="0.5" data-f="target_price" value="${v.target_price ?? ""}" placeholder="auto">
      <button class="skip" data-f="hidden" title="Not collecting this one">${v.hidden ? "unskip" : "skip"}</button></div>
  </div>`;
}

async function saveItem(row, changes) {
  const updated = await api(`/api/items/${encodeURIComponent(row.dataset.id)}`, changes);
  const local = state.items.find((i) => i.id === updated.id);
  if (local) Object.assign(local, updated);
  loadStatus();
}

$("#checklist").addEventListener("change", (e) => {
  const row = e.target.closest(".variant");
  if (!row) return;
  if (e.target.dataset.f === "owned") saveItem(row, { owned: e.target.checked });
  if (e.target.dataset.f === "target_price") saveItem(row, { target_price: e.target.value });
});
$("#checklist").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-f]");
  if (!btn) return;
  const row = btn.closest(".variant");
  const local = state.items.find((i) => i.id === row.dataset.id);
  if (btn.dataset.f === "priority") {
    btn.classList.toggle("on");
    await saveItem(row, { priority: btn.classList.contains("on") });
  } else if (btn.dataset.f === "hidden") {
    await saveItem(row, { hidden: !local.hidden });
    renderChecklist();
  }
});

// ---------------------------------------------------------------- settings
async function loadPrefs() {
  const prefs = await api("/api/prefs");
  const form = $("#prefs");
  for (const [k, v] of Object.entries(prefs)) {
    const el = form.elements[k];
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v;
  }
}
$("#prefs").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = {};
  for (const el of e.target.elements) {
    if (!el.name) continue;
    data[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  await api("/api/prefs", data);
  $("#prefs-saved").textContent = "Saved. Applies from the next scan.";
  state.items = [];
});
async function action(btn, path, done) {
  btn.disabled = true;
  try { await api(path, {}); alert(done); } catch (err) { alert("Problem: " + err.message); }
  btn.disabled = false;
  loadStatus();
}
$("#scan-now").addEventListener("click", (e) => action(e.target, "/api/scan", "Scan started. Deals will appear in a minute."));
$("#refresh-cat").addEventListener("click", (e) => action(e.target, "/api/catalog/refresh", "Updating card list. Takes a minute or two."));
$("#test-alert").addEventListener("click", (e) => action(e.target, "/api/test-alert", "Test message sent. Check Telegram."));

// ---------------------------------------------------------------- boot
async function refresh() {
  try {
    const s = await loadStatus();
    await loadDeals();
    if (!state.items.length && s.catalog_updated_at && !$("#tab-checklist").hidden) loadCards();
  } catch (err) {
    $("#status").textContent = "Can't reach the server: " + err.message;
  }
}
showTab(localGet("tab", "deals"));
refresh();
setInterval(refresh, 60_000);
