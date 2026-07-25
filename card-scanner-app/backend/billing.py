"""Lemon Squeezy paywall integration (merchant of record).

Configured via environment variables:
  LEMONSQUEEZY_API_KEY         — API key from Settings → API
  LEMONSQUEEZY_STORE_ID        — numeric store ID
  LEMONSQUEEZY_VARIANT_ID      — variant ID of the Pro subscription product
  LEMONSQUEEZY_WEBHOOK_SECRET  — signing secret set when creating the webhook
  CARDVAULT_BASE_URL           — public URL for post-checkout redirect
                                 (default http://localhost:8000)

If Lemon Squeezy is not configured, checkout endpoints return 503 so the
rest of the app (free tier) keeps working in development.

Webhook contract (Settings → Webhooks, point at /api/billing/webhook):
  subscription_created   → upgrade the user in meta.custom_data.user_id to Pro
  subscription_expired   → downgrade the owner of that subscription to free
  subscription_cancelled → ignored on purpose: the customer has paid through
                           the current period, so access continues until
                           subscription_expired fires at period end.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

import httpx

from . import database

API_BASE = "https://api.lemonsqueezy.com/v1"


def _base_url() -> str:
    return os.environ.get("CARDVAULT_BASE_URL", "http://localhost:8000").rstrip("/")


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Authorization": f"Bearer {os.environ['LEMONSQUEEZY_API_KEY']}",
    }


def is_configured() -> bool:
    return bool(
        os.environ.get("LEMONSQUEEZY_API_KEY")
        and os.environ.get("LEMONSQUEEZY_STORE_ID")
        and os.environ.get("LEMONSQUEEZY_VARIANT_ID")
    )


def create_checkout_session(user: dict[str, Any]) -> str:
    """Create a Lemon Squeezy checkout for the Pro subscription; returns its URL."""
    body = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "product_options": {"redirect_url": f"{_base_url()}/?upgraded=1"},
                "checkout_data": {
                    "email": user["email"],
                    "custom": {"user_id": str(user["id"])},
                },
            },
            "relationships": {
                "store": {
                    "data": {"type": "stores", "id": os.environ["LEMONSQUEEZY_STORE_ID"]}
                },
                "variant": {
                    "data": {"type": "variants", "id": os.environ["LEMONSQUEEZY_VARIANT_ID"]}
                },
            },
        }
    }
    resp = httpx.post(f"{API_BASE}/checkouts", json=body, headers=_headers(), timeout=20)
    resp.raise_for_status()
    return resp.json()["data"]["attributes"]["url"]


def create_portal_session(subscription_id: str) -> str:
    """Fetch the signed customer-portal URL for a subscription (valid ~24h)."""
    resp = httpx.get(
        f"{API_BASE}/subscriptions/{subscription_id}", headers=_headers(), timeout=20
    )
    resp.raise_for_status()
    url = resp.json()["data"]["attributes"]["urls"].get("customer_portal")
    if not url:
        raise ValueError("No customer portal available for this subscription")
    return url


def handle_webhook(payload: bytes, signature: str) -> dict[str, str]:
    """Verify and process a Lemon Squeezy webhook. Raises ValueError on bad signature."""
    secret = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET")
    if not secret:
        raise ValueError("LEMONSQUEEZY_WEBHOOK_SECRET is not set")
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature.strip()):
        raise ValueError("invalid webhook signature")

    event = json.loads(payload)
    name = event.get("meta", {}).get("event_name")

    if name == "subscription_created":
        user_id = int(event["meta"]["custom_data"]["user_id"])
        database.set_plan(user_id, "pro", subscription_id=str(event["data"]["id"]))
        return {"status": "upgraded", "user_id": str(user_id)}

    if name == "subscription_expired":
        user = database.get_user_by_subscription(str(event["data"]["id"]))
        if user:
            database.set_plan(user["id"], "free")
            return {"status": "downgraded", "user_id": str(user["id"])}

    return {"status": "ignored"}
