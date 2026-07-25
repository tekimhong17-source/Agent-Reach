# CardVault — card scanner + encrypted vault with a Pro paywall

A self-contained web app that lets people **scan their payment cards with the
device camera, validate them, and store them in an encrypted personal vault** —
monetized with a freemium paywall (free: 2 cards, Pro: unlimited via Lemon
Squeezy, a merchant of record that works for founders in countries Stripe
doesn't support).

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
        (passphrase-derived key)          Lemon Squeezy checkout + webhooks
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
- Pro plan: unlimited cards, sold as a Lemon Squeezy subscription (Lemon
  Squeezy is the merchant of record, so it also handles global sales tax/VAT).
- Enforcement is **server-side**: the 3rd card on a free plan returns HTTP
  `402 Payment Required`, and the UI shows the upgrade panel.
- Upgrades flow through Lemon Squeezy checkout; the `subscription_created`
  webhook flips the user to `pro`. `subscription_cancelled` is deliberately
  ignored (the customer paid through the period), and `subscription_expired`
  downgrades them back to `free` at period end.

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

### Lemon Squeezy setup (for the paywall)

1. Create a store at lemonsqueezy.com, add a subscription product ("CardVault
   Pro") and note the **variant ID** of its monthly price.
2. Create an API key under Settings → API.
3. Add a webhook under Settings → Webhooks pointing at
   `https://yourdomain.com/api/billing/webhook`, subscribed to
   `subscription_created` and `subscription_expired`, and choose a signing
   secret.

```bash
export LEMONSQUEEZY_API_KEY=...
export LEMONSQUEEZY_STORE_ID=...          # numeric store ID
export LEMONSQUEEZY_VARIANT_ID=...        # variant ID of the Pro subscription
export LEMONSQUEEZY_WEBHOOK_SECRET=...    # the signing secret you chose
export CARDVAULT_BASE_URL=http://localhost:8000
```

Use the store's **test mode** for end-to-end checkout testing before launch.
Without Lemon Squeezy keys the app still runs — checkout returns 503 and, if
you set `CARDVAULT_DEV=1`, a `POST /api/billing/dev-upgrade` endpoint lets
you exercise the Pro path locally.

## Tests

```bash
cd card-scanner-app
pytest tests/ -v
```

Covers registration/login/logout, session invalidation, per-user card
isolation, the plaintext-PAN rejection safety net, free-limit enforcement
(402), Pro bypass, webhook signature verification, and the full subscription
lifecycle (created → pro, cancelled → still pro, expired → free).

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
| POST | `/api/billing/checkout` | Start Lemon Squeezy checkout for Pro |
| POST | `/api/billing/portal` | Open the Lemon Squeezy customer portal (self-serve cancel) |
| POST | `/api/billing/webhook` | Lemon Squeezy webhook (HMAC signature-verified) |

The "Manage billing" button fetches the signed customer-portal URL from the
user's subscription, so cancellations and card updates are fully self-serve.
