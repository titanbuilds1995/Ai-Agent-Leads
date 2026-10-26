// Text-message opt-in records. The public form at /sms-consent POSTs here; each submission becomes one JSON file in the
// project's Blob store under titan/consents/ (append-only, random suffix, never overwritten). The engine on the Mac pulls
// them with `titan.cli consents` (also inside every check-replies) and only texts agents with a record. No password:
// agents reach this page from a link in Charles's email. Nothing here reads the CRM snapshot.
import { put } from "@vercel/blob";

const MAX_BODY = 8 * 1024;

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });
  if (!process.env.BLOB_READ_WRITE_TOKEN) return res.status(500).json({ error: "Blob store not connected to this project" });
  let b = req.body;
  if (typeof b === "string") { try { b = JSON.parse(b); } catch { b = null; } }
  if (!b || typeof b !== "object") return res.status(400).json({ error: "bad request" });
  if (JSON.stringify(b).length > MAX_BODY) return res.status(413).json({ error: "too large" });
  if (b.website) return res.status(200).json({ ok: true });            // honeypot filled → a bot; pretend success, store nothing
  const str = (v, n) => String(v ?? "").trim().slice(0, n);
  const digits = str(b.phone, 20).replace(/\D/g, "").replace(/^1(\d{10})$/, "$1");
  if (digits.length !== 10 || "01".includes(digits[0])) return res.status(400).json({ error: "Please enter a 10-digit US mobile number." });
  const name = str(b.name, 120);
  if (!name) return res.status(400).json({ error: "Please enter your name." });
  if (b.consent !== true) return res.status(400).json({ error: "Please tick the consent box." });
  const now = new Date();
  const id = `${now.toISOString().replace(/[:.]/g, "-")}-${Math.random().toString(36).slice(2, 8)}`;
  const record = {
    id, action: "opt_in", created_at: now.toISOString(),
    name, phone: `+1${digits}`, email: str(b.email, 200).toLowerCase(), property: str(b.property, 200),
    agent_id: str(b.agent, 20), ref: str(b.ref, 40),
    consent_text: str(b.consent_text, 1200), page: str(b.page, 300), tz: str(b.tz, 60),
    ip: String(req.headers["x-forwarded-for"] || req.socket?.remoteAddress || "").split(",")[0].trim(),
    user_agent: str(req.headers["user-agent"], 300),
  };
  try {
    await put(`titan/consents/${id}.json`, JSON.stringify(record), { access: "public", addRandomSuffix: true, contentType: "application/json" });
    return res.status(200).json({ ok: true, id });
  } catch (e) {
    return res.status(500).json({ error: "could not save: " + String(e && e.message || e).slice(0, 200) });
  }
}
