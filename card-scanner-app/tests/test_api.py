"""API tests for CardVault: auth, encrypted vault, paywall enforcement.

Run from card-scanner-app/:  pytest tests/ -v
"""

import importlib
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


def test_portal_unconfigured_returns_503(client):
    token = register(client)
    res = client.post("/api/billing/portal", headers=auth(token))
    assert res.status_code == 503


def test_portal_without_billing_profile_returns_400(client, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_dummy")
    token = register(client)
    res = client.post("/api/billing/portal", headers=auth(token))
    assert res.status_code == 400
    assert "billing profile" in res.json()["detail"]


def test_webhook_without_secret_rejected(client):
    res = client.post(
        "/api/billing/webhook", content=b"{}", headers={"stripe-signature": "bogus"}
    )
    assert res.status_code == 400
