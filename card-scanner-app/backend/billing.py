"""Stripe paywall integration.

Configured via environment variables:
  STRIPE_SECRET_KEY      — sk_test_... / sk_live_...
  STRIPE_PRICE_ID        — recurring Price for the Pro plan
  STRIPE_WEBHOOK_SECRET  — whsec_... for webhook signature verification
  CARDVAULT_BASE_URL     — public URL for Checkout redirects (default http://localhost:8000)

If Stripe is not configured, checkout endpoints return 503 so the rest of
the app (free tier) keeps working in development.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import stripe
except ImportError:  # pragma: no cover - stripe is in requirements.txt
    stripe = None  # type: ignore[assignment]

from . import database


def _base_url() -> str:
    return os.environ.get("CARDVAULT_BASE_URL", "http://localhost:8000").rstrip("/")


def is_configured() -> bool:
    return bool(
        stripe
        and os.environ.get("STRIPE_SECRET_KEY")
        and os.environ.get("STRIPE_PRICE_ID")
    )


def create_checkout_session(user: dict[str, Any]) -> str:
    """Create a Stripe Checkout session for the Pro subscription; returns its URL."""
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": os.environ["STRIPE_PRICE_ID"], "quantity": 1}],
        customer_email=user["email"],
        client_reference_id=str(user["id"]),
        success_url=f"{_base_url()}/?upgraded=1",
        cancel_url=f"{_base_url()}/?canceled=1",
    )
    return session.url


def create_portal_session(customer_id: str) -> str:
    """Create a Stripe customer portal session (self-serve cancel/update card)."""
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{_base_url()}/",
    )
    return session.url


def handle_webhook(payload: bytes, signature: str) -> dict[str, str]:
    """Verify and process a Stripe webhook. Raises ValueError on bad signature."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET is not set")
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:
        raise ValueError(f"invalid webhook: {exc}") from exc

    obj = event["data"]["object"]
    if event["type"] == "checkout.session.completed":
        user_id = int(obj["client_reference_id"])
        database.set_plan(user_id, "pro", stripe_customer_id=obj.get("customer"))
        return {"status": "upgraded", "user_id": str(user_id)}
    if event["type"] in ("customer.subscription.deleted", "customer.subscription.paused"):
        user = database.get_user_by_stripe_customer(obj.get("customer", ""))
        if user:
            database.set_plan(user["id"], "free")
            return {"status": "downgraded", "user_id": str(user["id"])}
    return {"status": "ignored"}
