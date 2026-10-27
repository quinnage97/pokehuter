"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const gbp = (n) => (n == null ? "–" : "£" + Number(n).toFixed(2));
const COND = { NM: "Near Mint", LP: "Lightly Played", MP: "Moderately Played", HP: "Heavily Played", DMG: "Damaged", "?": "Condition not stated" };
const PHOTO = { genuine: ["good", "📷 Looks genuine"], unsure: ["warn", "📷 Photo unclear"], fake: ["bad", "📷 Fake"], not_a_card: ["bad", "📷 Not a card"], wrong_card: ["bad", "📷 Wrong card"] };
const FILTER_DEFAULTS = { q: "", sort: "best", poke: "All", cond: [], vars: [], max: "", min: "", disc: "", fb: "", star: false, photo: false, verified: false, post: false };
const state = {
  scope: store("scope", "deals"),
  listings: [],
  watch: [],
  items: [],
  status: null,
  f: { ...FILTER_DEFAULTS, ...JSON.parse(store("filters", "{}")) },
  c: { q: "", poke: store("cpoke", "All"), status: store("cstatus", "need"), era: "", star: false, stamped: true, hidden: false },
};

function store(key, fallback, value) {
  try {
    if (value !== undefined) localStorage.setItem("ph." + key, value);
    return localStorage.getItem("ph." + key) ?? fallback;
  } catch { return fallback; }
}
async function api(path, body, method) {
  const opts = body === undefined && !method ? {} : {
    method: method || "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3200);
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
  const mins = Math.round((new Date(iso) - Date.now()) / 60000);
  return mins <= 0 ? "any moment" : `in ${mins} min`;
}

// ---------------------------------------------------------------- pictures
// Each picture has fallbacks: your chosen picture → TCGdex → pokemontcg.io → a placeholder.
function cardSources(c, size = "low") {
  const out = [];
  if (c.image_override) out.push(c.image_override);
  const img = c.card_image ?? c.image; // listings carry the eBay photo in .image
  if (img) out.push(c.custom ? img : `${img}/${size}.webp`);
  if (c.image_alt) out.push(c.image_alt);
  return out;
}
function picture(sources, label, cls = "") {
  if (!sources.length) return `<div class="placeholder ${cls}">${esc(label)}</div>`;
  const [first, ...rest] = sources;
  return `<img class="${cls}" loading="lazy" src="${esc(first)}" data-rest="${esc(rest.join("|"))}" data-label="${esc(label)}" alt="" onerror="nextPicture(this)">`;
}
window.nextPicture = (img) => {
  const rest = (img.dataset.rest || "").split("|").filter(Boolean);
  if (rest.length) {
    img.src = rest.shift();
    img.dataset.rest = rest.join("|");
  } else {
    img.outerHTML = `<div class="placeholder ${img.className}">${esc(img.dataset.label)}</div>`;
  }
};
function showPicture(src, title, extra = "") {
  const d = $("#card-dialog");
  d.innerHTML = `<div class="dlg"><button class="btn ghost small close" onclick="this.closest('dialog').close()">Close</button>
    <h2>${esc(title)}</h2>${src}${extra}</div>`;
  d.showModal();
}
$("#card-dialog").addEventListener("click", (e) => { if (e.target.id === "card-dialog") e.target.close(); });

// ---------------------------------------------------------------- tabs
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) showTab(b.dataset.tab);
});
function showTab(tab) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$("main > section").forEach((s) => (s.hidden = s.id !== "tab-" + tab));
  store("tab", null, tab);
  if (tab === "checklist") loadCards();
  if (tab === "settings") loadPrefs();
  window.scrollTo({ top: 0 });
}

