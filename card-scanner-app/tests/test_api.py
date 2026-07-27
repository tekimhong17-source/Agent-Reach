"""API tests for CardVault: auth, encrypted vault, paywall enforcement.

Run from card-scanner-app/:  pytest tests/ -v
"""

import importlib
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CARDVAULT_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("CARDVAULT_DEV", "1")
    monkeypatch.setenv("CARDVAULT_FREE_LIMIT", "2")
    import backend.database as database
    import backend.main as main
    importlib.reload(database)
    importlib.reload(main)
    return TestClient(main.app)


def register(client, email="user@example.com", password="hunter2secure"):
    res = client.post("/api/register", json={"email": email, "password": password})
    assert res.status_code == 201, res.text
    return res.json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


ENCRYPTED_CARD = {
    "label": "Personal Visa",
    "brand": "Visa",
    "last4": "4242",
    "ciphertext": "b64ciphertextplaceholder==",
    "iv": "b64iv==",
    "salt": "b64salt==",
}


# ---------- auth ----------

def test_register_and_me(client):
    token = register(client)
    res = client.get("/api/me", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "user@example.com"
    assert body["plan"] == "free"
    assert body["cards"] == 0


def test_register_duplicate_email(client):
    register(client)
    res = client.post(
        "/api/register", json={"email": "user@example.com", "password": "hunter2secure"}
    )
    assert res.status_code == 409


def test_login_wrong_password(client):
    register(client)
    res = client.post(
        "/api/login", json={"email": "user@example.com", "password": "wrongpassword"}
    )
    assert res.status_code == 401


def test_login_success(client):
    register(client)
    res = client.post(
        "/api/login", json={"email": "user@example.com", "password": "hunter2secure"}
    )
    assert res.status_code == 200
    assert "token" in res.json()


def test_short_password_rejected(client):
    res = client.post("/api/register", json={"email": "a@b.com", "password": "short"})
    assert res.status_code == 422


def test_unauthenticated_access_rejected(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/cards").status_code == 401
    assert client.post("/api/cards", json=ENCRYPTED_CARD).status_code == 401


def test_logout_invalidates_session(client):
    token = register(client)
    client.post("/api/logout", headers=auth(token))
    assert client.get("/api/me", headers=auth(token)).status_code == 401


# ---------- vault ----------

def test_add_and_list_card(client):
    token = register(client)
    res = client.post("/api/cards", json=ENCRYPTED_CARD, headers=auth(token))
    assert res.status_code == 201
    cards = client.get("/api/cards", headers=auth(token)).json()
    assert len(cards) == 1
    assert cards[0]["label"] == "Personal Visa"
    assert cards[0]["last4"] == "4242"
    assert cards[0]["ciphertext"] == ENCRYPTED_CARD["ciphertext"]


def test_delete_card(client):
    token = register(client)
    card_id = client.post("/api/cards", json=ENCRYPTED_CARD, headers=auth(token)).json()["id"]
    assert client.delete(f"/api/cards/{card_id}", headers=auth(token)).status_code == 200
    assert client.get("/api/cards", headers=auth(token)).json() == []


def test_cannot_delete_other_users_card(client):
    token_a = register(client, "a@example.com")
    token_b = register(client, "b@example.com")
    card_id = client.post("/api/cards", json=ENCRYPTED_CARD, headers=auth(token_a)).json()["id"]
    assert client.delete(f"/api/cards/{card_id}", headers=auth(token_b)).status_code == 404


def test_cards_are_isolated_per_user(client):
    token_a = register(client, "a@example.com")
    token_b = register(client, "b@example.com")
    client.post("/api/cards", json=ENCRYPTED_CARD, headers=auth(token_a))
    assert client.get("/api/cards", headers=auth(token_b)).json() == []


def test_plaintext_pan_in_label_rejected(client):
    token = register(client)
    bad = dict(ENCRYPTED_CARD, label="4111 1111 1111 1111")
    res = client.post("/api/cards", json=bad, headers=auth(token))
    assert res.status_code == 422
    assert "card number" in res.json()["detail"]


def test_invalid_last4_rejected(client):
    token = register(client)
    bad = dict(ENCRYPTED_CARD, last4="42424")
    assert client.post("/api/cards", json=bad, headers=auth(token)).status_code == 422


# ---------- paywall ----------

def test_free_plan_limit_enforced(client):
    token = register(client)
    for i in range(2):
        res = client.post(
            "/api/cards", json=dict(ENCRYPTED_CARD, label=f"Card {i}"), headers=auth(token)
        )
        assert res.status_code == 201
    res = client.post(
        "/api/cards", json=dict(ENCRYPTED_CARD, label="One too many"), headers=auth(token)
    )
    assert res.status_code == 402
    assert "Upgrade" in res.json()["detail"]


def test_pro_plan_bypasses_limit(client):
    token = register(client)
    assert client.post("/api/billing/dev-upgrade", headers=auth(token)).status_code == 200
    for i in range(5):
        res = client.post(
            "/api/cards", json=dict(ENCRYPTED_CARD, label=f"Card {i}"), headers=auth(token)
        )
        assert res.status_code == 201
    assert client.get("/api/me", headers=auth(token)).json()["plan"] == "pro"


def test_checkout_unconfigured_returns_503(client):
    token = register(client)
    res = client.post("/api/billing/checkout", headers=auth(token))
    assert res.status_code == 503


def _configure_billing(monkeypatch):
    monkeypatch.setenv("LEMONSQUEEZY_API_KEY", "lsq_test_dummy")
    monkeypatch.setenv("LEMONSQUEEZY_STORE_ID", "1")
    monkeypatch.setenv("LEMONSQUEEZY_VARIANT_ID", "1")
    monkeypatch.setenv("LEMONSQUEEZY_ANNUAL_VARIANT_ID", "12")
    monkeypatch.setenv("LEMONSQUEEZY_LIFETIME_VARIANT_ID", "99")
    monkeypatch.setenv("LEMONSQUEEZY_WEBHOOK_SECRET", "whsecret")


def _signed_webhook(client, event: dict):
    import hashlib
    import hmac as hmac_mod

    payload = json.dumps(event).encode()
    signature = hmac_mod.new(b"whsecret", payload, hashlib.sha256).hexdigest()
    return client.post(
        "/api/billing/webhook", content=payload, headers={"x-signature": signature}
    )


def test_portal_unconfigured_returns_503(client):
    token = register(client)
    res = client.post("/api/billing/portal", headers=auth(token))
    assert res.status_code == 503


def test_portal_without_billing_profile_returns_400(client, monkeypatch):
    _configure_billing(monkeypatch)
    token = register(client)
    res = client.post("/api/billing/portal", headers=auth(token))
    assert res.status_code == 400
    assert "billing profile" in res.json()["detail"]


def test_webhook_without_secret_rejected(client):
    res = client.post(
        "/api/billing/webhook", content=b"{}", headers={"x-signature": "bogus"}
    )
    assert res.status_code == 400


def test_webhook_bad_signature_rejected(client, monkeypatch):
    _configure_billing(monkeypatch)
    res = client.post(
        "/api/billing/webhook", content=b"{}", headers={"x-signature": "deadbeef"}
    )
    assert res.status_code == 400
    assert "signature" in res.json()["detail"]


def test_webhook_subscription_lifecycle(client, monkeypatch):
    _configure_billing(monkeypatch)
    token = register(client)

    # subscription_created with our user_id in custom_data → Pro
    res = _signed_webhook(client, {
        "meta": {"event_name": "subscription_created", "custom_data": {"user_id": "1"}},
        "data": {"type": "subscriptions", "id": "sub_42", "attributes": {}},
    })
    assert res.status_code == 200 and res.json()["status"] == "upgraded"
    assert client.get("/api/me", headers=auth(token)).json()["plan"] == "pro"

    # subscription_cancelled is ignored: paid through the period, access stays
    res = _signed_webhook(client, {
        "meta": {"event_name": "subscription_cancelled"},
        "data": {"type": "subscriptions", "id": "sub_42", "attributes": {}},
    })
    assert res.json()["status"] == "ignored"
    assert client.get("/api/me", headers=auth(token)).json()["plan"] == "pro"

    # subscription_expired at period end → back to free
    res = _signed_webhook(client, {
        "meta": {"event_name": "subscription_expired"},
        "data": {"type": "subscriptions", "id": "sub_42", "attributes": {}},
    })
    assert res.json()["status"] == "downgraded"
    assert client.get("/api/me", headers=auth(token)).json()["plan"] == "free"


def test_lifetime_order_grants_lifetime_plan(client, monkeypatch):
    _configure_billing(monkeypatch)
    token = register(client)

    res = _signed_webhook(client, {
        "meta": {"event_name": "order_created", "custom_data": {"user_id": "1"}},
        "data": {
            "type": "orders", "id": "777",
            "attributes": {"first_order_item": {"variant_id": 99}},
        },
    })
    assert res.status_code == 200 and res.json()["status"] == "lifetime"
    assert client.get("/api/me", headers=auth(token)).json()["plan"] == "lifetime"

    # lifetime bypasses the free limit
    for i in range(3):
        res = client.post(
            "/api/cards", json=dict(ENCRYPTED_CARD, label=f"Card {i}"), headers=auth(token)
        )
        assert res.status_code == 201


def test_subscription_first_invoice_order_is_ignored(client, monkeypatch):
    _configure_billing(monkeypatch)
    token = register(client)

    res = _signed_webhook(client, {
        "meta": {"event_name": "order_created", "custom_data": {"user_id": "1"}},
        "data": {
            "type": "orders", "id": "778",
            "attributes": {"first_order_item": {"variant_id": 1}},
        },
    })
    assert res.json()["status"] == "ignored"
    assert client.get("/api/me", headers=auth(token)).json()["plan"] == "free"


def test_checkout_invalid_tier_rejected(client):
    token = register(client)
    res = client.post(
        "/api/billing/checkout", json={"tier": "weekly"}, headers=auth(token)
    )
    assert res.status_code == 422


@pytest.mark.parametrize(
    "tier,env_key",
    [
        ("yearly", "LEMONSQUEEZY_ANNUAL_VARIANT_ID"),
        ("monthly", "LEMONSQUEEZY_VARIANT_ID"),
        ("lifetime", "LEMONSQUEEZY_LIFETIME_VARIANT_ID"),
    ],
)
def test_checkout_reports_the_unconfigured_tier(client, monkeypatch, tier, env_key):
    """Each plan names the exact env var it needs, rather than failing vaguely."""
    _configure_billing(monkeypatch)
    monkeypatch.delenv(env_key, raising=False)
    if env_key != "LEMONSQUEEZY_VARIANT_ID":
        monkeypatch.setenv("LEMONSQUEEZY_VARIANT_ID", "1")  # keep billing "configured"
    token = register(client)
    res = client.post("/api/billing/checkout", json={"tier": tier}, headers=auth(token))
    assert res.status_code == 503
    assert env_key in res.json()["detail"]


def test_upstream_failure_becomes_502_not_500(client, monkeypatch):
    """A Lemon Squeezy outage must not surface as a raw 500 to a buyer."""
    import backend.billing as billing_mod
    import backend.main as main_mod

    _configure_billing(monkeypatch)
    token = register(client)

    def boom(*_a, **_kw):
        raise billing_mod.BillingUnavailable("connection refused")

    monkeypatch.setattr(main_mod.billing, "create_checkout_session", boom)
    res = client.post("/api/billing/checkout", json={"tier": "yearly"}, headers=auth(token))
    assert res.status_code == 502
    assert "Nothing was charged" in res.json()["detail"]


def test_checkout_defaults_to_yearly(client, monkeypatch):
    """No body at all must mean the annual plan, not the monthly one."""
    _configure_billing(monkeypatch)
    monkeypatch.delenv("LEMONSQUEEZY_ANNUAL_VARIANT_ID", raising=False)
    token = register(client)
    res = client.post("/api/billing/checkout", headers=auth(token))
    assert res.status_code == 503
    assert "yearly" in res.json()["detail"]
