"""Verify the Gumroad setup before trusting it with real money.

Run from card-scanner-app/ with your billing environment variables set:

    python -m backend.check_billing          # verify the setup
    python -m backend.check_billing --list   # show your Gumroad products

It answers "did I wire this up correctly?" by asking Gumroad directly: is the
access token valid, does each configured product URL exist in your account,
does each carry the price and recurrence the paywall advertises, and is the
sale webhook pointing at this deployment.

Every failure names the exact thing to fix. Nothing here writes or charges
anything — it is read-only.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import httpx

from .billing import API_BASE, TIER_URL_ENV, permalink_of

# What the paywall promises, so the checker can tell you when Gumroad
# disagrees with the app. Prices are in cents, as Gumroad returns them.
EXPECTED = {
    "yearly": {"label": "Yearly plan", "price": 1900, "recurring": True},
    "monthly": {"label": "Monthly plan", "price": 299, "recurring": True},
    "lifetime": {"label": "Lifetime plan", "price": 3900, "recurring": False},
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    warnings: list[str] = field(default_factory=list)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _money(cents: object) -> str:
    try:
        return f"${int(cents) / 100:.2f}"
    except (TypeError, ValueError):
        return f"{cents!r}"


def run_checks(client: httpx.Client | None = None) -> list[Check]:
    owns = client is None
    client = client or httpx.Client(timeout=20)
    try:
        return _run(client)
    finally:
        if owns:
            client.close()


def _run(client: httpx.Client) -> list[Check]:
    checks: list[Check] = []
    token = os.environ.get("GUMROAD_ACCESS_TOKEN", "")
    base_url = os.environ.get("CARDVAULT_BASE_URL", "")
    secret = os.environ.get("GUMROAD_WEBHOOK_SECRET", "")

    # 1. Environment -----------------------------------------------------------
    missing = [k for k in ("GUMROAD_ACCESS_TOKEN", "GUMROAD_WEBHOOK_SECRET",
                           "CARDVAULT_BASE_URL") if not os.environ.get(k)]
    checks.append(Check(
        "Environment variables set", not missing,
        "all core variables present" if not missing else f"missing: {', '.join(missing)}",
        "Set them on your host (Railway → Variables), then redeploy.",
    ))
    if not token:
        return checks

    # 2. Access token ----------------------------------------------------------
    try:
        r = client.get(f"{API_BASE}/user", headers=_headers(token))
    except httpx.HTTPError as exc:
        checks.append(Check("Access token valid", False, f"could not reach Gumroad: {exc}",
                            "Check network access to api.gumroad.com."))
        return checks
    if r.status_code != 200 or not r.json().get("success", True):
        checks.append(Check("Access token valid", False, f"HTTP {r.status_code}",
                            "Create a token at Gumroad → Settings → Advanced → "
                            "Applications, and set GUMROAD_ACCESS_TOKEN."))
        return checks
    user = r.json().get("user", {})
    checks.append(Check("Access token valid", True,
                        f"authenticated as {user.get('email') or user.get('name') or 'unknown'}"))

    # 3. Products --------------------------------------------------------------
    products_resp = client.get(f"{API_BASE}/products", headers=_headers(token))
    products = products_resp.json().get("products", []) if products_resp.status_code == 200 else []
    by_permalink = {p.get("short_url", "") and permalink_of(p["short_url"]): p for p in products}
    by_permalink.update({p.get("permalink"): p for p in products if p.get("permalink")})

    for tier, want in EXPECTED.items():
        env_key = TIER_URL_ENV[tier]
        url = os.environ.get(env_key)
        if not url:
            optional = tier == "lifetime"
            checks.append(Check(
                f"{want['label']} product", optional,
                "not configured" + (" (optional — button will 503)" if optional else ""),
                f"Set {env_key} to the product's Gumroad URL.",
            ))
            continue

        product = by_permalink.get(permalink_of(url))
        if not product:
            checks.append(Check(
                f"{want['label']} product", False,
                f"no product in your account matches {url}",
                f"Copy the product's URL from Gumroad (Share → link) into {env_key}.",
            ))
            continue

        warnings: list[str] = []
        price = product.get("price")
        recurring = product.get("is_recurring_billing")
        # Currency first: a product priced in another currency looks like a
        # wildly wrong price, when really only the currency is wrong. The
        # paywall quotes USD, so anything else is a mismatch the buyer sees
        # between the button they clicked and the checkout they land on.
        currency = (product.get("currency") or "").lower()
        if currency and currency != "usd":
            warnings.append(
                f"priced in {currency.upper()}, but the paywall quotes USD — buyers click "
                f"'{_money(want['price'])}' and land on a {currency.upper()} checkout")
        elif price is not None and price != want["price"]:
            warnings.append(
                f"price is {_money(price)} but the paywall advertises {_money(want['price'])}")
        if recurring is not None and bool(recurring) != want["recurring"]:
            is_kind = "a subscription" if recurring else "a one-time purchase"
            want_kind = "a subscription" if want["recurring"] else "a one-time purchase"
            warnings.append(f"this product is {is_kind}, but {want['label']} must be {want_kind}")
        if product.get("published") is False:
            warnings.append("product is unpublished — buyers cannot reach it")

        checks.append(Check(
            f"{want['label']} product", True,
            f"{product.get('name', '?')} — {_money(price)}"
            + (" recurring" if recurring else " one-time"),
            "Fix the product in Gumroad, or update the paywall copy to match."
            if warnings else "",
            warnings,
        ))

    # 4. Webhook ---------------------------------------------------------------
    want_url = f"{base_url.rstrip('/')}/api/billing/webhook?token={secret}" if base_url else None
    subs_resp = client.get(f"{API_BASE}/resource_subscriptions",
                           headers=_headers(token), params={"resource_name": "sale"})
    if subs_resp.status_code != 200:
        checks.append(Check(
            "Sale webhook registered", False, f"HTTP {subs_resp.status_code}",
            "Register it once with: python -m backend.check_billing --register-webhook"))
    else:
        subs = subs_resp.json().get("resource_subscriptions", [])
        urls = [s.get("post_url", "") for s in subs]
        if not subs:
            checks.append(Check(
                "Sale webhook registered", False, "no sale webhook on this account",
                f"Register it: python -m backend.check_billing --register-webhook "
                f"(it will point at {want_url})"))
        elif want_url and want_url not in urls:
            checks.append(Check(
                "Webhook points at this deployment", False,
                f"registered: {', '.join(urls)} — expected {want_url}",
                "Re-register with --register-webhook, or fix CARDVAULT_BASE_URL. "
                "If they disagree, purchases will never grant access."))
        else:
            checks.append(Check("Sale webhook registered", True, want_url or urls[0]))

    return checks


def register_webhook(client: httpx.Client | None = None) -> str:
    """Point Gumroad's sale + subscription webhooks at this deployment."""
    owns = client is None
    client = client or httpx.Client(timeout=20)
    token = os.environ.get("GUMROAD_ACCESS_TOKEN", "")
    secret = os.environ.get("GUMROAD_WEBHOOK_SECRET", "")
    base = os.environ.get("CARDVAULT_BASE_URL", "").rstrip("/")
    try:
        if not (token and secret and base):
            return ("Set GUMROAD_ACCESS_TOKEN, GUMROAD_WEBHOOK_SECRET and "
                    "CARDVAULT_BASE_URL first.")
        post_url = f"{base}/api/billing/webhook?token={secret}"
        results = []
        for resource in ("sale", "cancellation", "subscription_ended"):
            r = client.put(f"{API_BASE}/resource_subscriptions", headers=_headers(token),
                           data={"resource_name": resource, "post_url": post_url})
            ok = r.status_code < 300 and r.json().get("success", False)
            results.append(f"  {resource}: {'registered' if ok else f'FAILED ({r.status_code})'}")
        return f"Pointing Gumroad at {post_url}\n" + "\n".join(results)
    except httpx.HTTPError as exc:
        return f"Could not reach Gumroad: {exc}"
    finally:
        if owns:
            client.close()


