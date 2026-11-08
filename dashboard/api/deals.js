// The 1031 deals feed for dashboard/public/1031.html. Two doors:
//   x-dashboard-key = DASHBOARD_PASSWORD  → everything (Rodrigo's private view, internal numbers + broker contact)
//   ?k=DEALS_SHARE_KEY                    → the investor view: only the investor columns, no `private` block
// The engine (titan/commercial/site.py) uploads titan/deals.json to this project's Blob store after every pull.
import { list } from "@vercel/blob";

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  const pw = process.env.DASHBOARD_PASSWORD || "";
  const share = process.env.DEALS_SHARE_KEY || "";
  const given = req.headers["x-dashboard-key"] || "";
  const key = (req.query && req.query.k) || "";
  const isPrivate = pw && given === pw;
  const isInvestor = share && key === share;
  if (!isPrivate && !isInvestor) return res.status(401).json({ error: "sign in, or use the investor link" });
  if (!process.env.BLOB_READ_WRITE_TOKEN) return res.status(500).json({ error: "Blob store not connected to this project" });
  try {
    const { blobs } = await list({ prefix: "titan/deals.json", limit: 10 });
    if (!blobs.length) return res.status(404).json({ error: "no deals yet: run `make commercial` on the machine that runs the engine" });
    blobs.sort((a, b) => new Date(b.uploadedAt) - new Date(a.uploadedAt));
    const r = await fetch(`${blobs[0].url}?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) return res.status(502).json({ error: `blob fetch failed: ${r.status}` });
    const data = await r.json();
    if (!isPrivate) {
      const cols = new Set(data.investor_columns || []);
      data.deals = (data.deals || []).filter(d => d.investor && cols.has(d.board_column));
      delete data.private;
      data.view = "investor";
    } else {
      data.view = "private";
    }
    res.setHeader("Content-Type", "application/json");
    return res.status(200).send(JSON.stringify(data));
  } catch (e) {
    return res.status(500).json({ error: String(e && e.message || e).slice(0, 300) });
  }
}
