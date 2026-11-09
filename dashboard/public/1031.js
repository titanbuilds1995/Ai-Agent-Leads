// 1031 deals page: a gallery of photo cards per board column, and a page per property (#<listing_id>).
// Private view (dashboard password) shows everything; the investor link (?k=…) shows only the investor columns.
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = n => n == null ? "—" : "$" + Math.round(n).toLocaleString("en-US");
const pct = n => n == null ? "—" : Number(n).toFixed(2) + "%";
const params = new URLSearchParams(location.search);
const shareKey = params.get("k") || "";
let DATA = null, COL = "all";

async function load() {
  const headers = {};
  const pw = sessionStorage.getItem("titan_pw") || "";
  if (pw) headers["x-dashboard-key"] = pw;
  const r = await fetch("/api/deals" + (shareKey ? `?k=${encodeURIComponent(shareKey)}` : ""), { headers, cache: "no-store" });
  if (r.status === 401) { $("#gate").hidden = false; $("#sub").textContent = "sign in"; return; }
  const data = await r.json();
  if (!r.ok) { $("#sub").textContent = data.error || "error"; return; }
  DATA = data;
  $("#gate").hidden = true;
  $("#viewpill").textContent = data.view === "private" ? "private view" : "investor view";
  $("#viewpill").className = "pill " + (data.view === "private" ? "now" : "ok");
  $("#backlink").hidden = data.view !== "private"; $("#logout").hidden = data.view !== "private";
  $("#sub").textContent = `${data.deals.length} deals · updated ${new Date(data.generated_at).toLocaleString()}`;
  renderCols();
  route();
}

function cols() { return DATA.view === "private" ? DATA.columns : DATA.investor_columns; }

function renderCols() {
  const counts = {}; for (const d of DATA.deals) counts[d.board_column] = (counts[d.board_column] || 0) + 1;
  const nav = $("#cols"); nav.innerHTML = "";
  const all = document.createElement("button"); all.textContent = `All (${DATA.deals.length})`; all.className = COL === "all" ? "active" : ""; all.onclick = () => { COL = "all"; location.hash = ""; renderGallery(); renderCols(); };
  nav.appendChild(all);
  for (const c of cols()) {
    const b = document.createElement("button"); b.textContent = `${c} (${counts[c] || 0})`; b.className = COL === c ? "active" : "";
    b.onclick = () => { COL = c; location.hash = ""; renderGallery(); renderCols(); }; nav.appendChild(b);
  }
}

function card(d) {
  const facts = [d.price ? money(d.price) : "price n/a", d.stated_cap ? pct(d.stated_cap) + " cap" : "cap on request",
                 d.building_sf ? d.building_sf.toLocaleString("en-US") + " SF" : "", d.units > 1 ? d.units + " units" : "", d.year_built ? "built " + d.year_built : ""].filter(Boolean);
  return `<article class="deal" data-id="${esc(d.listing_id)}">
    <div class="ph" style="${d.photo ? `background-image:url('${esc(d.photo)}')` : ""}">${d.photo ? "" : '<div class="nop">no photo</div>'}
      <span class="pill col">${esc(d.board_column)}</span>${DATA.view === "private" ? `<span class="grade" title="evidence grade">${esc(d.evidence_grade || "?")}</span>` : ""}</div>
    <div class="b"><h3>${esc(d.street)}</h3><div class="where">${esc(d.city)} · ${esc(d.county || "?")} County · ${esc((d.asset_class || d.property_type || "").toUpperCase())}</div>
      <div class="facts">${facts.map(esc).join(" · ")}</div><div class="why">${esc(d.why)}</div>
      <div class="foot"><span>${esc(d.region || "")}</span><span>${DATA.view === "private" ? esc(d.tag || "") : ""}</span></div></div></article>`;
}

