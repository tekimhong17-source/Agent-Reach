"""Tests for the Gumroad configuration checker.

All Gumroad calls are stubbed, so these run offline.
"""

import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import check_billing  # noqa: E402

GOOD_PRODUCTS = [
    {"name": "CardVault Pro Yearly", "price": 1900, "is_recurring_billing": True,
     "published": True, "short_url": "https://shop.gumroad.com/l/cv-yearly"},
    {"name": "CardVault Pro Monthly", "price": 299, "is_recurring_billing": True,
     "published": True, "short_url": "https://shop.gumroad.com/l/cv-monthly"},
    {"name": "CardVault Lifetime", "price": 3900, "is_recurring_billing": False,
     "published": True, "short_url": "https://shop.gumroad.com/l/cv-lifetime"},
]

WEBHOOK_URL = "https://trycardvault.com/api/billing/webhook?token=whsecret"


def make_client(products=None, subscriptions=None, user_status=200):
    products = GOOD_PRODUCTS if products is None else products
    if subscriptions is None:
        subscriptions = [{"post_url": WEBHOOK_URL}]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/user"):
            return httpx.Response(user_status, json={"success": user_status == 200,
                                                     "user": {"email": "seller@shop.com"}})
        if path.endswith("/products"):
            return httpx.Response(200, json={"success": True, "products": products})
        if path.endswith("/resource_subscriptions"):
            if request.method == "PUT":
                return httpx.Response(200, json={"success": True})
            return httpx.Response(200, json={"success": True,
                                             "resource_subscriptions": subscriptions})
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("GUMROAD_ACCESS_TOKEN", "token")
    monkeypatch.setenv("GUMROAD_WEBHOOK_SECRET", "whsecret")
    monkeypatch.setenv("CARDVAULT_BASE_URL", "https://trycardvault.com")
    monkeypatch.setenv("GUMROAD_YEARLY_URL", "https://shop.gumroad.com/l/cv-yearly")
    monkeypatch.setenv("GUMROAD_MONTHLY_URL", "https://shop.gumroad.com/l/cv-monthly")
    monkeypatch.setenv("GUMROAD_LIFETIME_URL", "https://shop.gumroad.com/l/cv-lifetime")


def results(client):
    return {c.name: c for c in check_billing.run_checks(client=client)}


def test_fully_correct_setup_passes(env):
    out = results(make_client())
    assert all(c.ok for c in out.values()), [c for c in out.values() if not c.ok]
    assert not any(c.warnings for c in out.values())
    assert "seller@shop.com" in out["Access token valid"].detail


