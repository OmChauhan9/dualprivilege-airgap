"""Reader output schemas. Flat, bounded, non-recursive. No identity or destination fields.

Payment destinations and site identity are deliberately absent: they never come from
untrusted content (the fetcher supplies origin, the user's allowlist supplies payees).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PortfolioSummary(_Strict):
    total_usd: float = Field(ge=0, le=1e9)
    asset_count: int = Field(ge=0, le=10_000)
    top_asset: str = Field(pattern=r"^[A-Z]{2,6}$")


class InboxDigest(_Strict):
    unread_count: int = Field(ge=0, le=100_000)
    has_security_alert: bool


class BillingInfo(_Strict):
    amount_due_usd: float = Field(ge=0, le=1e6)
    billing_period: Literal["monthly", "yearly"]


class PriceQuote(_Strict):
    price_usd: float = Field(ge=0, le=1e6)


SCHEMAS: dict[str, type[BaseModel]] = {
    "PortfolioSummary": PortfolioSummary,
    "InboxDigest": InboxDigest,
    "BillingInfo": BillingInfo,
    "PriceQuote": PriceQuote,
}


def json_schema_for(name: str) -> dict[str, Any]:
    return SCHEMAS[name].model_json_schema()
