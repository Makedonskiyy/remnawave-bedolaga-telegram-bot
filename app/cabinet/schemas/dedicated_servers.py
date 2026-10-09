"""Schemas for Dedicated Personal Servers (Личные серверы)."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class DedicatedServerCountry(BaseModel):
    """Country info for dedicated server order."""

    code: str
    name: str
    flag: str
    continent: str
    city: str


class DedicatedServerOption(BaseModel):
    """Exclusive addon option for dedicated servers."""

    id: str
    name: str
    description: str
    price_kopeks: int = 0
    icon: str


class DedicatedServerPeriodPrice(BaseModel):
    """Price for selected rental duration."""

    days: int
    price_kopeks: int
    price_rubles: float
    discount_percent: int = 0


class DedicatedServerMarketing(BaseModel):
    """Marketing content, terms of service and value propositions."""

    title: str
    badge: str
    description: str
    features: list[dict[str, str]]
    exclusive_benefits: list[dict[str, str]]
    terms_of_service: list[str]


class DedicatedServerConfigResponse(BaseModel):
    """Public configuration for dedicated servers offering."""

    countries: list[DedicatedServerCountry]
    period_prices: list[DedicatedServerPeriodPrice]
    options: list[DedicatedServerOption]
    marketing: DedicatedServerMarketing
    byos_supported: bool = True


class DedicatedServerOrderRequest(BaseModel):
    """User request to order and pay for a dedicated server."""

    country_code: str = Field(..., min_length=2, max_length=8)
    period_days: int = Field(30, ge=1, le=365)
    deployment_type: str = Field('turnkey', description="'turnkey' (под ключ) or 'byos' (свой сервер)")
    options: dict[str, bool] = Field(default_factory=dict)


class DedicatedServerOrderItem(BaseModel):
    """Dedicated server order details for user and admin."""

    id: int
    user_id: int
    status: str
    country_code: str
    country_name: str
    continent: str | None = None
    deployment_type: str
    cpu_cores: int = 1
    ram_gb: int = 1
    period_days: int
    amount_kopeks: int
    amount_rubles: float
    options: dict[str, Any] = Field(default_factory=dict)
    ip_address: str | None = None
    squad_uuid: str | None = None
    subscription_id: int | None = None
    subscription_url: str | None = None
    setup_token: str | None = None
    setup_script: str | None = None
    admin_notes: str | None = None
    rejected_reason: str | None = None
    created_at: datetime
    expires_at: datetime | None = None

    class Config:
        from_attributes = True


class DedicatedServerListResponse(BaseModel):
    """List of user's dedicated servers."""

    orders: list[DedicatedServerOrderItem]
    total: int


class DedicatedServerAdminAssignRequest(BaseModel):
    """Admin request to assign RemnaWave squad and activate server."""

    ip_address: str = Field(..., min_length=7, max_length=64)
    squad_uuid: str = Field(..., min_length=10, max_length=255)
    admin_notes: str | None = None


class DedicatedServerAdminRejectRequest(BaseModel):
    """Admin request to reject order and refund user balance."""

    reason: str = Field(..., min_length=3, max_length=500)


class DedicatedServerAdminSettingsUpdate(BaseModel):
    """Admin update of dedicated server base prices or countries."""

    base_monthly_price_kopeks: int | None = Field(None, ge=10000)
    allowed_country_codes: list[str] | None = None


class DedicatedServerSetupScriptResponse(BaseModel):
    """Script response for node auto-installation."""

    order_id: int
    install_command: str
    manual_instructions: str

