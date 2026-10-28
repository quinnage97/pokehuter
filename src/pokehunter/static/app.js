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
  items: [],
  status: null,
  f: { ...FILTER_DEFAULTS, ...JSON.parse(store("filters", "{}")) },
  basket: new Map(),
  picking: false,
  picked: new Set(),
  c: { q: "", poke: store("cpoke", "All"), status: store("cstatus", "need"), era: "", star: false, stamped: true, hidden: false },
};

function store(key, fallback, value) {
  try {
    if (value !== undefined) localStorage.setItem("ph." + key, value);
    return localStorage.getItem("ph." + key) ?? fallback;
  } catch { return fallback; }
}
// Loading feedback: while anything is being saved, a bar runs under the header and the
// button that was tapped shows a spinning wheel, so nothing looks frozen.
let lastButton = null;
let pending = 0;
document.addEventListener("click", (e) => { lastButton = e.target.closest("button, .btn"); }, true);
function setLoading(on) {
  pending += on ? 1 : -1;
  $("#loadbar").hidden = pending <= 0;
}
async function api(path, body, method) {
  const opts = body === undefined && !method ? {} : {
    method: method || "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
  };
  const writing = !!opts.method;
  const button = writing && lastButton && document.contains(lastButton) ? lastButton : null;
  if (writing) setLoading(true);
  button?.classList.add("loading");
  try {
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return await res.json();
  } finally {
    if (writing) setLoading(false);
    button?.classList.remove("loading");
  }
}
function toast(msg, undo) {
  const t = $("#toast");
  t.textContent = msg;
  if (undo) {
    const b = document.createElement("button");
    b.className = "undo";
    b.textContent = "Undo";
    b.onclick = async () => { t.hidden = true; await undo(); };
    t.append(b);
  }
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), undo ? 7000 : 3200);
}
// Mark card versions owned / not owned, then refresh everything that shows them.
async function setOwned(ids, owned) {
  await api("/api/items-bulk", { ids, owned });
  state.items = [];
  await Promise.all([loadStatus(), loadDeals()]);
  if (!$("#tab-checklist").hidden) loadCards();
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
  d.onclick = (e) => { if (e.target === d) d.close(); };
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
  if (tab === "settings") { loadPrefs(); loadLearning(); }
  $("#pick-bar").hidden = tab !== "checklist" || !state.picking;
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
  $("#photo-usage").textContent = s.photo_check_ready
    ? `Checked today: ${s.photo_checks_today} of ${s.photo_checks_per_day}. You can also set a monthly spend limit in the Anthropic Console billing settings.`
    : "";
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
  if (state.scope === "deals") $("#deal-count").textContent = data.deals.length || "";
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
      <button class="btn icon ${state.basket.has(d.listing_id) ? "on" : ""}" data-act="basket" title="Add to basket to total up">🧺</button>
      <button class="btn icon" data-act="got" title="We bought it: tick it off">✓</button>
      <button class="btn icon" data-act="fake" title="Report a problem with this listing">🚩</button>
      <button class="btn icon ghost" data-act="dismiss" title="Hide this listing">✕</button>
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
  if (e.target.closest(".ref")) {
    const d = state.listings.find((l) => l.listing_id === card.dataset.listing);
    const cardId = card.dataset.item.split(":")[0];
    showPicture(picture(cardSources(d, "high"), d.card_name), d.card_name,
      `<p class="hint">This is the picture used in your collection too.</p>
       <p><button class="btn small" onclick="choosePicture('${esc(cardId)}')">🖼 Wrong picture? Change it</button></p>`);
    return;
  }
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  if (btn.dataset.act === "basket") {
    const id = card.dataset.listing;
    if (state.basket.has(id)) await basketRemove(id); else await basketAdd([id]);
    btn.classList.toggle("on", state.basket.has(id));
    return;
  }
  if (btn.dataset.act === "fake") return reportFake(card.dataset.listing, $(".name", card).textContent);
  if (btn.dataset.act === "got") {
    const itemId = card.dataset.item;
    await api(`/api/items/${encodeURIComponent(itemId)}`, { owned: true });
    toast("Ticked off in your collection 🎉", async () => { await setOwned([itemId], false); toast("Undone: marked as needed again"); });
    state.items = [];
  } else {
    await api(`/api/listings/${encodeURIComponent(card.dataset.listing)}/dismiss`, {});
    toast("Listing hidden");
  }
  await Promise.all([loadDeals(), loadStatus()]);
});

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
  $("#checklist").innerHTML = recentlyAdded() + [...sets.values()].map((s) => `
    <div class="set">
      <div class="set-head"><h3>${esc(s.name)} <span class="hint">${s.date && s.date !== "9999" ? s.date.slice(0, 4) : ""}</span></h3>
        <span class="hint"><b>${s.have}</b>/${s.total} owned</span></div>
      <div class="cards">${s.cards.map(tile).join("")}</div>
    </div>`).join("");
}
function recentlyAdded() {
  const recent = state.items.filter((i) => i.owned && i.owned_at)
    .sort((a, b) => b.owned_at.localeCompare(a.owned_at)).slice(0, 8);
  if (!recent.length || state.picking) return "";
  return `
    <details class="panel recent" ${store("recentOpen", "1") === "1" ? "open" : ""}>
      <summary><b>🕘 Recently added</b> <span class="hint">Added one by mistake? Remove it here.</span></summary>
      ${recent.map((i) => `
        <div class="brow">
          ${picture(cardSources(i), i.name, "bthumb")}
          <div class="bmain"><b>${esc(i.name)}</b><div class="hint">${esc(i.set_name)} #${esc(i.local_id)} · ${esc(i.variant_label || i.variant)}</div>
            <div class="hint">added ${ago(i.owned_at)}</div></div>
          <div class="bside"><button class="btn small" data-unown="${esc(i.id)}">Remove</button></div>
        </div>`).join("")}
    </details>`;
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
  if (state.picking) {
    const now = v.cheapest_total != null ? `eBay now <b>${gbp(v.cheapest_total)}</b> (${esc(v.cheapest_condition)})` : "none listed now";
    return `
  <div class="variant pickable ${state.picked.has(v.id) ? "picked" : ""}" data-pick="${esc(v.id)}">
    <div class="vtop"><span class="pickbox">${state.picked.has(v.id) ? "✓" : ""}</span><span class="vname">${esc(v.variant_label || v.variant)}</span>
      <span class="mk">${gbp(v.market_gbp)}</span></div>
    <div class="vbot">${now}</div>
  </div>`;
  }
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
    const owned = e.target.checked;
    await saveItem(row, { owned });
    toast(owned ? "Added to your collection 🎉" : "Marked as needed", async () => {
      await setOwned([row.dataset.id], !owned);
      toast("Undone");
    });
    setTimeout(renderChecklist, 600);
  }
  if (e.target.dataset.f === "target_price") {
    await saveItem(row, { target_price: e.target.value });
    toast(e.target.value ? `You'll be alerted at £${e.target.value} or less` : "Alert price removed");
  }
});
$("#checklist").addEventListener("toggle", (e) => {
  if (e.target.classList?.contains("recent")) store("recentOpen", null, e.target.open ? "1" : "0");
}, true);
$("#checklist").addEventListener("click", async (e) => {
  const unown = e.target.closest("[data-unown]");
  if (unown) {
    const id = unown.dataset.unown;
    await setOwned([id], false);
    toast("Removed from your collection", async () => { await setOwned([id], true); toast("Undone"); });
    return;
  }
  const pick = e.target.closest("[data-pick]");
  if (pick) {
    const id = pick.dataset.pick;
    state.picked.has(id) ? state.picked.delete(id) : state.picked.add(id);
    pick.classList.toggle("picked", state.picked.has(id));
    $(".pickbox", pick).textContent = state.picked.has(id) ? "✓" : "";
    renderPickBar();
    return;
  }
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

// ---------------------------------------------------------------- price-up mode
$("#pick-mode").addEventListener("click", () => {
  state.picking = !state.picking;
  if (!state.picking) state.picked.clear();
  $("#pick-mode").classList.toggle("on", state.picking);
  $("#pick-mode").textContent = state.picking ? "Done pricing" : "🧮 Price up cards";
  renderChecklist();
  renderPickBar();
});
function renderPickBar() {
  const bar = $("#pick-bar");
  if (!state.picking) { bar.hidden = true; return; }
  const chosen = state.items.filter((i) => state.picked.has(i.id));
  const guide = chosen.reduce((s, i) => s + (i.market_gbp || 0), 0);
  const listed = chosen.filter((i) => i.cheapest_total != null);
  const now = listed.reduce((s, i) => s + i.cheapest_total, 0);
  bar.hidden = false;
  bar.innerHTML = chosen.length
    ? `<div class="sum"><b>${chosen.length} selected</b>
        <span>Price guide ${gbp(guide)}${chosen.some((i) => i.market_gbp == null) ? "+" : ""}</span>
        <span>Cheapest on eBay now <b>${listed.length ? gbp(now) : "–"}</b>${listed.length < chosen.length ? ` (${listed.length} of ${chosen.length} listed)` : ""}</span></div>
       <div class="actions">${listed.length ? `<button class="btn primary small" id="pick-basket">🧺 Add cheapest to basket</button>` : ""}
         <button class="btn ghost small" id="pick-clear">Clear</button></div>`
    : `<div class="sum">Tap card versions to add up what they'd cost.</div>`;
}
$("#pick-bar").addEventListener("click", async (e) => {
  if (e.target.id === "pick-clear") { state.picked.clear(); renderChecklist(); renderPickBar(); }
  if (e.target.id === "pick-basket") {
    const ids = state.items.filter((i) => state.picked.has(i.id) && i.cheapest_listing).map((i) => i.cheapest_listing);
    await basketAdd(ids);
    toast(`Added ${ids.length} listing${ids.length === 1 ? "" : "s"} to the basket 🧺`);
  }
});

// ---------------------------------------------------------------- basket
async function loadBasket() {
  const { items } = await api("/api/basket");
  setBasket(items);
}
function setBasket(items) {
  state.basket = new Map(items.map((i) => [i.listing_id, i]));
  const bar = $("#basket-bar");
  bar.hidden = !items.length;
  if (state.listings.length) renderDeals();
  if (!items.length) return;
  const total = items.reduce((s, i) => s + i.total, 0);
  bar.innerHTML = `<span>🧺 <b>${items.length}</b> in basket · <b>${gbp(total)}</b></span><button class="btn primary small">View</button>`;
}
async function basketAdd(ids) {
  const { items } = await api("/api/basket", { listing_ids: ids });
  setBasket(items);
  toast("Added to basket 🧺");
}
async function basketRemove(id) {
  const { items } = await api(`/api/basket/${encodeURIComponent(id)}`, undefined, "DELETE");
  setBasket(items);
}
$("#basket-bar").addEventListener("click", () => showBasket());
function showBasket() {
  const items = [...state.basket.values()];
  const cards = items.reduce((s, i) => s + i.price, 0);
  const post = items.reduce((s, i) => s + i.shipping, 0);
  const fair = items.reduce((s, i) => s + (i.fair_gbp || 0), 0);
  const bySeller = {};
  items.forEach((i) => (bySeller[i.seller] = (bySeller[i.seller] || 0) + 1));
  const shared = Object.entries(bySeller).filter(([, n]) => n > 1);
  const rows = items.map((i) => `
    <div class="brow">
      ${picture(i.image ? [i.image] : cardSources(i), i.card_name, "bthumb")}
      <div class="bmain"><b>${esc(i.card_name)}</b><div class="hint">${esc(i.set_name)} #${esc(i.local_id)} · ${esc(i.variant_label || "")} · ${esc(i.condition)}</div>
        <div class="hint">${gbp(i.price)} + ${i.shipping_known ? gbp(i.shipping) : "~" + gbp(i.shipping)} postage · ${esc(i.seller || "")}</div></div>
      <div class="bside"><b>${gbp(i.total)}</b><a class="btn small" href="${esc(i.url)}" target="_blank" rel="noopener">eBay</a>
        <button class="linkbtn" data-unbasket="${esc(i.listing_id)}">remove</button></div>
    </div>`).join("");
  showPicture("", `🧺 Basket (${items.length})`, `
    ${rows || `<p class="hint">Empty. Tap 🧺 on any listing to add it.</p>`}
    ${items.length ? `<div class="totals">
      <div><span>Cards</span><b>${gbp(cards)}</b></div>
      <div><span>Postage</span><b>${gbp(post)}</b></div>
      <div class="grand"><span>Total</span><b>${gbp(cards + post)}</b></div>
      ${fair ? `<div><span>Fair value</span><b>${gbp(fair)}</b></div><div class="save"><span>You'd save</span><b>${gbp(fair - cards - post)}</b></div>` : ""}
    </div>
    ${shared.length ? `<p class="hint">💡 ${shared.map(([s, n]) => `${n} items from ${esc(s)}`).join(", ")}: ask about combined postage.</p>` : ""}
    <div class="actions end"><button class="btn ghost small" data-basket="empty">Empty basket</button>
      <button class="btn primary" data-basket="bought">We bought these ✓</button></div>` : ""}`);
  $("#card-dialog").onclick = async (e) => {
    if (e.target.id === "card-dialog") { e.target.close(); return; }
    const un = e.target.closest("[data-unbasket]");
    if (un) { await basketRemove(un.dataset.unbasket); showBasket(); renderDeals(); return; }
    const act = e.target.closest("[data-basket]");
    if (!act) return;
    if (act.dataset.basket === "empty") {
      const { items: left } = await api("/api/basket/all", undefined, "DELETE");
      setBasket(left);
      toast("Basket emptied");
    } else {
      const { newly_owned: ticked } = await api("/api/basket/bought", {});
      setBasket([]);
      state.items = [];
      toast(`Ticked off ${ticked.length} in your collection 🎉`, async () => { await setOwned(ticked, false); toast("Undone: marked as needed again"); });
      loadStatus();
      loadDeals();
    }
    $("#card-dialog").close();
    renderDeals();
  };
}

// ---------------------------------------------------------------- fake reports
const FAKE_REASONS = ["Gold / metal / foil card", "Wrong font or blurry print", "Sticker, skin or print", "Fan art / custom / proxy", "Not English", "Wrong card", "Too cheap to be real"];
function reportFake(listingId, name) {
  showPicture("", `🚩 Report an issue: ${name}`, `
    <p class="hint">What's wrong with this listing? The bot learns from your report: the seller is blocked,
      telltale title words and price levels are learned, and the photo check is told what to look for.
      Choose only "Wrong card" for a real card listed under the wrong name: it's hidden without blocking the seller.</p>
    <div class="chips multi" id="fake-reasons">${FAKE_REASONS.map((r) => `<button>${esc(r)}</button>`).join("")}</div>
    <p><input id="fake-note" type="text" placeholder="Anything else? (optional)" style="width:100%"></p>
    <div class="actions end"><button class="btn ghost" data-fake="cancel">Cancel</button><button class="btn primary" data-fake="send">Submit issue</button></div>`);
  $("#card-dialog").onclick = async (e) => {
    if (e.target.id === "card-dialog") return e.target.close();
    const chip = e.target.closest("#fake-reasons button");
    if (chip) return chip.classList.toggle("on");
    const b = e.target.closest("[data-fake]");
    if (!b) return;
    if (b.dataset.fake === "send") {
      const chosen = $$("#fake-reasons button.on").map((x) => x.textContent);
      if (chosen.length === 1 && chosen[0] === "Wrong card" && !$("#fake-note").value.trim()) {
        // A genuine card listed under the wrong name/number: hide it, don't punish the seller.
        await api(`/api/listings/${encodeURIComponent(listingId)}/dismiss`, {});
        await basketRemove(listingId);
        toast("Hidden as the wrong card (seller not blocked)");
        $("#card-dialog").close();
        return loadDeals();
      }
      const reason = [...chosen, $("#fake-note").value.trim()].filter(Boolean).join("; ");
      b.textContent = "Submitting…";
      $$("#card-dialog button").forEach((x) => (x.disabled = true));
      const learned = await api(`/api/listings/${encodeURIComponent(listingId)}/fake`, { reason });
      state.basket.delete(listingId);
      toast(`Issue submitted. Blocking ${learned.sellers.length} seller${learned.sellers.length === 1 ? "" : "s"} and learning from ${learned.reports.length} report${learned.reports.length === 1 ? "" : "s"} 🧠`);
      loadDeals();
      loadBasket();
    }
    $("#card-dialog").close();
  };
}
async function loadLearning() {
  renderLearning(await api("/api/learning"));
}
function renderLearning(l) {
  const reports = l.reports.map((r) => `
    <div class="brow">
      ${picture(r.image ? [r.image] : [], "", "bthumb")}
      <div class="bmain"><b>${esc(r.card_name || r.title)}</b><div class="hint">${esc(r.reason)}</div>
        <div class="hint">${gbp(r.total)} · ${esc(r.seller || "?")} · ${ago(r.created_at)}</div></div>
      <div class="bside"><button class="btn small ghost" data-undo="${r.id}">Undo</button></div>
    </div>`).join("");
  $("#learned").innerHTML = l.reports.length ? `
    <p class="hint">${l.reports.length} issue${l.reports.length === 1 ? "" : "s"} reported. The bot now:</p>
    <div class="frow"><span class="flabel">Blocks sellers</span><div class="chips">${l.sellers.map((s) => `<span class="badge bad">${esc(s)}</span>`).join("") || `<span class="hint">none</span>`}</div></div>
    <div class="frow"><span class="flabel">Blocks words</span><div class="chips">${l.words.map((w) => `<button data-ignore="${esc(w)}" title="Stop blocking this word">${esc(w)} ✕</button>`).join("") || `<span class="hint">none yet (needs 2+ reports sharing a word)</span>`}</div></div>
    ${l.ignored_words.length ? `<div class="frow"><span class="flabel">Allowed words</span><div class="chips">${l.ignored_words.map((w) => `<button data-restore="${esc(w)}" title="Block again">${esc(w)} ↺</button>`).join("")}</div></div>` : ""}
    <h3 class="pick-h">Reports</h3>${reports}`
    : `<p class="hint">Nothing yet. Tap 🚩 on any listing with a problem (fake, print, wrong card…) to report an issue, and the bot will learn from it.</p>`;
}
$("#learned").addEventListener("click", async (e) => {
  const undo = e.target.closest("[data-undo]");
  const ignore = e.target.closest("[data-ignore]");
  const restore = e.target.closest("[data-restore]");
  if (undo) renderLearning(await api(`/api/learning/undo/${undo.dataset.undo}`, {}));
  if (ignore) renderLearning(await api("/api/learning/ignore-word", { word: ignore.dataset.ignore }));
  if (restore) renderLearning(await api("/api/learning/ignore-word", { word: restore.dataset.restore, restore: true }));
  if (undo || ignore || restore) toast("Updated");
});

// pick a different picture for a card
window.choosePicture = async (cardId) => {
  if (!state.items.length) state.items = (await api("/api/cards")).items;
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
      // The same picture is used on deals and in the basket: refresh them all.
      renderChecklist();
      loadDeals();
      loadBasket();
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
loadBasket();
setInterval(refresh, 60_000);
