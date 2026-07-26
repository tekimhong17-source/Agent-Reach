"""CardVault API — auth, encrypted card vault, and subscription paywall.

Run with:  uvicorn backend.main:app --reload  (from card-scanner-app/)

Privacy model: the browser encrypts card data with a key derived from the
user's passphrase before upload. This API only accepts ciphertext plus
non-sensitive display fields (brand, last 4 digits). Endpoints reject
anything that looks like a full card number as a safety net.
"""

from __future__ import annotations

import os
import re
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from . import billing, database, security

FREE_CARD_LIMIT = int(os.environ.get("CARDVAULT_FREE_LIMIT", "2"))
PRO_PLANS = {"pro", "lifetime"}  # plans with unlimited cards
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = FastAPI(title="CardVault", version="1.0.0")
database.init_db()


# ---------- models ----------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class CheckoutRequest(BaseModel):
    tier: str = Field(default="subscription", pattern="^(subscription|lifetime)$")


class CardCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    brand: str = Field(min_length=1, max_length=32)
    last4: str = Field(pattern=r"^\d{4}$")
    ciphertext: str = Field(min_length=1, max_length=16384)
    iv: str = Field(min_length=1, max_length=128)
    salt: str = Field(min_length=1, max_length=128)


# ---------- auth dependency ----------

def current_user(authorization: str = Header(default="")) -> dict[str, Any]:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    user = database.get_session_user(authorization.removeprefix("Bearer "))
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


# ---------- auth routes ----------

@app.post("/api/register", status_code=201)
def register(body: RegisterRequest) -> dict[str, str]:
    if database.get_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    user_id = database.create_user(body.email, security.hash_password(body.password))
    token = security.new_session_token()
    database.create_session(token, user_id)
    return {"token": token, "plan": "free"}


@app.post("/api/login")
def login(body: LoginRequest) -> dict[str, str]:
    user = database.get_user_by_email(body.email)
    if not user or not security.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = security.new_session_token()
    database.create_session(token, user["id"])
    return {"token": token, "plan": user["plan"]}


@app.post("/api/logout")
def logout(authorization: str = Header(default="")) -> dict[str, str]:
    if authorization.startswith("Bearer "):
        database.delete_session(authorization.removeprefix("Bearer "))
    return {"status": "ok"}


@app.get("/api/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {
        "email": user["email"],
        "plan": user["plan"],
        "cards": database.count_cards(user["id"]),
        "free_limit": FREE_CARD_LIMIT,
    }


# ---------- vault routes ----------

_PAN_PATTERN = re.compile(r"(?:\d[ -]?){13,19}")


def _looks_like_pan(value: str) -> bool:
    return bool(_PAN_PATTERN.search(value))


@app.post("/api/cards", status_code=201)
def create_card(body: CardCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    # Safety net: refuse plaintext card numbers in any display field.
    if _looks_like_pan(body.label) or _looks_like_pan(body.brand):
        raise HTTPException(
            status_code=422,
            detail="Field appears to contain a full card number; only encrypted data is accepted",
        )
    if user["plan"] not in PRO_PLANS and database.count_cards(user["id"]) >= FREE_CARD_LIMIT:
        raise HTTPException(
            status_code=402,
            detail=f"Free plan is limited to {FREE_CARD_LIMIT} cards. Upgrade to Pro for unlimited cards.",
        )
    card_id = database.add_card(
        user["id"], body.label, body.brand, body.last4, body.ciphertext, body.iv, body.salt
    )
    return {"id": card_id}


@app.get("/api/cards")
def get_cards(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return [
        {
            "id": c["id"],
            "label": c["label"],
            "brand": c["brand"],
            "last4": c["last4"],
            "ciphertext": c["ciphertext"],
            "iv": c["iv"],
            "salt": c["salt"],
        }
        for c in database.list_cards(user["id"])
    ]


@app.delete("/api/cards/{card_id}")
def remove_card(card_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
    if not database.delete_card(user["id"], card_id):
        raise HTTPException(status_code=404, detail="Card not found")
    return {"status": "deleted"}


# ---------- billing routes ----------

@app.post("/api/billing/checkout")
def checkout(
    body: CheckoutRequest | None = None, user: dict[str, Any] = Depends(current_user)
) -> dict[str, str]:
    tier = body.tier if body else "subscription"
    if user["plan"] in PRO_PLANS:
        raise HTTPException(status_code=400, detail="Already on Pro")
    if not billing.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Billing is not configured (set LEMONSQUEEZY_API_KEY, "
            "LEMONSQUEEZY_STORE_ID and LEMONSQUEEZY_VARIANT_ID)",
        )
    variant = None
    if tier == "lifetime":
        variant = os.environ.get("LEMONSQUEEZY_LIFETIME_VARIANT_ID")
        if not variant:
            raise HTTPException(
                status_code=503,
                detail="Lifetime tier is not configured (set LEMONSQUEEZY_LIFETIME_VARIANT_ID)",
            )
    return {"url": billing.create_checkout_session(user, variant_id=variant)}


@app.post("/api/billing/portal")
def billing_portal(user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
    if not billing.is_configured():
        raise HTTPException(status_code=503, detail="Billing is not configured")
    if not user.get("subscription_id"):
        raise HTTPException(status_code=400, detail="No billing profile for this account")
    return {"url": billing.create_portal_session(user["subscription_id"])}


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request) -> dict[str, str]:
    payload = await request.body()
    signature = request.headers.get("x-signature", "")
    try:
        return billing.handle_webhook(payload, signature)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# Dev-only shortcut so the paywall can be exercised without billing keys.
if os.environ.get("CARDVAULT_DEV") == "1":

    @app.post("/api/billing/dev-upgrade")
    def dev_upgrade(user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
        database.set_plan(user["id"], "pro")
        return {"status": "upgraded"}


# ---------- frontend ----------

if os.path.isdir(FRONTEND_DIR):

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