// ---------------------------------------------------------------- status
async function loadStatus() {
  const s = (state.status = await api("/api/status"));
  const last = s.last_run;
  let text = s.busy ? `⏳ ${s.busy}…` : last ? `Checked eBay ${ago(last.finished_at || last.started_at)}` : "Starting up…";
  if (!s.busy && s.next_scan_at) text += ` · next ${until(s.next_scan_at)}`;
  $("#status").textContent = text;

  const warn = [];
  if (!s.ebay_ready) warn.push("eBay keys aren't set on the server yet, so nothing is being searched.");
  if (!s.catalog_updated_at) warn.push("Downloading the Charizard & Mew card list. This takes a minute or two the first time.");
  if (s.error) warn.push("Last problem: " + s.error);
  $("#setup-warning").hidden = !warn.length;
  $("#setup-warning").innerHTML = warn.map(esc).join("<br>");

  $("#progress").innerHTML = (s.progress || []).map((p) => {
    const pct = p.total ? Math.round((100 * (p.owned || 0)) / p.total) : 0;
    return `<div class="progress ${p.pokemon.toLowerCase() === "mew" ? "mew" : ""}">
      <div class="ring" style="--p:${pct}"><span>${pct}%</span></div>
      <div><div class="pn">${esc(p.pokemon)} master set</div><div class="ps">${p.owned || 0} of ${p.total} versions collected</div></div></div>`;
  }).join("");

  const ps = $("#photo-state");
  ps.textContent = s.photo_check_ready ? "On" : "Off: no key";
  ps.className = "badge " + (s.photo_check_ready ? "good" : "warn");
  $("#system").textContent = [
    `eBay keys: ${s.ebay_ready ? "set ✅" : "missing ❌"}`,
    `Telegram: ${s.telegram_ready ? "set ✅" : "missing ❌"}`,
    `Photo check: ${s.photo_check_ready ? "on ✅" : "off (no key)"}`,
    `Scans every ${s.interval_minutes} min · eBay calls today ${s.calls_today}/${s.call_budget}`,
    `Card list updated ${ago(s.catalog_updated_at)}`,
    `Exchange rates: €1 = £${s.fx?.EUR ?? "?"}, $1 = £${s.fx?.USD ?? "?"}`,
    last ? `Last scan: ${last.listings_seen} listings, ${last.deals} deals, ${last.alerts} alerts` : "",
    s.error ? `Last problem: ${s.error}` : "",
  ].filter(Boolean).join("\n");
  return s;
}

// ---------------------------------------------------------------- deals + filters
async function loadDeals() {
  const data = await api("/api/deals" + (state.scope === "all" ? "?everything=true" : ""));
  state.listings = data.deals;
  state.watch = data.watch;
  if (state.scope === "deals") $("#deal-count").textContent = data.deals.length || "";
  renderWatch();
  renderDeals();
}

function syncFilterInputs() {
  const f = state.f;
  $("#f-search").value = f.q;
  $("#f-sort").value = f.sort;
  $("#f-max").value = f.max;
  $("#f-min").value = f.min;
  $("#f-disc").value = f.disc;
  $("#f-fb").value = f.fb;
  $("#f-star").checked = f.star;
  $("#f-photo").checked = f.photo;
  $("#f-verified").checked = f.verified;
  $("#f-post").checked = f.post;
  const pokes = ["All", ...(state.status?.progress || []).map((p) => p.pokemon)];
  $("#f-poke").innerHTML = pokes.map((p) => `<button data-v="${esc(p)}" class="${p === f.poke ? "on" : ""}">${esc(p)}</button>`).join("");
  $$("#f-cond button").forEach((b) => b.classList.toggle("on", f.cond.includes(b.dataset.v)));
  $$("#f-var button").forEach((b) => b.classList.toggle("on", f.vars.includes(b.dataset.v)));
  $$("#scope button").forEach((b) => b.classList.toggle("on", b.dataset.scope === state.scope));
}
function activeFilterCount() {
  const f = state.f;
  return ["q", "max", "min", "disc", "fb"].filter((k) => f[k]).length + (f.poke !== "All") + f.cond.length + f.vars.length +
    ["star", "photo", "verified", "post"].filter((k) => f[k]).length;
}
function saveFilters() {
  store("filters", null, JSON.stringify(state.f));
  $("#filter-count").textContent = activeFilterCount() || "";
  renderDeals();
}
$("#filter-toggle").addEventListener("click", () => $("#filters").classList.toggle("open"));
$("#scope").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.scope = b.dataset.scope;
  store("scope", null, state.scope);
  syncFilterInputs();
  loadDeals();
});
[["#f-search", "q"], ["#f-sort", "sort"], ["#f-max", "max"], ["#f-min", "min"], ["#f-disc", "disc"], ["#f-fb", "fb"]].forEach(([sel, key]) =>
  $(sel).addEventListener("input", (e) => { state.f[key] = e.target.value; saveFilters(); }));