function renderGallery() {
  $("#detail").hidden = true; $("#gallery").hidden = false;
  const bb = DATA.buyer_box || {};
  let deals = DATA.deals.filter(d => COL === "all" || d.board_column === COL);
  const sortBy = $("#sort") ? $("#sort").value : "score";
  const asset = $("#asset") ? $("#asset").value : "";
  if (asset) deals = deals.filter(d => d.asset_class === asset);
  const priv = DATA.private || {};
  if (sortBy === "score") deals.sort((a, b) => ((priv[b.listing_id] || {}).score || 0) - ((priv[a.listing_id] || {}).score || 0) || (b.stated_cap || 0) - (a.stated_cap || 0));
  if (sortBy === "cap") deals.sort((a, b) => (b.stated_cap || 0) - (a.stated_cap || 0));
  if (sortBy === "price") deals.sort((a, b) => (a.price || 9e9) - (b.price || 9e9));
  const assets = [...new Set(DATA.deals.map(d => d.asset_class).filter(Boolean))].sort();
  $("#gallery").innerHTML = `<div class="deals-head"><div><div class="eyebrow">${esc(DATA.view === "private" ? "Private · every column" : "For the 1031 buyer")}</div>
      <h2>${esc(COL === "all" ? "Commercial deals in Florida" : COL)}</h2>
      <p class="muted">${money(bb.price_min)} to ${money(bb.price_max)} · stated cap ${bb.cap_min}% and up · existing, cash-flowing property · all of Florida. Click a card for the property.</p></div>
      <div class="filters"><select id="asset"><option value="">every asset type</option>${assets.map(a => `<option ${a === asset ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>
      <select id="sort"><option value="score" ${sortBy === "score" ? "selected" : ""}>best first</option><option value="cap" ${sortBy === "cap" ? "selected" : ""}>highest cap</option><option value="price" ${sortBy === "price" ? "selected" : ""}>lowest price</option></select></div></div>
    ${deals.length ? `<div class="deals">${deals.map(card).join("")}</div>` : `<div class="empty">Nothing in this column yet.</div>`}`;
  $("#asset").onchange = renderGallery; $("#sort").onchange = renderGallery;
  for (const el of document.querySelectorAll(".deal")) el.onclick = () => { location.hash = el.dataset.id; };
}

function kv(k, v) { return `<div><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`; }

function renderDetail(id) {
  const d = DATA.deals.find(x => x.listing_id === id);
  if (!d) { renderGallery(); return; }
  const p = (DATA.private || {})[id] || null;
  const photos = (d.photos && d.photos.length ? d.photos : (d.photo ? [d.photo] : []));
  $("#gallery").hidden = true; $("#detail").hidden = false;
  $("#detail").innerHTML = `<button class="back" id="back">← all deals</button>
    <div class="hero"><div><div class="big" id="big" style="${photos[0] ? `background-image:url('${esc(photos[0])}')` : ""}"></div>
      ${photos.length > 1 ? `<div class="thumbs">${photos.slice(0, 12).map(u => `<div style="background-image:url('${esc(u)}')" data-u="${esc(u)}"></div>`).join("")}</div>` : ""}</div>
      <div><span class="pill">${esc(d.board_column)}</span> ${p ? `<span class="tag ${esc(d.tag || "")}">${esc(d.tag || "")}</span> <span class="tag">grade ${esc(d.evidence_grade || "?")}</span>` : ""}
        <h2 style="margin-top:10px">${esc(d.street)}</h2><div class="where">${esc(d.city)}, FL ${esc(d.zip || "")} · ${esc(d.county || "?")} County · ${esc(d.region || "")}</div>
        <div class="kv">${kv("Asking", d.price ? money(d.price) : (d.price_text || "not disclosed"))}${kv("Stated cap", d.stated_cap ? pct(d.stated_cap) : "on request")}
          ${kv("Type", [d.property_type, d.property_subtype].filter(Boolean).join(" / ") || d.asset_class || "")}${kv("Building", d.building_sf ? d.building_sf.toLocaleString("en-US") + " SF" : "—")}
          ${kv("Year built", d.year_built || "—")}${kv("Units / tenancy", d.units > 1 ? d.units + " units" : (d.tenancy || "—"))}
          ${kv("Lease", (d.lease_type || "—").toUpperCase() + (d.remaining_term_years ? ` · ${d.remaining_term_years} yrs left` : ""))}${kv("Occupancy", d.occupancy ? d.occupancy + "%" : "—")}
          ${d.stated_noi ? kv("Stated NOI", money(d.stated_noi)) : ""}${d.days_on_market ? kv("On market", d.days_on_market + " days") : ""}</div>
        <div class="whybox"><div class="eyebrow" style="color:var(--gold-ink)">Why this fits</div>${esc(d.why)}</div>
        <p style="margin-top:12px"><a href="${esc(d.listing_url)}" target="_blank" rel="noopener">View on LoopNet ↗</a></p></div></div>
    <div class="blocks">
      ${d.highlights && d.highlights.length ? `<div class="block"><h3>Highlights</h3><ul>${d.highlights.map(h => `<li>${esc(h)}</li>`).join("")}</ul></div>` : ""}
      ${d.description ? `<div class="block"><h3>From the listing</h3><p>${esc(d.description)}</p></div>` : ""}
      ${p ? `<div class="block internal"><h3>Internal (not for the investor)</h3>
          <div class="kv">${kv("Score", p.score ?? "—")}${kv("Offer anchor", p.offer_anchor ? money(p.offer_anchor) : "—")}${kv("Back-solved cap", p.back_solved_cap ? pct(p.back_solved_cap) : "—")}${kv("Tier", p.tier || "—")}</div>
          <p class="muted">${esc(p.offer_anchor_basis || "")}</p>
          <p><b>${esc(d.tag || "")}</b>: ${esc((p.tag_reasons || []).join("; "))}</p><p>Evidence grade ${esc(d.evidence_grade || "?")}: ${esc(p.evidence_note || "")}</p>
          <p>Score parts: ${esc(Object.entries(p.parts || {}).map(([k, v]) => `${k} ${v}`).join(" · "))}</p>
          ${p.docs_missing && p.docs_missing.length ? `<p>Docs missing: ${esc(p.docs_missing.join(", "))}</p>` : ""}</div>
        <div class="block internal"><h3>Broker</h3><p><b>${esc(p.broker_name || "?")}</b> · ${esc(p.brokerage || "")} (${esc(p.brokerage_size || "")})</p>
          <p>${p.broker_phone ? `<a href="tel:${esc(p.broker_phone)}">${esc(p.broker_phone_pretty || p.broker_phone)}</a>` : "no phone"} · ${p.broker_email ? `<a href="mailto:${esc(p.broker_email)}">${esc(p.broker_email)}</a>` : "no email"}</p>
          ${p.om_request ? `<p><b>Books requested</b> ${esc((p.om_request.sent_at || "").slice(0, 10))}${p.om_request.bounced ? " · bounced" : p.om_request.hours_to_reply != null ? ` · answered in ${esc(p.om_request.hours_to_reply)} h` : " · no answer yet"}${p.om_request.state === "BOOKS" ? " · books received" : ""}</p>` : ""}
          ${p.asana_task_id || p.asana_om_task_id ? `<p><a href="https://app.asana.com/0/0/${esc(p.asana_task_id || p.asana_om_task_id)}/f" target="_blank" rel="noopener">Asana card ↗</a></p>` : ""}</div>` : ""}
    </div>`;
  $("#back").onclick = () => { location.hash = ""; };
  for (const t of document.querySelectorAll(".thumbs div")) t.onclick = () => { $("#big").style.backgroundImage = `url('${t.dataset.u}')`; };
  window.scrollTo(0, 0);
}

function route() {
  if (!DATA) return;
  const id = location.hash.replace("#", "");
  if (id) renderDetail(id); else renderGallery();
}
window.addEventListener("hashchange", route);
$("#loginform").onsubmit = async ev => {
  ev.preventDefault(); sessionStorage.setItem("titan_pw", $("#pw").value); $("#loginerr").hidden = true;
  await load(); if (!$("#gate").hidden) { $("#loginerr").textContent = "wrong password"; $("#loginerr").hidden = false; }
};
$("#logout").onclick = () => { sessionStorage.removeItem("titan_pw"); location.reload(); };
load();
