# Gumroad product listings — copy and paste

Gumroad hosts the **checkout**, not the app. CardVault itself runs on Railway
(see DEPLOY.md); these three products are what take the payment and trigger
the webhook that grants Pro.

Create all three, publish them, then run:

```bash
python -m backend.check_billing --list   # gives you the URL for each env var
```

---

## Product 1 — CardVault Pro (Yearly)

- **Type:** Membership / subscription
- **Price:** `$19` — recurring, **yearly**
- **URL slug:** `cardvault-yearly`
- **Environment variable:** `GUMROAD_YEARLY_URL`

**Name**

```
CardVault Pro — Yearly
```

**Description**

```
Every card you own, encrypted before anyone else can see it.

CardVault scans your cards with your phone camera and locks them in a vault
only you can open. The encryption happens on your device, so the server
stores scrambled text it cannot read — not a promise, just how it's built.

Pro removes the two-card limit on the free plan:

• Unlimited cards — debit, credit, business, the one that only exists for
  subscriptions. All of them, one passphrase.
• The same zero-knowledge encryption. Pro doesn't unlock your data for us.
• Works on any device with a browser and a camera.

$19 a year — about $1.58 a month, and 47% less than paying monthly.

Cancel any time. Your cards stay encrypted and readable if you do; you just
can't add more than two. No hostage-taking.

One thing to know before you buy: there is no passphrase recovery. If you
lose it, your cards are gone. That's the trade-off that makes the vault worth
having — a company that can recover your data is a company that can leak it.
```

---

## Product 2 — CardVault Pro (Monthly)

- **Type:** Membership / subscription
- **Price:** `$2.99` — recurring, **monthly**
- **URL slug:** `cardvault-monthly`
- **Environment variable:** `GUMROAD_MONTHLY_URL`

**Name**

```
CardVault Pro — Monthly
```

**Description**

```
Every card you own, encrypted before anyone else can see it.

CardVault scans your cards with your phone camera and locks them in a vault
only you can open. The encryption happens on your device, so the server
stores scrambled text it cannot read.

Pro removes the two-card limit on the free plan:

• Unlimited cards, one passphrase.
• The same zero-knowledge encryption.
• Works on any device with a browser and a camera.

$2.99 a month, cancel any time. Paying yearly is $19 — the same thing for
47% less, if you already know you'll keep it.

There is no passphrase recovery. If you lose it, your cards are gone. That's
deliberate: a company that can recover your data is one that can leak it.
```

---

## Product 3 — CardVault Pro (Lifetime)

- **Type:** **One-time payment — NOT a subscription.** This is the step most
  people get wrong; created as a subscription, buyers get charged every month
  for a "lifetime" plan.
- **Price:** `$39` — one-time
- **URL slug:** `cardvault-lifetime`
- **Environment variable:** `GUMROAD_LIFETIME_URL`

**Name**

```
CardVault Pro — Lifetime
```

**Description**

```
Pay once. Unlimited encrypted cards, forever.

CardVault scans your cards with your phone camera and locks them in a vault
only you can open — encrypted on your device, so the server stores scrambled
text it cannot read.

Lifetime is exactly the Pro plan, without the renewal:

• Unlimited cards, one passphrase, no subscription to remember.
• The same zero-knowledge encryption as every other plan.
• No future price increase, no expiry.

$39 once, instead of $19 every year.

Launch offer — this price is for early buyers and won't be listed forever.

There is no passphrase recovery. If you lose it, your cards are gone. That's
deliberate: a company that can recover your data is one that can leak it.
```

---

## After creating them

1. Publish all three — an unpublished product cannot be bought.
2. `python -m backend.check_billing --list` → copy each URL into the matching
   environment variable on your host.
3. `python -m backend.check_billing --register-webhook` → points Gumroad's
   sale, cancellation and subscription_ended events at your deployment.
4. `python -m backend.check_billing` → should report no failures.
5. Create a **100% off discount code** on each product to walk the purchase
   flow end to end without moving money, then delete the codes before launch.