def test_missing_env_is_reported_first(monkeypatch):
    for k in ("GUMROAD_ACCESS_TOKEN", "GUMROAD_WEBHOOK_SECRET", "CARDVAULT_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    out = check_billing.run_checks(client=make_client())
    assert out[0].name == "Environment variables set" and not out[0].ok
    assert "GUMROAD_ACCESS_TOKEN" in out[0].detail


def test_bad_access_token_stops_early(env):
    out = results(make_client(user_status=401))
    assert not out["Access token valid"].ok
    assert "GUMROAD_ACCESS_TOKEN" in out["Access token valid"].fix


def test_wrong_price_warns_without_failing(env):
    bad = [dict(GOOD_PRODUCTS[0], price=2900)] + GOOD_PRODUCTS[1:]
    out = results(make_client(products=bad))
    check = out["Yearly plan product"]
    assert check.ok and check.warnings
    assert "$29.00" in check.warnings[0] and "$19.00" in check.warnings[0]


def test_product_in_another_currency_is_flagged(env):
    """A PHP-priced product looks like a huge price error; name the real cause."""
    bad = GOOD_PRODUCTS[:2] + [dict(GOOD_PRODUCTS[2], currency="php", price=240213)]
    out = results(make_client(products=bad))
    check = out["Lifetime plan product"]
    assert check.ok and check.warnings
    assert "priced in PHP" in check.warnings[0]
    assert "$39.00" in check.warnings[0]
    # the confusing raw-price complaint must not also fire
    assert not any("price is" in w for w in check.warnings)


def test_lifetime_sold_as_subscription_is_flagged(env):
    bad = GOOD_PRODUCTS[:2] + [dict(GOOD_PRODUCTS[2], is_recurring_billing=True)]
    out = results(make_client(products=bad))
    assert any("one-time purchase" in w for w in out["Lifetime plan product"].warnings)


def test_unpublished_product_is_flagged(env):
    bad = [dict(GOOD_PRODUCTS[0], published=False)] + GOOD_PRODUCTS[1:]
    out = results(make_client(products=bad))
    assert any("unpublished" in w for w in out["Yearly plan product"].warnings)


def test_product_url_not_in_account_fails(env, monkeypatch):
    monkeypatch.setenv("GUMROAD_YEARLY_URL", "https://shop.gumroad.com/l/typo")
    out = results(make_client())
    assert not out["Yearly plan product"].ok
    assert "GUMROAD_YEARLY_URL" in out["Yearly plan product"].fix


def test_missing_lifetime_is_optional(env, monkeypatch):
    monkeypatch.delenv("GUMROAD_LIFETIME_URL", raising=False)
    out = results(make_client())
    assert out["Lifetime plan product"].ok
    assert "optional" in out["Lifetime plan product"].detail


def test_webhook_pointing_at_another_host_fails(env):
    out = results(make_client(subscriptions=[
        {"post_url": "https://cardvault-heh1.onrender.com/api/billing/webhook?token=whsecret"}]))
    check = out["Webhook points at this deployment"]
    assert not check.ok
    assert "purchases will never grant access" in check.fix


def test_no_webhook_registered_fails(env):
    out = results(make_client(subscriptions=[]))
    check = out["Sale webhook registered"]
    assert not check.ok
    assert "--register-webhook" in check.fix


def test_register_webhook_covers_sale_and_ending(env):
    ok, out = check_billing.register_webhook(client=make_client())
    assert ok
    assert WEBHOOK_URL in out
    for resource in ("sale", "cancellation", "subscription_ended"):
        assert f"{resource}: registered" in out


def test_register_webhook_needs_config(monkeypatch):
    monkeypatch.delenv("GUMROAD_ACCESS_TOKEN", raising=False)
    ok, out = check_billing.register_webhook(client=make_client())
    assert not ok
    assert "GUMROAD_ACCESS_TOKEN" in out


def test_partial_registration_is_a_failure(env):
    """sale registered but subscription_ended not = purchases upgrade, nothing
    ever downgrades. That must not look like success."""
    calls = {"n": 0}

    def handler(request):
        p = request.url.path
        if p.endswith("/resource_subscriptions") and request.method == "PUT":
            calls["n"] += 1
            # first resource succeeds, the rest fail
            if calls["n"] == 1:
                return httpx.Response(200, json={"success": True})
            return httpx.Response(500, json={"success": False})
        return httpx.Response(404, json={})

    ok, out = check_billing.register_webhook(
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert not ok, "a partial registration must not report success"
    assert "sale: registered" in out
    assert "FAILED" in out


def test_register_webhook_unreachable_is_a_failure(env):
    def handler(request):
        raise httpx.ConnectError("blocked")

    ok, out = check_billing.register_webhook(
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert not ok
    assert "Could not reach Gumroad" in out


def test_catalog_lists_products_and_suggests_env_vars(env):
    ok, lines = check_billing.list_catalog(client=make_client())
    assert ok
    out = "\n".join(lines)
    assert "GUMROAD_YEARLY_URL=https://shop.gumroad.com/l/cv-yearly" in out
    assert "GUMROAD_MONTHLY_URL=https://shop.gumroad.com/l/cv-monthly" in out
    assert "GUMROAD_LIFETIME_URL=https://shop.gumroad.com/l/cv-lifetime" in out
    assert "CardVault Pro Yearly — $19.00 recurring" in out


def test_catalog_without_token_says_so(monkeypatch):
    monkeypatch.delenv("GUMROAD_ACCESS_TOKEN", raising=False)
    ok, lines = check_billing.list_catalog(client=make_client())
    assert not ok, "no token is a failure, not an empty success"
    assert "Settings → Advanced" in lines[0]


def test_catalog_unreachable_is_a_failure(env):
    def handler(request):
        raise httpx.ConnectError("blocked")

    ok, lines = check_billing.list_catalog(
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert not ok
    assert any("Could not reach Gumroad" in l for l in lines)


def test_main_exit_codes(env, monkeypatch, capsys):
    """The CLI's exit code is its machine-readable half — it must not lie."""
    monkeypatch.setattr(check_billing, "list_catalog", lambda: (False, ["nope"]))
    monkeypatch.setattr(sys, "argv", ["check_billing", "--list"])
    assert check_billing.main() == 1

    monkeypatch.setattr(check_billing, "list_catalog", lambda: (True, ["fine"]))
    assert check_billing.main() == 0

    monkeypatch.setattr(sys, "argv", ["check_billing", "--register-webhook"])
    monkeypatch.setattr(check_billing, "register_webhook", lambda: (False, "nope"))
    assert check_billing.main() == 1
    monkeypatch.setattr(check_billing, "register_webhook", lambda: (True, "done"))
    assert check_billing.main() == 0