[["#f-star", "star"], ["#f-photo", "photo"], ["#f-verified", "verified"], ["#f-post", "post"]].forEach(([sel, key]) =>
  $(sel).addEventListener("change", (e) => { state.f[key] = e.target.checked; saveFilters(); }));
$("#f-poke").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.f.poke = b.dataset.v;
  syncFilterInputs();
  saveFilters();
});
[["#f-cond", "cond"], ["#f-var", "vars"]].forEach(([sel, key]) => $(sel).addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  const list = state.f[key];
  const i = list.indexOf(b.dataset.v);
  i >= 0 ? list.splice(i, 1) : list.push(b.dataset.v);
  b.classList.toggle("on", i < 0);
  saveFilters();
}));
$("#f-reset").addEventListener("click", () => {
  state.f = { ...FILTER_DEFAULTS, cond: [], vars: [] };
  syncFilterInputs();
  saveFilters();
});

function passes(d) {
  const f = state.f;
  const q = f.q.trim().toLowerCase();
  if (q && !`${d.card_name} ${d.set_name} ${d.local_id} ${d.title}`.toLowerCase().includes(q)) return false;
  if (f.poke !== "All" && d.pokemon !== f.poke) return false;
  if (f.cond.length && !f.cond.includes(d.condition)) return false;
  if (f.vars.length) {
    const base = d.variant.split(":")[0];
    const stamped = d.variant.includes(":");
    if (!(f.vars.includes(base) && !stamped) && !(stamped && f.vars.includes("stamped"))) return false;
  }
  if (f.max !== "" && d.total > Number(f.max)) return false;
  if (f.min !== "" && d.total < Number(f.min)) return false;
  if (f.disc !== "" && !((d.discount_pct ?? -1) >= Number(f.disc))) return false;
  if (f.fb !== "" && (d.feedback_score ?? 0) < Number(f.fb)) return false;
  if (f.star && !d.priority) return false;
  if (f.photo && d.photo_verdict !== "genuine") return false;
  if (f.verified && !d.ebay_median_gbp) return false;
  if (f.post && !d.shipping_known) return false;
  return true;
}
const SORTS = {
  best: (a, b) => b.is_deal - a.is_deal || b.priority - a.priority || (b.discount_pct ?? -999) - (a.discount_pct ?? -999),
  saving: (a, b) => ((b.fair_gbp ?? 0) - b.total) - ((a.fair_gbp ?? 0) - a.total),
  cheap: (a, b) => a.total - b.total,
  dear: (a, b) => (b.fair_gbp ?? 0) - (a.fair_gbp ?? 0),
  new: (a, b) => String(b.listed_at || b.first_seen).localeCompare(String(a.listed_at || a.first_seen)),
  old: (a, b) => String(a.release_date || "9").localeCompare(String(b.release_date || "9")),
};

function renderDeals() {
  const shown = state.listings.filter(passes).sort(SORTS[state.f.sort] || SORTS.best);
  const what = state.scope === "deals" ? "deals" : "listings";
  $("#result-line").textContent = state.listings.length
    ? `Showing ${shown.length} of ${state.listings.length} ${what}` + (activeFilterCount() ? " (filtered)" : "")
    : "";
  if (!shown.length) {
    $("#deals").innerHTML = state.listings.length
      ? `<div class="empty"><div class="big">🔍</div>Nothing matches these filters. <button class="btn ghost small" onclick="document.querySelector('#f-reset').click()">Reset filters</button></div>`
      : `<div class="empty"><div class="big">🕵️</div>${state.scope === "deals"
        ? "No deals right now. The bot checks eBay every 15 minutes and pings Telegram when it finds one."
        : "No listings seen yet for the cards you still need."}</div>`;
    return;
  }
  $("#deals").innerHTML = shown.map(dealCard).join("");
}

