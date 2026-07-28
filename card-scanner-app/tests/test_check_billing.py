"""Tests for the billing configuration checker.

All Lemon Squeezy calls are stubbed, so these run offline.
"""

import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import check_billing  # noqa: E402

GOOD_VARIANTS = {
    "12": {"name": "Pro Yearly", "price": 1900, "interval": "year", "is_subscription": True},
    "1": {"name": "Pro Monthly", "price": 299, "interval": "month", "is_subscription": True},
    "99": {"name": "Pro Lifetime", "price": 3900, "interval": None, "is_subscription": False},
}


def make_client(variants=None, webhooks=None, api_status=200):
    variants = GOOD_VARIANTS if variants is None else variants
    if webhooks is None:
        webhooks = [{"attributes": {
            "url": "https://trycardvault.com/api/billing/webhook",
            "events": ["subscription_created", "subscription_expired", "order_created"],
        }}]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/users/me"):
            return httpx.Response(api_status, json={"data": {"attributes": {"email": "me@shop.com"}}})
        if "/stores/" in path:
            return httpx.Response(200, json={"data": {"attributes": {"name": "CardVault", "slug": "cardvault"}}})
        if "/variants/" in path:
            vid = path.rsplit("/", 1)[-1]
            if vid not in variants:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"data": {"attributes": variants[vid]}})
        if path.endswith("/webhooks"):
            return httpx.Response(200, json={"data": webhooks})
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("LEMONSQUEEZY_API_KEY", "key")
    monkeypatch.setenv("LEMONSQUEEZY_STORE_ID", "7")
    monkeypatch.setenv("LEMONSQUEEZY_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("CARDVAULT_BASE_URL", "https://trycardvault.com")
    monkeypatch.setenv("LEMONSQUEEZY_ANNUAL_VARIANT_ID", "12")
    monkeypatch.setenv("LEMONSQUEEZY_VARIANT_ID", "1")
    monkeypatch.setenv("LEMONSQUEEZY_LIFETIME_VARIANT_ID", "99")


def results(client):
    return {c.name: c for c in check_billing.run_checks(client=client)}


def test_fully_correct_setup_passes(env):
    out = results(make_client())
    assert all(c.ok for c in out.values()), [c for c in out.values() if not c.ok]
    assert not any(c.warnings for c in out.values())
    assert "me@shop.com" in out["API key valid"].detail


def test_missing_env_is_reported_first(monkeypatch):
    for k in ("LEMONSQUEEZY_API_KEY", "LEMONSQUEEZY_STORE_ID",
              "LEMONSQUEEZY_WEBHOOK_SECRET", "CARDVAULT_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    out = check_billing.run_checks(client=make_client())
    assert out[0].name == "Environment variables set" and not out[0].ok
    assert "LEMONSQUEEZY_API_KEY" in out[0].detail


def test_bad_api_key_stops_early(env):
    out = results(make_client(api_status=401))
    assert not out["API key valid"].ok
    assert "Settings → API" in out["API key valid"].fix


def test_wrong_price_warns_without_failing(env):
    bad = dict(GOOD_VARIANTS)
    bad["12"] = dict(GOOD_VARIANTS["12"], price=2900)
    out = results(make_client(variants=bad))
    check = out["Yearly plan variant"]
    assert check.ok and check.warnings
    assert "$29.00" in check.warnings[0] and "$19.00" in check.warnings[0]


def test_monthly_variant_billed_yearly_is_flagged(env):
    bad = dict(GOOD_VARIANTS)
    bad["1"] = dict(GOOD_VARIANTS["1"], interval="year")
    out = results(make_client(variants=bad))
    assert "billing period is 'year'" in out["Monthly plan variant"].warnings[0]


def test_lifetime_configured_as_subscription_is_flagged(env):
    bad = dict(GOOD_VARIANTS)
    bad["99"] = dict(GOOD_VARIANTS["99"], is_subscription=True, interval="month")
    out = results(make_client(variants=bad))
    assert any("one-time purchase" in w for w in out["Lifetime plan variant"].warnings)


def test_unknown_variant_id_fails(env, monkeypatch):
    monkeypatch.setenv("LEMONSQUEEZY_ANNUAL_VARIANT_ID", "404")
    out = results(make_client())
    assert not out["Yearly plan variant"].ok
    assert "LEMONSQUEEZY_ANNUAL_VARIANT_ID" in out["Yearly plan variant"].fix


def test_missing_lifetime_is_optional(env, monkeypatch):
    monkeypatch.delenv("LEMONSQUEEZY_LIFETIME_VARIANT_ID", raising=False)
    out = results(make_client())
    assert out["Lifetime plan variant"].ok
    assert "optional" in out["Lifetime plan variant"].detail


def test_webhook_pointing_at_another_host_fails(env):
    out = results(make_client(webhooks=[{"attributes": {
        "url": "https://old-deployment.onrender.com/api/billing/webhook",
        "events": sorted(check_billing.REQUIRED_EVENTS)}}]))
    check = out["Webhook points at this deployment"]
    assert not check.ok
    assert "upgrades will never arrive" in check.fix


def test_missing_webhook_events_fails(env):
    out = results(make_client(webhooks=[{"attributes": {
        "url": "https://trycardvault.com/api/billing/webhook",
        "events": ["subscription_created"]}}]))
    check = out["Webhook configured"]
    assert not check.ok
    assert "order_created" in check.fix and "subscription_expired" in check.fix


def test_catalog_lists_ids_and_suggests_env_vars(env):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/stores"):
            return httpx.Response(200, json={"data": [{"id": "7", "attributes": {"name": "CardVault"}}]})
        if p.endswith("/products"):
            return httpx.Response(200, json={"data": [{"id": "55", "attributes": {"name": "CardVault Pro"}}]})
        if p.endswith("/variants"):
            return httpx.Response(200, json={"data": [
                {"id": "12", "attributes": {"name": "Yearly", "price": 1900, "interval": "year"}},
                {"id": "1", "attributes": {"name": "Monthly", "price": 299, "interval": "month"}},
            ]})
        return httpx.Response(404, json={})

    out = "\n".join(check_billing.list_catalog(
        client=httpx.Client(transport=httpx.MockTransport(handler))))
    assert "LEMONSQUEEZY_STORE_ID=7" in out
    assert "Product 55: CardVault Pro" in out
    assert "LEMONSQUEEZY_ANNUAL_VARIANT_ID=12" in out
    assert "LEMONSQUEEZY_VARIANT_ID=1" in out


def test_catalog_without_api_key_says_so(monkeypatch):
    monkeypatch.delenv("LEMONSQUEEZY_API_KEY", raising=False)
    assert "Settings → API" in check_billing.list_catalog(client=make_client())[0]


def test_no_webhooks_at_all_fails(env):
    out = results(make_client(webhooks=[]))
    assert not out["Webhook configured"].ok
    assert "trycardvault.com/api/billing/webhook" in out["Webhook configured"].fix
