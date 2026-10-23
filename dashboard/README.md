# Titan dashboard (Vercel)

A password-protected site on your own Vercel account that shows what the engine knows: project progress, the
listings/agents/conversations CRM view, and a live event feed. The engine pushes a JSON snapshot to the project's
Blob store after every run; the site only reads it. No database, no framework, nothing tied to a Claude account.

```
engine (Mac now, server later) ──PUT snapshot.json──▶ Vercel Blob ◀──read── /api/snapshot ◀── index.html (password)
```

## One-time setup (≈10 minutes)

1. **Create the Vercel project.** vercel.com → Add New → Project → import the GitHub repo `titanbuilds1995/Ai-Agent-Leads`.
   - Root Directory: `dashboard`  (click Edit next to Root Directory and type it)
   - Framework Preset: Other. Leave build settings empty. Deploy.
2. **Add a Blob store.** In the project → Storage → Create Database → Blob → name it `titan` → Connect to the project.
   This creates the `BLOB_READ_WRITE_TOKEN` environment variable on the project automatically.
3. **Set the password.** Project → Settings → Environment Variables → add `DASHBOARD_PASSWORD` = a password you choose.
   Then Deployments → ⋯ on the latest → Redeploy (env vars apply on the next deploy).
4. **Give the engine the token.** Storage → Blob → `.env.local` tab → copy the `BLOB_READ_WRITE_TOKEN=...` line and paste it
   into the engine's `.env` on the Mac (and later the server). Run `make doctor`; the "dashboard (Vercel)" row turns green.
5. **First push.** `make sync-dashboard`. Open the Vercel URL, enter the password.

From then on every `run-daily`, `check-replies` and `report` pushes a fresh snapshot; the Live tab refreshes every 30 s.

## Tabs

- **Progress**: the roadmap (`public/progress.json`, edited by hand as milestones move), live connection checks, the
  outreach switch, and the "things to watch" list.
- **Pipeline**: funnel tiles, then Listings (filter by rating/county/status, click for the full card: motivation, cited
  phrases, ARV/repairs/MAO, agent, every message about it), Agents, Conversations, Messages.
- **Live**: event feed (runs, drafts, sends, replies, hot leads, opt-outs), AI cost, recent runs.

## Privacy

The snapshot holds agent names, phones and emails (the CRM). The blob lives at a fixed path inside your private store
(`titan/snapshot.json`, 60 s CDN cache); the site reads it server-side and only after the password check. Do not share
the blob URL itself. Secrets never enter the snapshot (tested).

## Local preview

`npx vercel dev` inside `dashboard/` with `DASHBOARD_PASSWORD` and `BLOB_READ_WRITE_TOKEN` in `dashboard/.env.local`.
