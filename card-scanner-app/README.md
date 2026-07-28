# CardVault — card scanner + encrypted vault with a Pro paywall

A self-contained web app that lets people **scan their payment cards with the
device camera, validate them, and store them in an encrypted personal vault** —
monetized with a freemium paywall (free: 2 cards, Pro: unlimited via Gumroad,
a merchant of record that pays out to founders in countries Stripe doesn't
support — including the Philippines).

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
        (passphrase-derived key)          Gumroad checkout + webhooks
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
- Pro plan: unlimited cards, sold as three explicit choices — **$19/year**
  (the default and the one the paywall leads with), $2.99/month, or $39
  one-time for the lifetime plan. Gumroad is the merchant of record, so it
  also handles global sales tax/VAT.
- Annual is the default deliberately: the per-transaction fee takes ~22% of a
  $2.99 monthly charge but only ~7.6% of a $19 annual one.
- Enforcement is **server-side**: the 3rd card on a free plan returns HTTP
  `402 Payment Required`, and the UI shows the upgrade panel.
- Upgrades flow through Gumroad checkout. The buyer's `user_id` rides along
  in the checkout URL and returns in the webhook's `url_params`, which is how
  a payment is tied to an account.
- **Webhooks are verified by calling Gumroad back.** A ping is only acted on
  if `GET /v2/sales/{id}` confirms the sale exists in your account, so a
  forged POST cannot grant anyone Pro. A shared `?token=` secret rejects
  random traffic before that callback.
- A cancellation is deliberately ignored (the customer paid through the
  period); only a subscription actually ending downgrades to `free`. A sale of
  the lifetime product grants `lifetime`, which is never downgraded.

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

### Gumroad setup (for the paywall)

Create **three products** in Gumroad — there are no numeric IDs to hunt for,
you copy each product's URL:

1. "CardVault Pro Yearly" — **$19, recurring, yearly**
2. "CardVault Pro Monthly" — **$2.99, recurring, monthly**
3. "CardVault Pro Lifetime" — **$39, one-time** (not a subscription)

Then create an access token at Gumroad → Settings → Advanced → Applications.

```bash
export GUMROAD_ACCESS_TOKEN=...
export GUMROAD_YEARLY_URL=https://yourshop.gumroad.com/l/...
export GUMROAD_MONTHLY_URL=https://yourshop.gumroad.com/l/...
export GUMROAD_LIFETIME_URL=https://yourshop.gumroad.com/l/...   # optional
export GUMROAD_WEBHOOK_SECRET=...        # any long random string you invent
export CARDVAULT_BASE_URL=http://localhost:8000
```

Then point Gumroad's webhooks at this deployment and verify everything:

```bash
python -m backend.check_billing --list              # show your products + the env var for each
python -m backend.check_billing --register-webhook  # register sale/cancellation/ended hooks
python -m backend.check_billing                     # verify the whole setup
```

The checker asks Gumroad directly whether the token works, each product URL
exists with the advertised price and recurrence, and the webhook points at
this deployment. It is read-only.

Without a Gumroad token the app still runs — checkout returns 503 and, if you
set `CARDVAULT_DEV=1`, a `POST /api/billing/dev-upgrade` endpoint lets you
exercise the Pro path locally.

## Rescuing a missed sale

Webhooks are not guaranteed. If Gumroad's webhook was pointing at an old
deployment when someone paid, the ping never arrived and there is nothing to
retry — the customer is charged and still on the free plan. Fix them by hand:

```bash
python -m backend.grant --list                                   # who exists, on what plan
python -m backend.grant buyer@example.com pro --subscription-id 12345
python -m backend.grant buyer@example.com lifetime               # a one-time sale
python -m backend.grant buyer@example.com free                   # revoke
```

Pass `--subscription-id` (from the Gumroad sale) whenever you rescue a
**subscription**: without it the account is upgraded but never linked to the
subscription, so the eventual `subscription_ended` webhook cannot find them
and they keep Pro after cancelling.

On Railway, run it with `railway run` (or from the service shell) so
`CARDVAULT_DB` points at the mounted volume rather than a local file.

## Tests

```bash
cd card-scanner-app
pytest tests/ -v
```

Covers registration/login/logout, session invalidation, per-user card
isolation, the plaintext-PAN rejection safety net, free-limit enforcement
(402), Pro bypass, webhook token + sale verification, the full subscription
lifecycle (sale → pro, cancelled → still pro, ended → free), the guarantee
that a **forged webhook cannot grant access** because Gumroad must confirm the
sale, and the billing-configuration checker (wrong prices, lifetime
misconfigured as a subscription, unpublished products, webhook pointing at
the wrong host), and the manual grant CLI (including that a rescued
subscription can still be downgraded when it later ends).

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
| POST | `/api/billing/checkout` | Start checkout; body `{"tier": "yearly"\|"monthly"\|"lifetime"}`, defaults to `yearly` |
| POST | `/api/billing/portal` | Link to Gumroad subscription management |
| POST | `/api/billing/webhook` | Gumroad ping (`?token=` secret + sale verified via the API) |

Gumroad subscribers manage or cancel from their Gumroad library and from the
link in their receipt email, so cancellation stays self-serve.