function dealCard(d) {
  const saving = d.fair_gbp != null ? d.fair_gbp - d.total : null;
  const off = d.discount_pct != null
    ? `<span class="off ${d.is_deal ? "" : "meh"}">${d.discount_pct > 0 ? Math.round(d.discount_pct) + "% under" : Math.round(-d.discount_pct) + "% over"}</span>` : "";
  const photo = PHOTO[d.photo_verdict];
  const badges = [
    `<span class="badge">${esc(COND[d.condition] || d.condition)}</span>`,
    photo ? `<span class="badge ${photo[0]}" title="${esc(d.photo_reason || "")}">${photo[1]}</span>` : "",
    d.ebay_median_gbp ? `<span class="badge good">✓ Compared with ${d.ebay_comps} eBay listings</span>` : `<span class="badge warn">Not compared with eBay</span>`,
    d.target_price && d.total <= d.target_price ? `<span class="badge good">≤ your £${d.target_price} alert</span>` : "",
    !d.shipping_known ? `<span class="badge warn">Postage not listed</span>` : "",
  ].join("");
  const number = d.local_id + (d.set_official ? "/" + d.set_official : "");
  return `
  <article class="deal ${d.priority ? "priority" : ""} ${d.is_deal ? "" : "not-deal"}" data-listing="${esc(d.listing_id)}" data-item="${esc(d.match_item_id)}">
    <div class="media">
      ${picture(d.image ? [d.image] : [], "No photo", "photo")}
      <div class="ref">${picture(cardSources(d), d.card_name)}<span class="lbl">official card</span></div>
      ${off}${d.priority ? `<span class="star-badge">⭐</span>` : ""}
    </div>
    <div class="body">
      <div class="name">${esc(d.card_name)}</div>
      <div class="meta">${esc(d.set_name)} · #${esc(number)} · ${esc(d.variant_label || d.variant)}</div>
      <div class="priceline"><span class="price">${gbp(d.total)}</span><span class="fair">delivered${saving != null ? ` · fair <b>${gbp(d.fair_gbp)}</b>` : ""}</span></div>
      <div class="fair">Price guide ${gbp(d.market_gbp)} · eBay typical ${d.ebay_median_gbp ? gbp(d.ebay_median_gbp) : "–"}</div>
      <div class="badges">${badges}</div>
      <div class="title">${esc(d.title)}</div>
      ${d.photo_reason && photo ? `<div class="hint">📷 ${esc(d.photo_reason)}</div>` : ""}
      ${d.warning ? `<div class="warns">⚠️ ${esc(d.warning)}</div>` : ""}
      <div class="meta" style="margin-top:6px">Seller ${esc(d.seller || "?")} (${d.feedback_score ?? "?"} feedback) · seen ${ago(d.last_seen)}</div>
    </div>
    <div class="bar">
      <a class="btn primary" href="${esc(d.url)}" target="_blank" rel="noopener">Open on eBay</a>
      <button class="btn" data-act="got" title="We bought it: tick it off">Got it ✓</button>
      <button class="btn ghost" data-act="dismiss" title="Hide this listing">✕</button>
    </div>
  </article>`;
}
$("#deals").addEventListener("click", async (e) => {
  const card = e.target.closest(".deal");
  if (!card) return;
  if (e.target.classList.contains("photo")) {
    showPicture(`<img src="${esc(e.target.src)}" alt="">`, $(".name", card).textContent);
    return;
  }
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  if (btn.dataset.act === "got") {
    await api(`/api/items/${encodeURIComponent(card.dataset.item)}`, { owned: true });
    toast("Ticked off in your collection 🎉");
    state.items = [];
  } else {
    await api(`/api/listings/${encodeURIComponent(card.dataset.listing)}/dismiss`, {});
    toast("Listing hidden");
  }
  await Promise.all([loadDeals(), loadStatus()]);
});

function renderWatch() {
  $("#watch-wrap").hidden = !state.watch.length;
  $("#watch").innerHTML = state.watch.map((w) => `
    <div class="w">
      ${picture(cardSources(w), w.card_name)}
      <div>
        <div class="n">${esc(w.card_name)}</div>
        <div class="hint">${esc(w.set_name)} #${esc(w.local_id)} · ${esc(w.variant_label || w.variant)}</div>
        ${w.url ? `<a href="${esc(w.url)}" target="_blank" rel="noopener"><b>${gbp(w.total)}</b></a> <span class="hint">${esc(w.condition)}</span>`
          : `<span class="hint">None listed yet</span>`}
        <div class="hint">guide ${gbp(w.market_gbp)}${w.target_price ? ` · alert at ${gbp(w.target_price)}` : ""}</div>
      </div>
    </div>`).join("");
}

