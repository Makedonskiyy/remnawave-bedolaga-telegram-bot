"""Tests for Whitelist (Белые списки) pay-per-GB tariffs."""

import pytest
from app.database.models import Tariff, User
from app.cabinet.schemas.tariffs import TariffListItem, TariffDetailResponse, TariffCreateRequest, TariffUpdateRequest
from app.services.pricing_engine import PricingEngine


def test_tariff_whitelist_properties():
    tariff = Tariff(
        name='Белые списки',
        tariff_type='whitelist',
        traffic_limit_gb=0,
        device_limit=2,
        traffic_price_per_gb_kopeks=1500,  # 15 RUB per GB
        min_traffic_gb=10,
        max_traffic_gb=500,
    )
    assert tariff.is_whitelist is True
    assert tariff.can_purchase_custom_traffic() is True
    assert tariff.get_price_for_custom_traffic(10) == 15000  # 150 RUB
    assert tariff.get_price_for_custom_traffic(50) == 75000  # 750 RUB
    assert tariff.get_price_for_custom_traffic(5) is None  # below min
    assert tariff.get_price_for_custom_traffic(600) is None  # above max
    assert tariff.has_configured_price_for_period(30) is True


def test_tariff_standard_properties():
    tariff = Tariff(
        name='Стандарт',
        tariff_type='standard',
        traffic_limit_gb=100,
        device_limit=1,
        period_prices={'30': 20000},
    )
    assert tariff.is_whitelist is False
    assert tariff.can_purchase_custom_traffic() is False
    assert tariff.has_configured_price_for_period(30) is True
    assert tariff.has_configured_price_for_period(60) is False


def test_tariff_schemas_whitelist_support():
    # Test create request
    create_req = TariffCreateRequest(
        name='Белые списки',
        tariff_type='whitelist',
        traffic_price_per_gb_kopeks=1000,
        min_traffic_gb=5,
        max_traffic_gb=200,
    )
    assert create_req.tariff_type == 'whitelist'

    # Test update request
    update_req = TariffUpdateRequest(
        tariff_type='whitelist',
    )
    assert update_req.tariff_type == 'whitelist'

    now = __import__('datetime').datetime.now()
    list_item = TariffListItem(
        id=1,
        name='Белые списки',
        tariff_type='whitelist',
        is_whitelist=True,
        is_active=True,
        is_trial_available=False,
        traffic_limit_gb=0,
        device_limit=1,
        tier_level=1,
        display_order=0,
        servers_count=1,
        subscriptions_count=0,
        created_at=now,
    )
    assert list_item.tariff_type == 'whitelist'
    assert list_item.is_whitelist is True


@pytest.mark.asyncio
async def test_whitelist_pricing_calculation():
    tariff = Tariff(
        id=99,
        name='Белые списки',
        tariff_type='whitelist',
        traffic_limit_gb=0,
        device_limit=1,
        traffic_price_per_gb_kopeks=2000,  # 20 RUB per GB
        min_traffic_gb=10,
        max_traffic_gb=1000,
        period_prices={},  # No fixed base price
    )
    pricing = PricingEngine()
    result = await pricing.calculate_tariff_purchase_price(
        tariff=tariff,
        period_days=30,
        custom_traffic_gb=50,
    )
    # 50 GB * 2000 kopeks = 100 000 kopeks (1 000 RUB)
    assert result.final_total == 100000
    assert result.original_total == 100000
