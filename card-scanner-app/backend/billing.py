"""Gumroad paywall integration.

Gumroad is the merchant of record: it collects payment, handles global sales
tax/VAT, and pays out (including to Philippine sellers, via PayPal).

Configured via environment variables:
  GUMROAD_ACCESS_TOKEN     — API access token (Settings → Advanced → API)
  GUMROAD_YEARLY_URL       — full product URL of the $19/year subscription
  GUMROAD_MONTHLY_URL      — full product URL of the $2.99/month subscription
  GUMROAD_LIFETIME_URL     — full product URL of the $39 one-time product
  GUMROAD_WEBHOOK_SECRET   — random string appended to the ping URL as ?token=
  CARDVAULT_BASE_URL       — public URL, used for the post-purchase redirect

Why full product URLs rather than IDs: you copy them straight from Gumroad,
so there are no numeric IDs to hunt for.

## Security model for the webhook

Gumroad's ping is a plain form POST and its authenticity guarantees are not
something this app relies on. Instead, every webhook is verified two ways:

  1. a shared secret in the query string (`?token=...`), compared in
     constant time — cheap rejection of random traffic; and
  2. **a callback to the Gumroad API** for the reported `sale_id`. Access is
     granted only if Gumroad itself confirms the sale exists and belongs to
     one of our products.

An attacker can forge a POST, but cannot forge a sale inside your Gumroad
account, so they cannot grant themselves Pro.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx

from . import database

API_BASE = "https://api.gumroad.com/v2"

logger = logging.getLogger(__name__)

# Which env var holds the product URL for each purchasable tier.
TIER_URL_ENV = {
    "yearly": "GUMROAD_YEARLY_URL",
    "monthly": "GUMROAD_MONTHLY_URL",
    "lifetime": "GUMROAD_LIFETIME_URL",
}


class BillingUnavailable(RuntimeError):
    """An upstream Gumroad call failed (network, auth, rate limit, 5xx).

    Raised so the API can answer 502 with something a buyer can act on,
    instead of leaking a 500 at the moment they are trying to pay.
    """


def _base_url() -> str:
    return os.environ.get("CARDVAULT_BASE_URL", "http://localhost:8000").rstrip("/")


def is_configured() -> bool:
    return bool(os.environ.get("GUMROAD_ACCESS_TOKEN"))


def permalink_of(product_url: str) -> str:
    """The short code at the end of a Gumroad product URL (…/l/<permalink>)."""
    return urlparse(product_url).path.rstrip("/").rsplit("/", 1)[-1]


def tier_for_permalink(permalink: str) -> str | None:
    """Map a purchased product back to the tier it grants."""
    for tier, env_key in TIER_URL_ENV.items():
        configured = os.environ.get(env_key)
        if configured and permalink_of(configured) == permalink:
            return tier
    return None


def create_checkout_session(user: dict[str, Any], tier: str = "yearly") -> str:
    """Build the Gumroad checkout URL for this user and plan.

    No API call is involved — Gumroad checkout is a URL — so this cannot fail
    upstream. `user_id` rides along in the query string and comes back to us
    in the webhook's `url_params`, which is how a payment is tied to an
    account.
    """
    product_url = os.environ.get(TIER_URL_ENV[tier])
    if not product_url:
        raise BillingUnavailable(f"{TIER_URL_ENV[tier]} is not set")
    params = {
        "user_id": str(user["id"]),
        "email": user["email"],
        "wanted": "true",  # skip the product page, go straight to checkout
    }
    joiner = "&" if "?" in product_url else "?"
    return f"{product_url}{joiner}{urlencode(params)}"


def create_portal_session(subscription_id: str) -> str:
    """Gumroad manages subscriptions from the buyer's receipt email.

    There is no server-created portal session to fetch, so this returns the
    generic subscription management page. The customer's own receipt contains
    a direct link to their subscription.
    """
    return "https://app.gumroad.com/library?tab=subscriptions"


def _api_get(path: str, **params: str) -> dict[str, Any]:
    token = os.environ.get("GUMROAD_ACCESS_TOKEN")
    if not token:
        raise BillingUnavailable("GUMROAD_ACCESS_TOKEN is not set")
    try:
        resp = httpx.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or None,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Gumroad API call to %s failed: %s", path, exc)
        raise BillingUnavailable(str(exc)) from exc


def verify_sale(sale_id: str) -> dict[str, Any] | None:
    """Ask Gumroad whether this sale is real. Returns the sale, or None."""
    data = _api_get(f"/sales/{sale_id}")
    if not data.get("success"):
        return None
    return data.get("sale") or None


def _form_value(form: dict[str, str], *names: str) -> str | None:
    for n in names:
        if form.get(n):
            return form[n]
    return None


def handle_webhook(form: dict[str, str], token: str) -> dict[str, str]:
    """Process a Gumroad ping. Raises ValueError if the request is not trusted.

    `form` is the parsed form body; `token` is the `?token=` query parameter.
    """
    secret = os.environ.get("GUMROAD_WEBHOOK_SECRET")
    if not secret:
        raise ValueError("GUMROAD_WEBHOOK_SECRET is not set")
    if not token or not hmac.compare_digest(token, secret):
        raise ValueError("invalid webhook token")

    # --- a purchase -------------------------------------------------------
    sale_id = _form_value(form, "sale_id", "id")
    if sale_id:
        sale = verify_sale(sale_id)
        if not sale:
            # The POST claimed a sale that Gumroad does not know about.
            logger.warning("Rejected webhook for unverifiable sale %s", sale_id)
            raise ValueError("sale could not be verified with Gumroad")

        permalink = (
            sale.get("product_permalink_short")
            or sale.get("permalink")
            or _form_value(form, "permalink", "product_permalink")
            or ""
        )
        if permalink.startswith("http"):
            permalink = permalink_of(permalink)
        tier = tier_for_permalink(permalink)
        if not tier:
            logger.info("Ignoring sale of unrelated product %r", permalink)
            return {"status": "ignored"}

        # user_id travels through the checkout URL and comes back here.
        raw_user = (
            _form_value(form, "url_params[user_id]")
            or (sale.get("url_params") or {}).get("user_id")
        )
        if not raw_user:
            logger.warning("Sale %s carried no user_id; cannot grant access", sale_id)
            return {"status": "no_user"}

        user_id = int(raw_user)
        if tier == "lifetime":
            database.set_plan(user_id, "lifetime")
            return {"status": "lifetime", "user_id": str(user_id)}

        subscription_id = _form_value(form, "subscription_id") or sale.get("subscription_id")
        database.set_plan(user_id, "pro", subscription_id=subscription_id)
        return {"status": "upgraded", "user_id": str(user_id)}

    # --- a subscription reaching its end ----------------------------------
    subscription_id = _form_value(form, "subscription_id")
    if subscription_id:
        # A cancellation is deliberately NOT a downgrade: the customer has
        # paid through the current period. Only an ended/failed subscription
        # drops them back to free.
        ended = _form_value(
            form, "subscription_ended_at", "ended_at", "subscription_failed_at", "failed_at"
        )
        if not ended:
            return {"status": "ignored"}
        user = database.get_user_by_subscription(subscription_id)
        if user:
            database.set_plan(user["id"], "free")
            return {"status": "downgraded", "user_id": str(user["id"])}

    return {"status": "ignored"}