// ---------------------------------------------------------------- collection
async function loadCards() {
  if (state.items.length) return renderChecklist();
  const { items } = await api("/api/cards");
  state.items = items;
  const eras = [...new Set(items.map((i) => i.serie_name).filter(Boolean))];
  $("#c-era").innerHTML = `<option value="">All eras</option>` + eras.map((e) => `<option>${esc(e)}</option>`).join("");
  $("#c-era").value = state.c.era;
  renderChecklist();
}
function renderCollectionChips() {
  const pokes = ["All", ...new Set(state.items.map((i) => i.pokemon))];
  $("#c-poke").innerHTML = pokes.map((p) => `<button data-v="${esc(p)}" class="${p === state.c.poke ? "on" : ""}">${esc(p)}</button>`).join("");
  $$("#c-status button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.c.status));
}
$("#c-poke").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.c.poke = store("cpoke", null, b.dataset.v);
  renderChecklist();
});
$("#c-status").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.c.status = store("cstatus", null, b.dataset.v);
  renderChecklist();
});
$("#c-search").addEventListener("input", (e) => { state.c.q = e.target.value; renderChecklist(); });
$("#c-era").addEventListener("change", (e) => { state.c.era = e.target.value; renderChecklist(); });
[["#c-star", "star"], ["#c-stamped", "stamped"], ["#c-hidden", "hidden"]].forEach(([sel, key]) =>
  $(sel).addEventListener("change", (e) => { state.c[key] = e.target.checked; renderChecklist(); }));

