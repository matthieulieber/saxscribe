from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import settings


class BillingError(RuntimeError):
    """Raised when hosted billing is unavailable or a payment is invalid."""


@dataclass(frozen=True)
class PaidCheckout:
    session_id: str
    payment_intent_id: str | None
    amount_total: int
    currency: str
    customer_email: str | None

    def to_record(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "payment_intent_id": self.payment_intent_id,
            "amount_total": self.amount_total,
            "currency": self.currency,
            "customer_email": self.customer_email,
        }


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _stripe():
    if not settings.stripe_secret_key:
        raise BillingError("Hosted payments are not configured.")
    try:
        import stripe
    except ImportError as exc:
        raise BillingError("The Stripe server package is not installed.") from exc
    stripe.api_key = settings.stripe_secret_key
    return stripe


def hosted_enhanced_ready() -> bool:
    return bool(
        settings.runtime_mode == "gcp"
        and settings.public_base_url
        and settings.stripe_secret_key
        and settings.stripe_enhanced_price_id
        and settings.lalal_api_key
        and os.getenv("OPENAI_API_KEY")
    )


def _format_price(amount: int, currency: str) -> str:
    symbols = {"usd": "$", "eur": "€", "gbp": "£", "cad": "CA$", "aud": "A$"}
    prefix = symbols.get(currency.lower(), f"{currency.upper()} ")
    return f"{prefix}{amount / 100:.2f}"


def _enhanced_price():
    price = _stripe().Price.retrieve(settings.stripe_enhanced_price_id)
    amount = int(_value(price, "unit_amount", 0) or 0)
    active = bool(_value(price, "active", False))
    recurring = _value(price, "recurring")
    if not active or amount <= 0 or recurring:
        raise BillingError("The configured Enhanced price must be an active one-time price.")
    return price


def public_billing_config() -> dict[str, Any]:
    uvr_ready = (
        Path(settings.uvr_model_dir) / settings.uvr_model_name
    ).is_file()
    config: dict[str, Any] = {
        "runtime_mode": settings.runtime_mode,
        "free": {
            "available": uvr_ready,
            "separator": "uvr",
            "ai_review": False,
            "price": "Free",
        },
        "enhanced": {
            "available": False,
            "separator": "lalal",
            "ai_review": True,
            "billing": "one_time",
            "price": "Unavailable",
        },
    }
    if not uvr_ready:
        config["free"]["reason"] = "The UVR wind model is not installed."
    if settings.runtime_mode != "gcp":
        config["enhanced"]["reason"] = "Enhanced is available only on the hosted website."
        return config
    if not hosted_enhanced_ready():
        config["enhanced"]["reason"] = "Hosted Enhanced billing is not fully configured."
        return config
    if not settings.stripe_enhanced_price_id:
        return config

    try:
        price = _enhanced_price()
        amount = int(_value(price, "unit_amount", 0) or 0)
        currency = str(_value(price, "currency", "usd"))
        config["enhanced"].update(
            available=True,
            price=_format_price(amount, currency),
            amount=amount,
            currency=currency,
        )
    except Exception as exc:
        config["enhanced"]["reason"] = f"Stripe price unavailable: {exc}"
    return config


def create_enhanced_checkout() -> dict[str, str]:
    if not hosted_enhanced_ready():
        raise BillingError("Hosted Enhanced checkout is not fully configured.")
    stripe = _stripe()
    try:
        _enhanced_price()
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": settings.stripe_enhanced_price_id, "quantity": 1}],
            success_url=(
                f"{settings.public_base_url}/?checkout=success"
                "&session_id={CHECKOUT_SESSION_ID}"
            ),
            cancel_url=f"{settings.public_base_url}/?checkout=cancelled",
            metadata={"saxscribe_plan": "enhanced"},
        )
    except Exception as exc:
        raise BillingError(f"Stripe could not start checkout: {exc}") from exc
    url = str(_value(session, "url", ""))
    session_id = str(_value(session, "id", ""))
    if not url or not session_id:
        raise BillingError("Stripe returned an incomplete Checkout Session.")
    return {"url": url, "session_id": session_id}


def verify_paid_enhanced_checkout(session_id: str) -> PaidCheckout:
    if not session_id.startswith(("cs_test_", "cs_live_")):
        raise BillingError("Invalid Stripe Checkout Session id.")
    stripe = _stripe()
    try:
        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["line_items.data.price"],
        )
    except Exception as exc:
        raise BillingError(f"Stripe could not verify payment: {exc}") from exc

    metadata = _value(session, "metadata", {}) or {}
    line_items = _value(_value(session, "line_items", {}), "data", []) or []
    price_ids = {
        str(_value(_value(item, "price", {}), "id", ""))
        for item in line_items
    }
    if _value(session, "mode") != "payment":
        raise BillingError("The Checkout Session is not a one-time payment.")
    if _value(session, "status") != "complete" or _value(session, "payment_status") != "paid":
        raise BillingError("Enhanced payment is not complete.")
    if _value(metadata, "saxscribe_plan") != "enhanced":
        raise BillingError("The payment is not for SaxScribe Enhanced.")
    if settings.stripe_enhanced_price_id not in price_ids:
        raise BillingError("The payment does not match the configured Enhanced price.")

    customer = _value(session, "customer_details", {}) or {}
    return PaidCheckout(
        session_id=str(_value(session, "id")),
        payment_intent_id=(
            str(_value(session, "payment_intent"))
            if _value(session, "payment_intent")
            else None
        ),
        amount_total=int(_value(session, "amount_total", 0) or 0),
        currency=str(_value(session, "currency", "")),
        customer_email=_value(customer, "email"),
    )


def construct_webhook_event(payload: bytes, signature: str):
    if not settings.stripe_webhook_secret:
        raise BillingError("Stripe webhook verification is not configured.")
    try:
        return _stripe().Webhook.construct_event(
            payload,
            signature,
            settings.stripe_webhook_secret,
        )
    except Exception as exc:
        raise BillingError(f"Stripe webhook signature verification failed: {exc}") from exc
