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
   | `GUMROAD_ACCESS_TOKEN` | Gumroad → Settings → Advanced → Applications |
   | `GUMROAD_YEARLY_URL` | product URL of the $19/year subscription |
   | `GUMROAD_MONTHLY_URL` | product URL of the $2.99/month subscription |
   | `GUMROAD_LIFETIME_URL` | product URL of the $39 one-time product (optional) |
   | `GUMROAD_WEBHOOK_SECRET` | any long random string you invent |

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
   | `GUMROAD_ACCESS_TOKEN` | Gumroad → Settings → Advanced → Applications |
   | `GUMROAD_YEARLY_URL` | product URL of the $19/year subscription |
   | `GUMROAD_MONTHLY_URL` | product URL of the $2.99/month subscription |
   | `GUMROAD_LIFETIME_URL` | product URL of the $39 one-time product (optional) |
   | `GUMROAD_WEBHOOK_SECRET` | any long random string you invent |

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

## 3. Wire Gumroad to the live domain

Create **three products** in Gumroad. There are no numeric IDs to find — you
copy each product's URL.

1. "CardVault Pro Yearly" — **$19, recurring, yearly**
2. "CardVault Pro Monthly" — **$2.99, recurring, monthly**
3. "CardVault Pro Lifetime" — **$39, one-time payment**, *not* a subscription.
   This is the step most people get wrong; created as a subscription, buyers
   get charged repeatedly for a "lifetime" plan.

Publish all three — an unpublished product cannot be bought.

Then, with the environment variables set:

```bash
cd card-scanner-app
python -m backend.check_billing --list              # products + the env var each belongs in
python -m backend.check_billing --register-webhook  # point Gumroad at this deployment
python -m backend.check_billing                     # verify everything
```

`--register-webhook` subscribes Gumroad's `sale`, `cancellation` and
`subscription_ended` events to
`https://trycardvault.com/api/billing/webhook?token=<your secret>`. Re-run it
whenever the public URL changes — a webhook still pointing at an old
deployment means purchases never grant access.

## 4. Launch checklist

Gumroad automatically treats a purchase by the product's creator as a **test
purchase** — it shows "your payment method will not be charged" at checkout —
so you can walk the whole flow signed in as yourself without moving money. (A
100% off discount code works too, if you want to test as someone else.)

1. `python -m backend.check_billing` reports no failures.
2. Fresh browser on your phone: register → add 2 cards → paywall appears →
   click "Yearly — $19" → Gumroad checkout opens → complete it (it will say
   "will not be charged") → back on the site, "finalizing your upgrade"
   resolves to a PRO badge → add a 3rd card.
3. Repeat for the lifetime product; the badge should read LIFETIME.
4. Camera scan works over HTTPS on a real phone (getUserMedia requires it).
5. Check every product is priced in the **same currency** as the paywall
   quotes (USD). A product created in another currency shows buyers a
   different number than the button they clicked;
   `python -m backend.check_billing` flags this.
6. Prove persistence: add a card, redeploy the service, log back in. If the
   card is still there, the volume is mounted correctly. If it vanished,
   `CARDVAULT_DB` is not on the volume — fix that before launch.
7. Ask someone else to buy one plan with a real card to confirm the live
   path — your own purchases are always test purchases. Refund them
   afterwards from Gumroad.

## If a purchase does not grant access

The webhook is the only thing that upgrades an account, and a webhook pointing
at a stale URL delivers nowhere — there is no retry to wait for. Re-point it,
then fix the affected customer by hand:

```bash
python -m backend.check_billing --register-webhook   # re-point at CARDVAULT_BASE_URL
python -m backend.check_billing                      # confirm they agree
railway run python -m backend.grant --list           # find the account
railway run python -m backend.grant buyer@example.com pro --subscription-id 12345
```

## Invariants for any other host (Fly.io, Koyeb, a VPS)

- Run `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` from
  `card-scanner-app/` (or use the Dockerfile).
- Mount a persistent volume and point `CARDVAULT_DB` at it.
- HTTPS on the final domain (camera + webhooks + basic trust all need it).
- Single instance only: SQLite doesn't support multi-instance writes. Scale
  up (bigger instance), not out, until the DB moves to Postgres.