function variantVisible(v) {
  const c = state.c;
  if (v.hidden && !c.hidden) return false;
  if (!c.stamped && v.variant.includes(":")) return false;
  if (c.status === "need" && v.owned) return false;
  if (c.status === "have" && !v.owned) return false;
  if (c.star && !v.priority) return false;
  return true;
}
function renderChecklist() {
  renderCollectionChips();
  if (!state.items.length) {
    $("#checklist").innerHTML = `<div class="empty"><div class="big">⏳</div>The card list is still downloading. Check back in a minute.</div>`;
    return;
  }
  const c = state.c;
  const q = c.q.trim().toLowerCase();
  const cards = new Map();
  for (const it of state.items) {
    if (c.poke !== "All" && it.pokemon !== c.poke) continue;
    if (c.era && it.serie_name !== c.era) continue;
    if (q && !`${it.name} ${it.set_name} ${it.local_id}`.toLowerCase().includes(q)) continue;
    if (!cards.has(it.card_id)) cards.set(it.card_id, { ...it, variants: [] });
    cards.get(it.card_id).variants.push(it);
  }
  const sets = new Map();
  for (const card of cards.values()) {
    card.shown = card.variants.filter(variantVisible);
    if (!card.shown.length) continue;
    const key = card.set_id === "custom" ? "custom" : card.set_id;
    if (!sets.has(key)) sets.set(key, { name: card.set_id === "custom" ? "Added by you" : card.set_name, date: card.release_date, cards: [], have: 0, total: 0 });
    const s = sets.get(key);
    s.cards.push(card);
  }
  for (const s of sets.values()) {
    for (const card of s.cards) for (const v of card.variants) if (!v.hidden) { s.total++; s.have += v.owned ? 1 : 0; }
  }
  if (!sets.size) {
    $("#checklist").innerHTML = `<div class="empty"><div class="big">${c.status === "need" ? "🏆" : "🔍"}</div>${c.status === "need"
      ? "Nothing left to find with these filters. Amazing!" : "No cards match these filters."}</div>`;
    return;
  }
  $("#checklist").innerHTML = [...sets.values()].map((s) => `
    <div class="set">
      <div class="set-head"><h3>${esc(s.name)} <span class="hint">${s.date && s.date !== "9999" ? s.date.slice(0, 4) : ""}</span></h3>
        <span class="hint"><b>${s.have}</b>/${s.total} owned</span></div>
      <div class="cards">${s.cards.map(tile).join("")}</div>
    </div>`).join("");
}
function tile(card) {
  const live = card.variants.filter((v) => !v.hidden);
  const done = live.length && live.every((v) => v.owned);
  const number = card.local_id + (card.set_official ? "/" + card.set_official : "");
  return `
  <div class="tile ${done ? "done" : ""}" data-card="${esc(card.card_id)}">
    <div class="art">${picture(cardSources(card), card.name)}${done ? `<span class="owned-tag">✓ Got it</span>` : ""}</div>
    <div class="name">${esc(card.name)}</div>
    <div class="num">#${esc(number)}${card.rarity ? " · " + esc(card.rarity) : ""}</div>
    ${card.shown.map(variantRow).join("")}
    ${card.custom ? `<button class="linkbtn" data-remove="${esc(card.card_id)}" style="margin:6px 0 0">Remove card</button>` : ""}
  </div>`;
}
function variantRow(v) {
  return `
  <div class="variant" data-id="${esc(v.id)}">
    <div class="vtop">
      <label><input type="checkbox" data-f="owned" ${v.owned ? "checked" : ""}><span>${esc(v.variant_label || v.variant)}</span></label>
      <span class="mk" title="Price guide${v.ebay_median_gbp ? " · eBay typical " + gbp(v.ebay_median_gbp) : ""}">${gbp(v.market_gbp)}</span>
      <button class="star ${v.priority ? "on" : ""}" data-f="priority" title="Priority">★</button>
    </div>
    <div class="vbot">Alert at £<input type="number" min="0" step="0.5" inputmode="decimal" data-f="target_price" value="${v.target_price ?? ""}" placeholder="auto">
      <button class="linkbtn" data-f="hidden">${v.hidden ? "unskip" : "skip"}</button></div>
  </div>`;
}
async function saveItem(row, changes) {
  const updated = await api(`/api/items/${encodeURIComponent(row.dataset.id)}`, changes);
  const local = state.items.find((i) => i.id === updated.id);
  if (local) Object.assign(local, updated);
  loadStatus();
}
$("#checklist").addEventListener("change", async (e) => {
  const row = e.target.closest(".variant");
  if (!row) return;
  if (e.target.dataset.f === "owned") {
    await saveItem(row, { owned: e.target.checked });
    toast(e.target.checked ? "Added to your collection 🎉" : "Marked as needed");
    setTimeout(renderChecklist, 600);
  }
  if (e.target.dataset.f === "target_price") {
    await saveItem(row, { target_price: e.target.value });
    toast(e.target.value ? `You'll be alerted at £${e.target.value} or less` : "Alert price removed");
  }
});
$("#checklist").addEventListener("click", async (e) => {
  const remove = e.target.closest("[data-remove]");
  if (remove) {
    if (!confirm("Remove this card you added?")) return;
    await api(`/api/cards/custom/${encodeURIComponent(remove.dataset.remove)}`, undefined, "DELETE");
    state.items = [];
    return loadCards();
  }
  const art = e.target.closest(".art");
  if (art) {
    const card = state.items.find((i) => i.card_id === art.closest(".tile").dataset.card);
    const prices = state.items.filter((i) => i.card_id === card.card_id).map((v) =>
      `<div>${esc(v.variant_label)}: guide ${gbp(v.market_gbp)}${v.ebay_median_gbp ? ` · eBay typical ${gbp(v.ebay_median_gbp)} (${v.ebay_comps} listed)` : ""}${v.owned ? " · ✓ owned" : ""}</div>`).join("");
    showPicture(picture(cardSources(card, "high"), card.name), card.name,
      `<p class="hint">${esc(card.set_name)} · #${esc(card.local_id)}${card.set_official ? "/" + card.set_official : ""}${card.rarity ? " · " + esc(card.rarity) : ""}</p>${prices}
       <p><button class="btn small" onclick="choosePicture('${esc(card.card_id)}')">🖼 Wrong picture? Change it</button></p>`);
    return;
  }
  const btn = e.target.closest("button[data-f]");
  if (!btn) return;
  const row = btn.closest(".variant");
  const local = state.items.find((i) => i.id === row.dataset.id);
  if (btn.dataset.f === "priority") {
    btn.classList.toggle("on");
    const on = btn.classList.contains("on");
    await saveItem(row, { priority: on });
    toast(on ? "⭐ Starred: searched every scan" : "Unstarred");
  } else if (btn.dataset.f === "hidden") {
    await saveItem(row, { hidden: !local.hidden });
    renderChecklist();
  }
});

