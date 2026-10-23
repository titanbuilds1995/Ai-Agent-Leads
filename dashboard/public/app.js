/* Titan dashboard. Reads /api/snapshot (password header) and renders three tabs. No framework. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const h = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => (n == null ? "—" : "$" + Math.round(n).toLocaleString());
  const pct = (n) => (n == null ? "—" : Math.round(n) + "%");
  const ago = (iso) => { if (!iso) return "—"; const s = (Date.now() - new Date(iso)) / 1000; if (s < 90) return "just now"; if (s < 3600) return Math.round(s / 60) + " min ago"; if (s < 86400) return Math.round(s / 3600) + " h ago"; return Math.round(s / 86400) + " d ago"; };
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");
  const tag = (v) => `<span class="tag ${h(v)}">${h(v || "—")}</span>`;

  let snap = null, progress = null, tab = "progress", sub = "listings", timer = null, filters = { q: "", rating: "", county: "", lead: "" };
  const key = () => { try { return localStorage.getItem("titan_key") || ""; } catch { return ""; } };

  let local = null;
  async function load() {
    if (local === null) { try { local = !!(await (await fetch("/api/health", { cache: "no-store" })).json()).local; } catch { local = false; } }
    const k = local ? "local" : key();
    if (!k) return showGate();
    $("#sub").textContent = "loading…";
    try {
      const r = await fetch("/api/snapshot", { headers: { "x-dashboard-key": k }, cache: "no-store" });
      if (r.status === 401) { try { localStorage.removeItem("titan_key"); } catch {} return showGate("Wrong password."); }
      const body = await r.json();
      if (!r.ok) { snap = null; return render(body.error || `error ${r.status}`); }
      snap = body;
      if (!progress) progress = await (await fetch("/progress.json", { cache: "no-store" })).json().catch(() => null);
      render();
    } catch (e) { render("Could not reach the dashboard API: " + e.message); }
  }

  function showGate(msg) {
    $("#gate").hidden = false; document.querySelectorAll(".tab").forEach((t) => (t.hidden = true));
    $("#sub").textContent = "sign in"; const e = $("#loginerr"); e.hidden = !msg; e.textContent = msg || "";
  }

  function render(err) {
    $("#gate").hidden = true;
    document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + tab));
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    if (err) {
      $("#sub").textContent = "no data";
      $("#tab-" + tab).innerHTML = `<div class="empty"><h2>Nothing to show yet</h2><p>${h(err)}</p><p class="muted">The engine uploads a snapshot after every run. On the machine that runs it: <code>make sync-dashboard</code>.</p></div>`;
      return;
    }
    $("#sub").textContent = `updated ${ago(snap.generated_at)} · ${snap.properties_total} listings · ${snap.funnel.agents} agents`;
    const mp = $("#modepill"); mp.textContent = snap.mode.replace(/_/g, " ").toLowerCase(); mp.className = "pill " + (snap.mode === "DRAFT_FOR_APPROVAL" ? "ok" : snap.mode === "FULL_AUTO" ? "risk" : "now");
    ({ progress: renderProgress, pipeline: renderPipeline, live: renderLive })[tab]();
  }

  /* ---------------- Progress ---------------- */
  function renderProgress() {
    const el = $("#tab-progress"); const p = progress || { milestones: [], modes: [], watch: [] };
    const conns = snap.connections || [];
    const connByName = Object.fromEntries(conns.map((c) => [c.check, c]));
    const stops = p.milestones.map((m) => {
      let state = m.state;
      if (m.connection && connByName[m.connection]?.status === "ok" && state !== "done") state = state === "now" ? "now" : state;
      return `<div class="stop ${state}"><span class="k">${m.n} · ${h(m.when)}</span><h3>${h(m.title)}</h3><ul>${m.lines.map((l) => `<li>${h(l)}</li>`).join("")}</ul>${m.connection ? `<div style="margin-top:6px">${connPill(connByName[m.connection])}</div>` : ""}</div>`;
    }).join("");
    const modes = p.modes.map((m) => `<div class="stop ${m.key === snap.mode ? "now" : "off"}"><span class="k">${m.key === snap.mode ? "current" : " "}</span><h3>${h(m.label)}</h3><ul><li>${h(m.note)}</li></ul></div>`).join("");
    const f = snap.funnel;
    el.innerHTML = `
      <div><h2>Where the project is</h2><p class="muted">The road from "built" to "runs every morning". The gold-ruled card is the stop we're on.</p></div>
      <div class="road">${stops}</div>
      <div><h2>Connections</h2><p class="muted">Live from the last check the engine ran${conns.length ? " · " + ago(snap.generated_at) : ""}.</p></div>
      ${conns.length ? `<div class="conns">${conns.map(connRow).join("")}</div>` : `<div class="empty">No connection check in this snapshot yet. It runs with every daily run.</div>`}
      <div><h2>What the engine has so far</h2></div>
      <div class="tiles">${tile(f.properties, "listings")}${tile(f.scored, "scored")}${tile(f.HOT, "hot", "hot")}${tile(f.WARM, "warm", "warm")}${tile(f.COLD, "cold", "cold")}${tile(f.agents_with_email, "agents w/ email")}${tile(f.emails_drafted + f.sms_drafted, "drafts waiting")}${tile(f.emails_sent + f.sms_sent, "messages sent")}${tile(f.replies, "replies")}${tile(f.hot_leads, "hot leads")}</div>
      <div><h2>The switch</h2><p class="muted">Set in config.yaml → outreach.mode on the machine that runs the engine.</p></div>
      <div class="grid g3">${modes}</div>
      <div><h2>Things to watch</h2></div>
      <div class="watch">${p.watch.map((w) => `<div class="w ${w.level}"><span class="when">${h(w.when)}</span><div><b>${h(w.title)}</b><div class="muted">${h(w.text)}</div></div></div>`).join("")}</div>`;
  }
  const tile = (n, l, cls = "") => `<div class="tile ${cls}"><div class="n">${n ?? 0}</div><div class="l">${h(l)}</div></div>`;
  const connPill = (c) => c ? `<span class="pill ${c.status === "ok" ? "ok" : c.status === "fail" ? "risk" : c.status === "warn" ? "" : "off"}">${h(c.check)}: ${h(c.status)}</span>` : "";
  const connRow = (c) => `<div class="conn ${h(c.status)}"><span class="dot"></span><div><b>${h(c.check)}</b> <span class="muted">${h(c.detail)}</span>${c.fix ? `<div class="fix">→ ${h(c.fix)}</div>` : ""}</div></div>`;

  /* ---------------- Pipeline (CRM) ---------------- */
  function renderPipeline() {
    const el = $("#tab-pipeline"); const f = snap.funnel;
    const counties = [...new Set(snap.properties.map((p) => p.county).filter(Boolean))].sort();
    el.innerHTML = `
      <div class="tiles">${tile(f.properties, "listings")}${tile(f.HOT, "hot", "hot")}${tile(f.WARM, "warm", "warm")}${tile(f.agents, "agents")}${tile(f.agents_with_email, "with email")}${tile(f.emails_drafted + f.sms_drafted, "drafts")}${tile(f.emails_sent + f.sms_sent, "sent")}${tile(f.replies, "replies")}${tile(f.hot_leads, "hot leads")}${tile(f.do_not_contact, "opted out")}</div>
      <div class="subtabs" id="subtabs">${["listings", "agents", "conversations", "messages"].map((s) => `<button data-sub="${s}" class="${s === sub ? "active" : ""}">${s}</button>`).join("")}</div>
      <div id="subbody"></div>`;
    el.querySelectorAll("#subtabs button").forEach((b) => (b.onclick = () => { sub = b.dataset.sub; renderPipeline(); }));
    const body = $("#subbody");
    if (sub === "listings") {
      body.innerHTML = `
        <div class="grid" style="gap:10px">
        <div class="toolbar">
          <input id="q" placeholder="search street, city, agent, remarks" value="${h(filters.q)}">
          <select id="frating"><option value="">all ratings</option>${["HOT", "WARM", "COLD"].map((r) => `<option ${filters.rating === r ? "selected" : ""}>${r}</option>`).join("")}</select>
          <select id="fcounty"><option value="">all counties</option>${counties.map((c) => `<option ${filters.county === c ? "selected" : ""}>${h(c)}</option>`).join("")}</select>
          <select id="flead"><option value="">any status</option>${["HOT_LEAD", "NEEDS_CALL", "NONE", "CLOSED", "DNC"].map((c) => `<option ${filters.lead === c ? "selected" : ""}>${c}</option>`).join("")}</select>
        </div>
        <div class="tablewrap" id="ltable"></div></div>`;
      const apply = () => { filters = { q: $("#q").value.toLowerCase(), rating: $("#frating").value, county: $("#fcounty").value, lead: $("#flead").value }; listingsTable(); };
      ["#q", "#frating", "#fcounty", "#flead"].forEach((s) => ($(s).oninput = apply));
      listingsTable();
    } else if (sub === "agents") {
      const rows = snap.agents.map((a) => `<tr class="row" data-agent="${a.id}"><td>${h(a.name)}${a.is_office_line ? ' <span class="tag">office line</span>' : ""}${a.dnc ? ' <span class="tag DNC">opted out</span>' : ""}</td><td>${h(a.brokerage || "")}</td><td class="mono">${h(a.phone || "")}</td><td class="mono">${h(a.email || "")}${a.email ? ` <span class="tag">${h(a.email_confidence || "")}</span>` : ""}</td><td class="num">${a.listings}</td><td class="num">${a.touches}</td><td>${a.last_reply_at ? fmt(a.last_reply_at) : "—"}</td></tr>`).join("");
      body.innerHTML = `<div class="tablewrap"><table><thead><tr><th>agent</th><th>brokerage</th><th>phone</th><th>email</th><th>listings</th><th>touches</th><th>last reply</th></tr></thead><tbody>${rows || `<tr><td colspan="7" class="muted">no agents yet</td></tr>`}</tbody></table></div>`;
      body.querySelectorAll("tr.row").forEach((tr) => (tr.onclick = () => openAgent(+tr.dataset.agent)));
    } else if (sub === "conversations") {
      const th = snap.threads;
      body.innerHTML = th.length ? `<div class="grid g2">${th.map(threadCard).join("")}</div>` : `<div class="empty">No conversations yet. They appear here when an agent replies.</div>`;
    } else {
      const rows = snap.outreach.map((o) => `<tr class="row" data-out="${o.id}"><td>${fmt(o.sent_at || o.created_at)}</td><td>${tag(o.channel)}</td><td>${tag(o.status)}</td><td>${h(o.agent?.name || o.to_address)}</td><td class="wrap">${h(o.properties.map((p) => p.street).join(", "))}</td><td class="wrap">${h(o.subject || (o.body || "").slice(0, 90))}</td></tr>`).join("");
      body.innerHTML = `<div class="tablewrap"><table><thead><tr><th>when</th><th>channel</th><th>status</th><th>to</th><th>about</th><th>subject / text</th></tr></thead><tbody>${rows || `<tr><td colspan="6" class="muted">nothing written yet</td></tr>`}</tbody></table></div>`;
      body.querySelectorAll("tr.row").forEach((tr) => (tr.onclick = () => openOutreach(+tr.dataset.out)));
    }
  }

  function listingsTable() {
    const q = filters.q;
    const rows = snap.properties.filter((p) =>
      (!filters.rating || p.rating === filters.rating) && (!filters.county || p.county === filters.county) && (!filters.lead || (p.lead_status || "NONE") === filters.lead) &&
      (!q || [p.street, p.city, p.agent?.name, p.remarks, p.summary].join(" ").toLowerCase().includes(q)));
    $("#ltable").innerHTML = `<table><thead><tr><th>rating</th><th>score</th><th>address</th><th>list</th><th>MAO</th><th>gap</th><th>why</th><th>agent</th><th>status</th><th>seen</th></tr></thead><tbody>
      ${rows.map((p) => `<tr class="row" data-id="${p.id}"><td>${tag(p.rating || "—")}</td><td><div class="bar" title="${p.score ?? ""}"><i style="width:${p.score ?? 0}%"></i></div><span class="mono muted" style="font-size:11px">${p.score ?? "—"}</span></td>
        <td>${h(p.street)}<br><span class="muted">${h(p.city)} · ${h(p.county || "")}</span></td><td class="num">${money(p.list_price)}${p.price_cuts ? `<br><span class="tag">${p.price_cuts} cut${p.price_cuts > 1 ? "s" : ""}</span>` : ""}</td><td class="num">${money(p.mao)}</td><td class="num">${pct(p.spread_pct)}</td>
        <td class="wrap">${h((p.cited || []).slice(0, 2).map((c) => typeof c === "string" ? c : c.phrase || c.text || "").join(" · ") || p.summary || "")}</td><td>${h(p.agent?.name || "—")}${p.agent?.is_office_line ? ' <span class="tag">office</span>' : ""}</td><td>${tag(p.lead_status === "NONE" ? "" : p.lead_status)}</td><td class="muted">${ago(p.last_seen)}</td></tr>`).join("") || `<tr><td colspan="10" class="muted">no listings match</td></tr>`}
      </tbody></table>`;
    $("#ltable").querySelectorAll("tr.row").forEach((tr) => (tr.onclick = () => openProperty(+tr.dataset.id)));
  }

  const threadCard = (t) => `<div class="card"><div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><b>${h(t.agent?.name || "?")}</b><span>${tag(t.channel)} ${tag(t.state)}</span></div>
    <div class="muted">${h(t.properties.map((p) => p.street).join(", "))}</div><div class="thread">${t.messages.map((m) => `<div class="msg ${m.direction}"><div>${h(m.body)}</div><div class="muted" style="font-size:11px;margin-top:4px">${fmt(m.created_at)}${m.classification ? " · " + h(m.classification) : ""}</div></div>`).join("")}</div></div>`;

  /* ---------------- Live ---------------- */
  function renderLive() {
    const el = $("#tab-live"); const u = snap.usage;
    const runs = snap.runs.slice(0, 12).map((r) => `<tr><td>${fmt(r.finished_at || r.started_at)}</td><td>${h(r.step)}</td><td>${tag(r.status)}</td><td class="wrap mono" style="font-size:12px">${h(summ(r))}</td></tr>`).join("");
    el.innerHTML = `
      <div class="livebar"><span class="blink"></span><b>Live feed</b><span class="muted">refreshes every 30 s · snapshot ${ago(snap.generated_at)}</span><label class="muted" style="margin-left:auto"><input type="checkbox" id="auto" ${timer ? "checked" : ""}> auto-refresh</label></div>
      <div class="tiles">${tile(money(u.last_24h.cost_usd), "AI cost 24h")}${tile(u.last_24h.calls, "AI calls 24h")}${tile(money(u.all_time.cost_usd), "AI cost total")}${tile(snap.funnel.today_new ?? 0, "new today")}${tile(snap.funnel.today_emails ?? 0, "emails today")}${tile(snap.funnel.today_sms ?? 0, "texts today")}</div>
      <div class="feed">${snap.events.length ? snap.events.map((e) => `<div class="ev ${h(e.type)}"><span class="ts">${fmt(e.ts)}</span><span>${tag(e.status)}</span><div><b>${h(e.title)}</b>${e.detail ? `<div class="d">${h(e.detail)}</div>` : ""}</div></div>`).join("") : `<div class="empty">Nothing has happened yet.</div>`}</div>
      <div><h2>Recent runs</h2></div>
      <div class="tablewrap"><table><thead><tr><th>finished</th><th>step</th><th>status</th><th>counts</th></tr></thead><tbody>${runs || `<tr><td colspan="4" class="muted">no runs yet</td></tr>`}</tbody></table></div>`;
    $("#auto").onchange = (e) => setAuto(e.target.checked);
  }
  const summ = (r) => Object.entries(r.counts || {}).filter(([k, v]) => typeof v !== "object").map(([k, v]) => `${k}=${v}`).join("  ");
  function setAuto(on) { clearInterval(timer); timer = on ? setInterval(load, 30000) : null; }

  /* ---------------- Drawer ---------------- */
  function drawer(html) { $("#drawerbody").innerHTML = html; $("#drawer").hidden = false; }
  function openProperty(id) {
    const p = snap.properties.find((x) => x.id === id); if (!p) return;
    const touches = snap.outreach.filter((o) => o.property_ids.includes(id));
    const threads = snap.threads.filter((t) => t.property_ids.includes(id));
    const cited = (p.cited || []).map((c) => typeof c === "string" ? c : c.phrase || c.text || JSON.stringify(c));
    drawer(`
      <div><span>${tag(p.rating)} ${tag(p.lead_status === "NONE" ? "" : p.lead_status)}</span><h2 style="margin-top:6px">${h(p.street)}</h2><div class="muted">${h(p.city)}, ${h(p.county || "")} ${h(p.zip || "")} · ${h(p.property_type || "")} · ${p.beds ?? "—"} bd / ${p.baths ?? "—"} ba / ${p.sqft ? p.sqft.toLocaleString() + " sqft" : "—"}</div>${p.listing_url ? `<a href="${h(p.listing_url)}" target="_blank" rel="noopener">open listing ↗</a>` : ""}</div>
      <div class="kv"><b>Motivation</b><span>${p.score ?? "—"} / 100 <span class="muted">(${h(p.method || "")})</span></span><b>Why</b><span>${cited.map((c) => `“${h(c)}”`).join("<br>") || h(p.summary || "—")}</span>
        <b>List price</b><span>${money(p.list_price)}${p.original_list_price && p.original_list_price !== p.list_price ? ` <span class="muted">was ${money(p.original_list_price)}</span>` : ""}</span><b>Days on market</b><span>${p.days_on_market ?? "—"}</span>
        <b>Worth fixed up</b><span>${money(p.arv)} <span class="muted">${h(p.arv_method || "")}${p.comp_count ? `, ${p.comp_count} comps` : ""}</span></span><b>Repairs</b><span>${money(p.repair_cost)} <span class="muted">${h(p.repair_tier || "")}</span></span>
        <b>Max offer (MAO)</b><span>${money(p.mao)}</span><b>First offer</b><span>${money(p.offer_price)}</span><b>List above MAO</b><span>${pct(p.spread_pct)}</span>
        <b>Agent</b><span>${p.agent ? `${h(p.agent.name)} · ${h(p.agent.brokerage || "")}<br><span class="mono">${h(p.agent.phone || "")} ${h(p.agent.email || "")}</span>${p.agent.is_office_line ? '<br><span class="tag">office line, not texted</span>' : ""}` : "—"}</span>
        <b>Status</b><span>${h(p.lead_status)}${p.lead_note ? ` · ${h(p.lead_note)}` : ""}</span></div>
      <div><h3>Remarks</h3><div class="quote">${h(p.remarks || "(none)")}</div></div>
      <div><h3>Messages about this listing (${touches.length})</h3>${touches.map((o) => `<div class="card" style="margin-top:8px"><div>${tag(o.channel)} ${tag(o.status)} <span class="muted">${fmt(o.sent_at || o.created_at)} → ${h(o.agent?.name || o.to_address)}</span></div>${o.subject ? `<b>${h(o.subject)}</b>` : ""}<div class="quote">${h(o.body)}</div>${o.error ? `<div class="err">${h(o.error)}</div>` : ""}</div>`).join("") || '<div class="muted">none yet</div>'}</div>
      ${threads.length ? `<div><h3>Conversation</h3>${threads.map(threadCard).join("")}</div>` : ""}`);
  }
  function openAgent(id) {
    const a = snap.agents.find((x) => x.id === id); if (!a) return;
    const props = snap.properties.filter((p) => p.agent?.id === id); const touches = snap.outreach.filter((o) => o.agent_id === id); const threads = snap.threads.filter((t) => t.agent_id === id);
    drawer(`<div><h2>${h(a.name)}</h2><div class="muted">${h(a.brokerage || "")}</div><div class="mono">${h(a.phone || "")} ${h(a.email || "")}</div>${a.is_office_line ? '<span class="tag">office line</span>' : ""} ${a.dnc ? '<span class="tag DNC">opted out</span>' : ""}</div>
      <div><h3>Listings (${props.length})</h3>${props.map((p) => `<div>${tag(p.rating)} ${h(p.street)}, ${h(p.city)} · ${money(p.list_price)} · MAO ${money(p.mao)}</div>`).join("") || '<div class="muted">none in the snapshot</div>'}</div>
      <div><h3>Messages (${touches.length})</h3>${touches.map((o) => `<div class="card" style="margin-top:8px"><div>${tag(o.channel)} ${tag(o.status)} <span class="muted">${fmt(o.sent_at || o.created_at)}</span></div><div class="quote">${h(o.body)}</div></div>`).join("") || '<div class="muted">none yet</div>'}</div>
      ${threads.length ? `<div><h3>Conversations</h3>${threads.map(threadCard).join("")}</div>` : ""}`);
  }
  function openOutreach(id) { const o = snap.outreach.find((x) => x.id === id); if (!o) return; drawer(`<div>${tag(o.channel)} ${tag(o.status)}<h2 style="margin-top:6px">${h(o.subject || "Text message")}</h2><div class="muted">${fmt(o.sent_at || o.created_at)} → ${h(o.agent?.name || "")} <span class="mono">${h(o.to_address || "")}</span></div></div><div class="quote">${h(o.body)}</div>${o.error ? `<div class="err">${h(o.error)}</div>` : ""}<div class="muted">About: ${h(o.properties.map((p) => p.street + ", " + p.city).join(" · "))}</div>`); }

  /* ---------------- wiring ---------------- */
  $("#tabs").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; tab = b.dataset.tab; try { localStorage.setItem("titan_tab", tab); } catch {} if (snap) render(); };
  $("#refresh").onclick = load;
  $("#logout").onclick = () => { try { localStorage.removeItem("titan_key"); } catch {} snap = null; showGate(); };
  $("#drawerclose").onclick = () => ($("#drawer").hidden = true);
  $("#drawer").onclick = (e) => { if (e.target.id === "drawer") $("#drawer").hidden = true; };
  $("#loginform").onsubmit = (e) => { e.preventDefault(); try { localStorage.setItem("titan_key", $("#pw").value); } catch {} load(); };
  try { tab = localStorage.getItem("titan_tab") || "progress"; } catch {}
  setAuto(true);
  load();
})();
