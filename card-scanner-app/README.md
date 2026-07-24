# CardVault — card scanner + encrypted vault with a Pro paywall

A self-contained web app that lets people **scan their payment cards with the
device camera, validate them, and store them in an encrypted personal vault** —
monetized with a freemium paywall (free: 2 cards, Pro: unlimited via Stripe).

> This app lives in its own directory and is independent of the Agent Reach
> package — nothing here imports from or modifies `agent_reach/`.

## How it works

```
Browser                                   Server (FastAPI + SQLite)
─────────────────────────────             ─────────────────────────
camera → OCR (tesseract.js)               auth (PBKDF2 passwords,
      → Luhn validation                        bearer session tokens)
      → brand detection                   encrypted blob storage
      → AES-256-GCM encryption   ──────►  paywall enforcement (402)
        (passphrase-derived key)          Stripe Checkout + webhooks
```

### Security model (the point of the app)

- **Zero-knowledge storage.** The card number, expiry, and holder name are
  encrypted *in the browser* with AES-256-GCM. The key is derived from a vault
  passphrase via PBKDF2-SHA256 (310k iterations) with a per-card salt. The
  server stores only ciphertext plus non-sensitive display fields (brand,
  last 4 digits, label).
- **On-device OCR.** Camera frames are processed locally with tesseract.js;
  no image or plaintext number is ever uploaded.
- **Server-side safety net.** The API rejects any display field that looks
  like a full card number (13–19 digit sequences), so a buggy or malicious
  client can't accidentally persist plaintext PANs.
- **No passphrase recovery.** By design, losing the vault passphrase means the
  ciphertext is unrecoverable. The UI says so.
- **Standard auth hygiene.** Passwords hashed with PBKDF2-SHA256 (600k
  iterations, constant-time comparison), random 256-bit session tokens with a
  7-day TTL, per-user data isolation on every vault query.

Because full card numbers never reach the server, the backend stays outside
the scope of PCI-DSS storage requirements (last4 + brand are explicitly
permitted for display). If you deploy this commercially, still serve it over
HTTPS only and review PCI-DSS SAQ A guidance.

### Paywall

- Free plan: **2 cards** (configurable via `CARDVAULT_FREE_LIMIT`).
- Pro plan: unlimited cards, sold as a Stripe subscription.
- Enforcement is **server-side**: the 3rd card on a free plan returns HTTP
  `402 Payment Required`, and the UI shows the upgrade panel.
- Upgrades flow through Stripe Checkout; the webhook
  (`checkout.session.completed`) flips the user to `pro`, and
  `customer.subscription.deleted` downgrades them back to `free`.

## Running it

```bash
cd card-scanner-app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
# open http://localhost:8000
```

Camera access requires a secure context: `localhost` works out of the box;
any other host must be HTTPS.

### Stripe setup (for the paywall)

```bash
export STRIPE_SECRET_KEY=sk_test_...
export STRIPE_PRICE_ID=price_...          # a recurring Price for "Pro"
export STRIPE_WEBHOOK_SECRET=whsec_...    # from `stripe listen` or the dashboard
export CARDVAULT_BASE_URL=http://localhost:8000
```

For local webhook testing: `stripe listen --forward-to localhost:8000/api/billing/webhook`

Without Stripe keys the app still runs — checkout returns 503 and, if you set
`CARDVAULT_DEV=1`, a `POST /api/billing/dev-upgrade` endpoint lets you
exercise the Pro path locally.

## Tests

```bash
cd card-scanner-app
pytest tests/ -v
```

Covers registration/login/logout, session invalidation, per-user card
isolation, the plaintext-PAN rejection safety net, free-limit enforcement
(402), Pro bypass, and webhook signature rejection.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/register` | Create account, returns bearer token |
| POST | `/api/login` | Log in, returns bearer token |
| POST | `/api/logout` | Invalidate session |
| GET | `/api/me` | Plan, card count, free limit |
| GET | `/api/cards` | List encrypted cards |
| POST | `/api/cards` | Store an encrypted card (402 at free limit) |
| DELETE | `/api/cards/{id}` | Delete a card |
| POST | `/api/billing/checkout` | Start Stripe Checkout for Pro |
| POST | `/api/billing/portal` | Open the Stripe customer portal (Pro self-serve cancel) |
| POST | `/api/billing/webhook` | Stripe webhook (signature-verified) |

Enable the customer portal in Stripe (Settings → Billing → Customer portal) so
the "Manage billing" button works — cancellations and card updates are then
fully self-serve.