// pick a different picture for a card
window.choosePicture = async (cardId) => {
  const card = state.items.find((i) => i.card_id === cardId);
  const { official, ebay } = await api(`/api/cards/${encodeURIComponent(cardId)}/photos`);
  const thumb = (url) => `<button class="pick" data-url="${esc(url)}"><img src="${esc(url)}" alt="" loading="lazy" onerror="this.closest('.pick').remove()"></button>`;
  showPicture("", `Choose a picture for ${card.name}`, `
    <p class="hint">Tap the right picture. It's saved for everyone using the site.</p>
    ${official.length ? `<h3 class="pick-h">Official pictures</h3><div class="picks">${official.map(thumb).join("")}</div>` : ""}
    ${ebay.length ? `<h3 class="pick-h">Photos from eBay listings</h3><div class="picks">${ebay.map(thumb).join("")}</div>` : `<p class="hint">No eBay photos of this card yet.</p>`}
    <h3 class="pick-h">Or paste a picture link</h3>
    <div class="frow"><input id="pick-url" type="url" placeholder="https://…"><button class="btn small" data-url-input>Use link</button></div>
    ${card.image_override ? `<button class="btn ghost small" data-url="">Go back to the default picture</button>` : ""}`);
  $("#card-dialog").onclick = async (e) => {
    if (e.target.id === "card-dialog") return e.target.close();
    const b = e.target.closest("[data-url],[data-url-input]");
    if (!b) return;
    const url = b.hasAttribute("data-url-input") ? $("#pick-url").value.trim() : b.dataset.url;
    try {
      await api(`/api/cards/${encodeURIComponent(cardId)}/image`, { url });
      state.items.filter((i) => i.card_id === cardId).forEach((i) => (i.image_override = url || null));
      $("#card-dialog").close();
      $("#card-dialog").onclick = null;
      toast(url ? "Picture updated 🖼" : "Back to the default picture");
      renderChecklist();
    } catch (err) { toast(err.message); }
  };
};

// add a missing card
$("#add-card").addEventListener("click", () => {
  $("#add-poke").innerHTML = [...new Set(state.items.map((i) => i.pokemon))].map((p) => `<option>${esc(p)}</option>`).join("");
  $("#add-form").reset();
  $$("#add-vars button").forEach((b) => b.classList.toggle("on", b.dataset.v === "holo"));
  $("#add-dialog").showModal();
});
$("#add-vars").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) b.classList.toggle("on"); });
$("#add-dialog").addEventListener("close", async () => {
  if ($("#add-dialog").returnValue !== "ok") return;
  const data = Object.fromEntries(new FormData($("#add-form")));
  data.variants = $$("#add-vars button.on").map((b) => b.dataset.v);
  try {
    await api("/api/cards/custom", data);
    toast("Card added 👍");
    state.items = [];
    loadCards();
  } catch (err) { toast("Couldn't add: " + err.message); }
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
  for (const el of e.target.elements) if (el.name) data[el.name] = el.type === "checkbox" ? el.checked : el.value;
  try {
    await api("/api/prefs", data);
    toast("Settings saved. They apply from the next scan.");
  } catch (err) { toast("Problem: " + err.message); }
});
$("#photo-save").addEventListener("click", async () => {
  const key = $("#photo-key").value.trim();
  if (!key) return toast("Paste your key first");
  try {
    await api("/api/photo-check-key", { key });
    $("#photo-key").value = "";
    toast("Photo check is on 📷");
    loadStatus();
  } catch (err) { toast(err.message); }
});
$("#photo-clear").addEventListener("click", async () => {
  await api("/api/photo-check-key", { key: "" });
  toast("Key removed. Photo check is off.");
  loadStatus();
});
async function action(btn, path, done) {
  btn.disabled = true;
  try { await api(path, {}); toast(done); } catch (err) { toast("Problem: " + err.message); }
  btn.disabled = false;
  loadStatus();
}
$("#scan-now").addEventListener("click", (e) => action(e.target, "/api/scan", "Scanning now. Deals appear in a minute or two."));
$("#refresh-cat").addEventListener("click", (e) => { state.items = []; action(e.target, "/api/catalog/refresh", "Updating the card list. Takes a minute or two."); });
$("#test-alert").addEventListener("click", (e) => action(e.target, "/api/test-alert", "Test sent. Check Telegram."));

// ---------------------------------------------------------------- boot
async function refresh() {
  try {
    await loadStatus();
    if (!$("#f-poke").children.length) syncFilterInputs();
    await loadDeals();
  } catch (err) {
    $("#status").textContent = "Can't reach the server: " + err.message;
  }
}
syncFilterInputs();
$("#filter-count").textContent = activeFilterCount() || "";
showTab(store("tab", "deals"));
refresh();
setInterval(refresh, 60_000);
