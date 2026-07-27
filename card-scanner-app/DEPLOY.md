# Deploying CardVault to trycardvault.com

The app is a single FastAPI service (serves the API and the frontend) with a
SQLite database. It needs one host with HTTPS and a **persistent disk** —
SQLite on an ephemeral filesystem loses every user on restart.

The steps below use Render (simplest path). Railway and Fly.io work the same
way; the invariants are at the bottom.

## 1. Create the service on Render

**Fastest path — Blueprint (recommended).** `card-scanner-app/render.yaml`
already describes the whole service: Docker runtime, the `card-scanner-app`
root directory, a Starter instance, the 1 GB disk mounted at `/data`, and
every environment variable. Render Dashboard → **New → Blueprint** → pick this
repo, and it prompts for the secrets (they are never stored in the repo).
Use this to recreate the service after a deletion — it restores the exact
configuration instead of retyping it. Then skip to step 2.

<details>
<summary>Manual setup (equivalent, if you prefer clicking)</summary>

1. render.com → New → **Web Service** → connect the GitHub repo.
2. Settings:
   - **Root Directory**: `card-scanner-app`
   - **Runtime**: Docker (it will pick up `card-scanner-app/Dockerfile`)
   - Instance type: Starter (the free tier has no persistent disk and sleeps)
3. Add a **Disk**: mount path `/data`, 1 GB is plenty to start.
4. Environment variables:

   | Key | Value |
   |---|---|
   | `CARDVAULT_DB` | `/data/cardvault.db` |
   | `CARDVAULT_BASE_URL` | `https://trycardvault.com` |
   | `LEMONSQUEEZY_API_KEY` | from Lemon Squeezy → Settings → API |
   | `LEMONSQUEEZY_STORE_ID` | numeric store ID |
   | `LEMONSQUEEZY_VARIANT_ID` | variant ID of the Pro subscription |
   | `LEMONSQUEEZY_LIFETIME_VARIANT_ID` | variant ID of the lifetime product (optional) |
   | `LEMONSQUEEZY_WEBHOOK_SECRET` | the signing secret you chose |

   Do **not** set `CARDVAULT_DEV` in production — it exposes a free-upgrade
   endpoint meant for local testing only.

5. Deploy, and confirm the `*.onrender.com` URL serves the app.

</details>

While the custom domain is still pending, set `CARDVAULT_BASE_URL` to the
`*.onrender.com` URL you are actually browsing — if it points at a different
host, checkout redirects land somewhere the buyer is not logged in and the
upgrade looks broken.

## 2. Point the domain

1. Render → your service → Settings → Custom Domains → add
   `trycardvault.com` and `www.trycardvault.com`.
2. Render shows the DNS records to create at your registrar:
   - apex `trycardvault.com` → A/ALIAS record Render specifies
   - `www` → CNAME Render specifies
3. Wait for DNS + the automatic TLS certificate (minutes to a few hours).
4. Set `www` to redirect to the apex (Render does this automatically when
   both are added) so there is exactly one canonical URL.

## 3. Wire Lemon Squeezy to the live domain

1. Settings → Webhooks → endpoint `https://trycardvault.com/api/billing/webhook`,
   events `subscription_created` + `subscription_expired` + `order_created`,
   signing secret =
   the `LEMONSQUEEZY_WEBHOOK_SECRET` you set above.
2. Keep the store in **test mode** until the launch checklist passes.

## 4. Launch checklist (run in Lemon Squeezy test mode)

1. Fresh browser on your phone: register → scan/add 2 cards → paywall
   appears → checkout with test card `4242 4242 4242 4242` → redirected back
   → "finalizing your upgrade" resolves to a PRO badge → add a 3rd card.
2. Webhook delivery log in Lemon Squeezy shows 200s.
3. "Manage billing" opens the customer portal; cancel the test subscription;
   after the period expires the account drops to FREE and cards remain.
4. Camera scan works over HTTPS on a real phone (getUserMedia requires it).
5. Flip the store to live mode, submit it for activation review, and repeat
   step 1 once with a real card. Refund yourself from the dashboard.

## Invariants for any other host (Railway, Fly.io, a VPS)

- Run `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` from
  `card-scanner-app/` (or use the Dockerfile).
- Mount a persistent volume and point `CARDVAULT_DB` at it.
- HTTPS on the final domain (camera + webhooks + basic trust all need it).
- Single instance only: SQLite doesn't support multi-instance writes. Scale
  up (bigger instance), not out, until the DB moves to Postgres.
