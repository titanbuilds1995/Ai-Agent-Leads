// Serves the latest engine snapshot to the dashboard, behind the DASHBOARD_PASSWORD env var.
// The engine (titan/dashboard_sync.py) uploads titan/snapshot.json to this project's Blob store after every run.
import { list } from "@vercel/blob";

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  const expected = process.env.DASHBOARD_PASSWORD || "";
  const given = req.headers["x-dashboard-key"] || "";
  if (!expected) return res.status(500).json({ error: "DASHBOARD_PASSWORD is not set on the Vercel project" });
  if (given !== expected) return res.status(401).json({ error: "wrong password" });
  if (!process.env.BLOB_READ_WRITE_TOKEN) return res.status(500).json({ error: "Blob store not connected to this project" });
  try {
    const { blobs } = await list({ prefix: "titan/snapshot.json", limit: 10 });
    if (!blobs.length) return res.status(404).json({ error: "no snapshot yet: run `make sync-dashboard` on the machine that runs the engine" });
    blobs.sort((a, b) => new Date(b.uploadedAt) - new Date(a.uploadedAt));
    const r = await fetch(`${blobs[0].url}?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) return res.status(502).json({ error: `blob fetch failed: ${r.status}` });
    res.setHeader("Content-Type", "application/json");
    return res.status(200).send(await r.text());
  } catch (e) {
    return res.status(500).json({ error: String(e && e.message || e).slice(0, 300) });
  }
}