def list_catalog(client: httpx.Client | None = None) -> list[str]:
    """Print every product with its URL, price and the env var it belongs in."""
    owns = client is None
    client = client or httpx.Client(timeout=20)
    token = os.environ.get("GUMROAD_ACCESS_TOKEN", "")
    lines: list[str] = []
    try:
        if not token:
            return ["GUMROAD_ACCESS_TOKEN is not set — create one at "
                    "Gumroad → Settings → Advanced → Applications."]
        r = client.get(f"{API_BASE}/products", headers=_headers(token))
        if r.status_code != 200:
            return [f"Could not list products (HTTP {r.status_code}); check the token."]
        for p in r.json().get("products", []):
            recurring = p.get("is_recurring_billing")
            kind = "recurring" if recurring else "one-time"
            hint = ""
            for tier, want in EXPECTED.items():
                if p.get("price") == want["price"] and bool(recurring) == want["recurring"]:
                    hint = f"\n      → {TIER_URL_ENV[tier]}={p.get('short_url')}"
            lines.append(f"  {p.get('name', '?')} — {_money(p.get('price'))} {kind}"
                         f"{'' if p.get('published', True) else '  [UNPUBLISHED]'}"
                         f"\n      {p.get('short_url', '?')}{hint}")
    except httpx.HTTPError as exc:
        lines.append(f"Could not reach Gumroad: {exc}")
    finally:
        if owns:
            client.close()
    return lines or ["No products found — create them in Gumroad first."]


def main() -> int:
    if "--list" in sys.argv:
        print("\nYour Gumroad products\n" + "=" * 21)
        for line in list_catalog():
            print(line)
        print()
        return 0
    if "--register-webhook" in sys.argv:
        print(register_webhook())
        return 0

    checks = run_checks()
    print("\nCardVault billing configuration\n" + "=" * 34)
    failed = 0
    for c in checks:
        mark = "FAIL" if not c.ok else ("WARN" if c.warnings else "OK  ")
        print(f"[{mark}] {c.name}" + (f": {c.detail}" if c.detail else ""))
        for w in c.warnings:
            print(f"       ! {w}")
        if c.fix and (not c.ok or c.warnings):
            print(f"       → {c.fix}")
        if not c.ok:
            failed += 1
    print()
    print(f"{failed} problem(s) to fix before taking payments." if failed
          else "Configuration looks correct. Make a test purchase to confirm end to end.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
