"""Verify the Lemon Squeezy setup before trusting it with real money.

Run from card-scanner-app/ with your billing environment variables set:

    python -m backend.check_billing

It answers the question "did I wire the dashboard up correctly?" by asking
Lemon Squeezy directly: is the API key valid, does the store exist, does each
variant ID point at a product with the price and billing period this app
expects, and is the webhook pointing at this deployment with the right events.

Every failure names the exact thing to fix. Nothing here writes or charges
anything — it is read-only.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import httpx

API_BASE = "https://api.lemonsqueezy.com/v1"

# What the paywall promises, so the checker can tell you when the dashboard
# disagrees with the app. Prices are in cents, as Lemon Squeezy returns them.
EXPECTED = {
    "LEMONSQUEEZY_ANNUAL_VARIANT_ID": {
        "label": "Yearly plan", "price": 1900, "interval": "year", "subscription": True,
    },
    "LEMONSQUEEZY_VARIANT_ID": {
        "label": "Monthly plan", "price": 299, "interval": "month", "subscription": True,
    },
    "LEMONSQUEEZY_LIFETIME_VARIANT_ID": {
        "label": "Lifetime plan", "price": 3900, "interval": None, "subscription": False,
    },
}

REQUIRED_EVENTS = {"subscription_created", "subscription_expired", "order_created"}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    warnings: list[str] = field(default_factory=list)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Authorization": f"Bearer {api_key}",
    }


def _money(cents: object) -> str:
    try:
        return f"${int(cents) / 100:.2f}"
    except (TypeError, ValueError):
        return f"{cents!r}"


def run_checks(client: httpx.Client | None = None) -> list[Check]:
    owns_client = client is None
    client = client or httpx.Client(timeout=20)
    checks: list[Check] = []
    try:
        checks.extend(_run(client))
    finally:
        if owns_client:
            client.close()
    return checks


def _run(client: httpx.Client) -> list[Check]:
    checks: list[Check] = []

    api_key = os.environ.get("LEMONSQUEEZY_API_KEY", "")
    store_id = os.environ.get("LEMONSQUEEZY_STORE_ID", "")
    base_url = os.environ.get("CARDVAULT_BASE_URL", "")

    # 1. Environment variables -------------------------------------------------
    missing = [k for k in ("LEMONSQUEEZY_API_KEY", "LEMONSQUEEZY_STORE_ID",
                           "LEMONSQUEEZY_WEBHOOK_SECRET", "CARDVAULT_BASE_URL")
               if not os.environ.get(k)]
    checks.append(Check(
        "Environment variables set",
        not missing,
        "all core variables present" if not missing else f"missing: {', '.join(missing)}",
        "Set them on your host (Railway → Variables), then redeploy.",
    ))
    if not api_key:
        return checks  # nothing else can be checked without a key

    # 2. API key ---------------------------------------------------------------
    try:
        r = client.get(f"{API_BASE}/users/me", headers=_headers(api_key))
        if r.status_code == 200:
            who = r.json().get("data", {}).get("attributes", {})
            checks.append(Check("API key valid", True,
                                f"authenticated as {who.get('email') or who.get('name') or 'unknown'}"))
        else:
            checks.append(Check("API key valid", False, f"HTTP {r.status_code}",
                                "Create a fresh key at Lemon Squeezy → Settings → API "
                                "and set LEMONSQUEEZY_API_KEY."))
            return checks
    except httpx.HTTPError as exc:
        checks.append(Check("API key valid", False, f"could not reach Lemon Squeezy: {exc}",
                            "Check network/proxy access to api.lemonsqueezy.com."))
        return checks

    # 3. Store -----------------------------------------------------------------
    if store_id:
        r = client.get(f"{API_BASE}/stores/{store_id}", headers=_headers(api_key))
        if r.status_code == 200:
            a = r.json().get("data", {}).get("attributes", {})
            checks.append(Check("Store ID valid", True,
                                f"{a.get('name', '?')} ({a.get('slug', '?')})"))
        else:
            checks.append(Check("Store ID valid", False, f"HTTP {r.status_code}",
                                "Find the numeric store ID in the Lemon Squeezy URL "
                                "when your store is open, and set LEMONSQUEEZY_STORE_ID."))

    # 4. Each plan variant -----------------------------------------------------
    for env_key, want in EXPECTED.items():
        variant_id = os.environ.get(env_key)
        if not variant_id:
            optional = env_key == "LEMONSQUEEZY_LIFETIME_VARIANT_ID"
            checks.append(Check(
                f"{want['label']} variant", optional,
                "not configured" + (" (optional — button will 503)" if optional else ""),
                f"Set {env_key} to the variant ID of the {want['label'].lower()}.",
            ))
            continue

        r = client.get(f"{API_BASE}/variants/{variant_id}", headers=_headers(api_key))
        if r.status_code != 200:
            checks.append(Check(f"{want['label']} variant", False,
                                f"variant {variant_id} not found (HTTP {r.status_code})",
                                f"Open the variant in Lemon Squeezy; its numeric ID goes in {env_key}."))
            continue

        a = r.json().get("data", {}).get("attributes", {})
        warnings: list[str] = []
        price, interval = a.get("price"), a.get("interval")
        is_sub = a.get("is_subscription")

        if price is not None and price != want["price"]:
            warnings.append(
                f"price is {_money(price)} but the paywall advertises {_money(want['price'])}")
        if want["subscription"] and interval != want["interval"]:
            warnings.append(
                f"billing period is {interval!r}, expected {want['interval']!r}")
        if is_sub is not None and bool(is_sub) != want["subscription"]:
            kind = "a subscription" if is_sub else "a one-time purchase"
            expected_kind = "a subscription" if want["subscription"] else "a one-time purchase"
            warnings.append(f"this variant is {kind}, but {want['label']} must be {expected_kind}")

        checks.append(Check(
            f"{want['label']} variant", True,
            f"{a.get('name', '?')} — {_money(price)}"
            + (f" / {interval}" if interval else " one-time"),
            "Fix the variant in Lemon Squeezy, or update the paywall copy to match."
            if warnings else "",
            warnings,
        ))

    # 5. Webhook ---------------------------------------------------------------
    if store_id:
        r = client.get(f"{API_BASE}/webhooks", headers=_headers(api_key),
                       params={"filter[store_id]": store_id})
        if r.status_code != 200:
            checks.append(Check("Webhook configured", False, f"HTTP {r.status_code}",
                                "Check the API key's permissions."))
        else:
            hooks = r.json().get("data", [])
            want_url = f"{base_url.rstrip('/')}/api/billing/webhook" if base_url else None
            match = next((h for h in hooks
                          if h.get("attributes", {}).get("url") == want_url), None)
            if not hooks:
                checks.append(Check(
                    "Webhook configured", False, "no webhooks on this store",
                    f"Add one at Settings → Webhooks pointing to {want_url or '<your-url>/api/billing/webhook'} "
                    f"with events: {', '.join(sorted(REQUIRED_EVENTS))}."))
            elif not match:
                urls = ", ".join(h.get("attributes", {}).get("url", "?") for h in hooks)
                checks.append(Check(
                    "Webhook points at this deployment", False,
                    f"found {urls}, but CARDVAULT_BASE_URL implies {want_url}",
                    "Either fix the webhook URL in Lemon Squeezy or fix CARDVAULT_BASE_URL. "
                    "If they disagree, upgrades will never arrive."))
            else:
                events = set(match.get("attributes", {}).get("events", []))
                lacking = REQUIRED_EVENTS - events
                checks.append(Check(
                    "Webhook configured", not lacking,
                    f"{want_url} → events: {', '.join(sorted(events)) or 'none'}",
                    f"Add the missing events: {', '.join(sorted(lacking))}." if lacking else "",
                ))

    return checks


def list_catalog(client: httpx.Client | None = None) -> list[str]:
    """Print every store, product and variant with its ID.

    Hunting for numeric IDs in the dashboard is the fiddliest part of the
    setup, so ask the API instead and copy the numbers from here.
    """
    owns = client is None
    client = client or httpx.Client(timeout=20)
    api_key = os.environ.get("LEMONSQUEEZY_API_KEY", "")
    lines: list[str] = []
    try:
        if not api_key:
            return ["LEMONSQUEEZY_API_KEY is not set — create one at Settings → API."]
        stores = client.get(f"{API_BASE}/stores", headers=_headers(api_key))
        if stores.status_code != 200:
            return [f"Could not list stores (HTTP {stores.status_code}); check the API key."]
        for store in stores.json().get("data", []):
            sid = store.get("id")
            lines.append(f"Store {sid}: {store.get('attributes', {}).get('name', '?')}")
            lines.append(f"  → LEMONSQUEEZY_STORE_ID={sid}")
            prods = client.get(f"{API_BASE}/products", headers=_headers(api_key),
                               params={"filter[store_id]": sid})
            for prod in prods.json().get("data", []) if prods.status_code == 200 else []:
                lines.append(f"  Product {prod.get('id')}: "
                             f"{prod.get('attributes', {}).get('name', '?')}")
                variants = client.get(f"{API_BASE}/variants", headers=_headers(api_key),
                                      params={"filter[product_id]": prod.get("id")})
                for v in variants.json().get("data", []) if variants.status_code == 200 else []:
                    a = v.get("attributes", {})
                    period = f"/{a['interval']}" if a.get("interval") else " one-time"
                    hint = ""
                    for env_key, want in EXPECTED.items():
                        if a.get("price") == want["price"] and a.get("interval") == want["interval"]:
                            hint = f"   ← looks like {env_key}={v.get('id')}"
                    lines.append(f"    Variant {v.get('id')}: {a.get('name', '?')} "
                                 f"{_money(a.get('price'))}{period}{hint}")
    except httpx.HTTPError as exc:
        lines.append(f"Could not reach Lemon Squeezy: {exc}")
    finally:
        if owns:
            client.close()
    return lines


def main() -> int:
    if "--list" in sys.argv:
        print("\nYour Lemon Squeezy catalog\n" + "=" * 26)
        for line in list_catalog():
            print(line)
        print()
        return 0

    checks = run_checks()
    print("\nCardVault billing configuration\n" + "=" * 34)
    failed = 0
    for c in checks:
        mark = "OK  " if c.ok else "FAIL"
        if c.ok and c.warnings:
            mark = "WARN"
        print(f"[{mark}] {c.name}" + (f": {c.detail}" if c.detail else ""))
        for w in c.warnings:
            print(f"       ! {w}")
        if c.fix and (not c.ok or c.warnings):
            print(f"       → {c.fix}")
        if not c.ok:
            failed += 1
    print()
    if failed:
        print(f"{failed} problem(s) to fix before taking payments.")
    else:
        print("Configuration looks correct. Run a test-mode purchase to confirm end to end.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
