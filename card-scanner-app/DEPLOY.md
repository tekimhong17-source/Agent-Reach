# Deploying CardVault to trycardvault.com

The app is a single FastAPI service (serves the API and the frontend) with a
SQLite database. It needs one host with HTTPS and a **persistent disk** —
SQLite on an ephemeral filesystem loses every user on restart.

Everything ships as a container, so the app is portable: any host that builds
from `card-scanner-app/Dockerfile` and can attach a volume will run it
unchanged. Railway is the primary path below; Render is kept as an
alternative, and the invariants for any other host are at the bottom.

## 1. Create the service on Railway

`card-scanner-app/railway.json` already pins the build (Dockerfile), the
health check, the restart policy, and **one replica** — SQLite cannot take
writes from two instances at once, so this must stay at 1 until the database
moves to Postgres.

1. railway.app → **New Project → Deploy from GitHub repo** → pick this repo.
2. Service → **Settings → Root Directory**: `card-scanner-app`. Railway then
   finds both `railway.json` and the `Dockerfile` and builds from them.
3. Service → **Variables** → add:

   | Key | Value |
   |---|---|
   | `CARDVAULT_DB` | `/data/cardvault.db` |
   | `CARDVAULT_BASE_URL` | the public URL you are actually browsing (see below) |
   | `LEMONSQUEEZY_API_KEY` | from Lemon Squeezy → Settings → API |
   | `LEMONSQUEEZY_STORE_ID` | numeric store ID |
   | `LEMONSQUEEZY_VARIANT_ID` | variant ID of the monthly subscription |
   | `LEMONSQUEEZY_ANNUAL_VARIANT_ID` | variant ID of the yearly subscription |
   | `LEMONSQUEEZY_LIFETIME_VARIANT_ID` | variant ID of the lifetime product (optional) |
   | `LEMONSQUEEZY_WEBHOOK_SECRET` | the signing secret you chose |

   Do **not** set `CARDVAULT_DEV` — it exposes a free-upgrade endpoint meant
   for local testing only. Do **not** set `PORT`; Railway injects it and the
   Dockerfile already reads it.

4. Service → **Volumes → Add volume**, mount path `/data`. Volumes are
   attached in the dashboard, not in `railway.json`. **Do this before real
   users sign up** — without it the database sits on the container filesystem
   and is wiped on every redeploy.
5. Settings → **Networking → Generate Domain** for a `*.up.railway.app` URL,
   and confirm it serves the app.

<details>
<summary>Alternative: Render</summary>

**Fastest path — Blueprint.** `card-scanner-app/render.yaml`
already describes the whole service: Docker runtime, the `card-scanner-app`
root directory, a Starter instance, the 1 GB disk mounted at `/data`, and
every environment variable. Render Dashboard → **New → Blueprint** → pick this
repo, and it prompts for the secrets (they are never stored in the repo).
Use this to recreate the service after a deletion — it restores the exact
configuration instead of retyping it. Then skip to step 2.

Manual Render setup, if you prefer clicking:

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
   | `LEMONSQUEEZY_VARIANT_ID` | variant ID of the monthly subscription |
   | `LEMONSQUEEZY_ANNUAL_VARIANT_ID` | variant ID of the yearly subscription |
   | `LEMONSQUEEZY_LIFETIME_VARIANT_ID` | variant ID of the lifetime product (optional) |
   | `LEMONSQUEEZY_WEBHOOK_SECRET` | the signing secret you chose |

   Do **not** set `CARDVAULT_DEV` in production — it exposes a free-upgrade
   endpoint meant for local testing only.

5. Deploy, and confirm the `*.onrender.com` URL serves the app.

</details>

**`CARDVAULT_BASE_URL` must match the host you are actually browsing.** While
the custom domain is still pending, set it to the generated
`*.up.railway.app` (or `*.onrender.com`) URL. If it points somewhere else,
checkout redirects land on a host where the buyer is not logged in and the
upgrade looks broken.

## 2. Point the domain

1. Railway → service → **Settings → Networking → Custom Domain** → add
   `trycardvault.com` and `www.trycardvault.com`. (Render: Settings → Custom
   Domains.)
2. The host shows the DNS records to create at your registrar — typically a
   CNAME for `www` and an A/ALIAS for the apex. Use exactly the targets it
   gives you; they are specific to your service, so records left over from a
   previous deployment will point at nothing.
3. Wait for DNS to propagate and the TLS certificate to issue (minutes to a
   few hours).
4. Make `www` redirect to the apex so there is exactly one canonical URL.
5. Update `CARDVAULT_BASE_URL` to `https://trycardvault.com`.

## 3. Wire Lemon Squeezy to the live domain

You need **two products** — one subscription with two variants, and one
one-time product — plus a webhook. Do it in this order.

1. **Create the subscription product.** Products → New Product → name it
   "CardVault Pro". Set its price to **$2.99, billing period Monthly**. Save.
2. **Add the yearly variant to that same product.** Open the product →
   Variants → add a second variant priced **$19, billing period Yearly**.
   Two variants on one product is what lets buyers choose; a separate
   product would work too, but this keeps the dashboard tidy.
3. **Create the lifetime product.** Products → New Product → "CardVault Pro
   Lifetime", price **$39**, and set it to a **one-time payment**, not a
   subscription. This is the step most people get wrong — if it is created
   as a subscription, buyers get charged repeatedly for a "lifetime" plan.
4. **Create an API key.** Settings → API → new key. Copy it into
   `LEMONSQUEEZY_API_KEY`.
5. **Find the numeric IDs without hunting.** With the API key set, run:

   ```bash
   cd card-scanner-app
   python -m backend.check_billing --list
   ```

   It prints every store, product and variant with its ID, and suggests which
   environment variable each one belongs in. Copy those into your host.

6. **Add the webhook.** Settings → Webhooks → new webhook:
   - URL: `https://trycardvault.com/api/billing/webhook` (or your current
     `*.up.railway.app` URL — it must match `CARDVAULT_BASE_URL`)
   - Events: `subscription_created`, `subscription_expired`, `order_created`
   - Signing secret: choose one, and put the same string in
     `LEMONSQUEEZY_WEBHOOK_SECRET`
7. **Verify the whole thing before trusting it:**

   ```bash
   python -m backend.check_billing
   ```

   It asks Lemon Squeezy directly whether the key works, the store exists,
   each variant has the price and billing period the paywall advertises, and
   the webhook points at this deployment with all three events. Every failure
   names the exact thing to fix. It is read-only — it never charges anything.

8. Keep the store in **test mode** until the launch checklist below passes.

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

6. Prove persistence before launch: add a card, redeploy the service, log
   back in. If the card is still there, the volume is mounted correctly. If
   it vanished, `CARDVAULT_DB` is not on the volume — fix that first.

## Invariants for any other host (Fly.io, Koyeb, a VPS)

- Run `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` from
  `card-scanner-app/` (or use the Dockerfile).
- Mount a persistent volume and point `CARDVAULT_DB` at it.
- HTTPS on the final domain (camera + webhooks + basic trust all need it).
- Single instance only: SQLite doesn't support multi-instance writes. Scale
  up (bigger instance), not out, until the DB moves to Postgres.
