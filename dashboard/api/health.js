export default function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  res.status(200).json({ ok: true, password_set: Boolean(process.env.DASHBOARD_PASSWORD), blob_connected: Boolean(process.env.BLOB_READ_WRITE_TOKEN) });
}
