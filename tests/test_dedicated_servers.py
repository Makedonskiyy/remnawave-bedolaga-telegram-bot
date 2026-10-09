"""Unit and integration tests for Dedicated / Personal Servers (Личные серверы)."""

import pytest
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

from app.database.models import (
    DedicatedServerOrder,
    DedicatedServerStatus,
    TransactionType,
    User,
    Subscription,
)
from app.services.dedicated_server_service import (
    DedicatedServerService,
    DEDICATED_SERVER_COUNTRIES,
    DEDICATED_SERVER_OPTIONS,
    DEDICATED_MARKETING_CONTENT,
    DEFAULT_BASE_MONTHLY_PRICE_KOPEKS,
)
from app.cabinet.schemas.dedicated_servers import (
    DedicatedServerConfigResponse,
    DedicatedServerOrderRequest,
    DedicatedServerOrderItem,
    DedicatedServerAdminAssignRequest,
    DedicatedServerAdminRejectRequest,
)


def test_dedicated_server_countries_developed_only():
    """Verify countries list contains only developed countries and has required metadata."""
    countries = DedicatedServerService.get_countries()
    assert len(countries) >= 10

    country_codes = {c['code'] for c in countries}
    # European developed countries
    assert 'DE' in country_codes
    assert 'NL' in country_codes
    assert 'FI' in country_codes
    assert 'GB' in country_codes
    assert 'FR' in country_codes
    assert 'SE' in country_codes
    # North America
    assert 'US' in country_codes
    assert 'CA' in country_codes
    # Asia
    assert 'JP' in country_codes
    assert 'SG' in country_codes
    assert 'KR' in country_codes

    for c in countries:
        assert c['flag']
        assert c['name']
        assert c['continent'] in ('europe', 'north_america', 'asia')
        assert c['city']


def test_dedicated_server_pricing_and_discounts():
    """Verify period pricing has proper progression and discounts for long-term rentals."""
    prices = DedicatedServerService.get_period_prices(DEFAULT_BASE_MONTHLY_PRICE_KOPEKS)
    price_by_days = {p['days']: p for p in prices}

    assert 30 in price_by_days
    assert 90 in price_by_days
    assert 180 in price_by_days
    assert 365 in price_by_days

    # 30 days is base price
    assert price_by_days[30]['price_kopeks'] == DEFAULT_BASE_MONTHLY_PRICE_KOPEKS

    # 90 days discount ~10%
    price_90 = price_by_days[90]['price_kopeks']
    assert price_90 < 3 * DEFAULT_BASE_MONTHLY_PRICE_KOPEKS

    # 365 days discount ~22%
    price_365 = price_by_days[365]['price_kopeks']
    assert price_365 < 12 * DEFAULT_BASE_MONTHLY_PRICE_KOPEKS

    # calculate_order_price matches
    assert DedicatedServerService.calculate_order_price(30) == DEFAULT_BASE_MONTHLY_PRICE_KOPEKS
    assert DedicatedServerService.calculate_order_price(90) == price_90


def test_dedicated_server_marketing_and_tos():
    """Verify marketing content and terms of service are populated."""
    config = DedicatedServerService.get_config_response()
    marketing = config['marketing']

    assert 'title' in marketing
    assert 'features' in marketing
    assert 'exclusive_benefits' in marketing
    assert 'terms_of_service' in marketing

    feature_titles = [f['title'] for f in marketing['features']]
    assert any('Безлимитный' in t for t in feature_titles)
    assert any('устройства' in t for t in feature_titles)
    assert any('IP' in t for t in feature_titles)

    benefit_titles = [b['title'] for b in marketing['exclusive_benefits']]
    assert any('нейросети' in t for t in benefit_titles)
    assert any('YouTube' in t for t in benefit_titles)

    # ToS includes spam/abuse prohibitions
    tos = ' '.join(marketing['terms_of_service'])
    assert 'спам' in tos.lower() or 'ddos' in tos.lower()


def test_dedicated_server_schemas():
    """Verify Pydantic schemas serialize and validate properly."""
    order_req = DedicatedServerOrderRequest(
        country_code='DE',
        period_days=30,
        deployment_type='turnkey',
        options={'ai_access': True, 'youtube_no_ads': True},
    )
    assert order_req.country_code == 'DE'
    assert order_req.period_days == 30
    assert order_req.options['ai_access'] is True

    item = DedicatedServerOrderItem(
        id=1,
        user_id=10,
        status='pending',
        country_code='DE',
        country_name='Германия',
        continent='europe',
        deployment_type='turnkey',
        cpu_cores=1,
        ram_gb=1,
        period_days=30,
        amount_kopeks=89000,
        amount_rubles=890.0,
        options={'ai_access': True},
        created_at=datetime.now(UTC),
    )
    assert item.id == 1
    assert item.amount_rubles == 890.0


@pytest.mark.asyncio
async def test_dedicated_server_create_order_insufficient_funds():
    """Verify error raised when user balance is insufficient."""
    user = User(
        id=1,
        telegram_id=123456,
        balance_kopeks=1000,  # 10 RUB only
    )
    db = AsyncMock()

    with pytest.raises(ValueError, match='Недостаточно средств'):
        await DedicatedServerService.create_order(
            db=db,
            user=user,
            country_code='DE',
            period_days=30,
        )


@pytest.mark.asyncio
async def test_dedicated_server_create_order_invalid_country():
    """Verify error raised when unsupported country code requested."""
    user = User(
        id=1,
        telegram_id=123456,
        balance_kopeks=1000000,
    )
    db = AsyncMock()

    with pytest.raises(ValueError, match='не поддерживается'):
        await DedicatedServerService.create_order(
            db=db,
            user=user,
            country_code='XX',
            period_days=30,
        )


@pytest.mark.asyncio
async def test_dedicated_server_workflow():
    """Test full workflow: create order, assign server, and reject order with refund."""
    user = User(
        id=1,
        telegram_id=123456,
        balance_kopeks=10000000,  # Plenty of funds
    )

    db = AsyncMock()
    # Mock subtract_user_balance
    with patch('app.services.dedicated_server_service.subtract_user_balance', new_callable=AsyncMock) as mock_sub:
        mock_sub.return_value = True

        order = await DedicatedServerService.create_order(
            db=db,
            user=user,
            country_code='DE',
            period_days=30,
            deployment_type='turnkey',
            options={'ai_access': True, 'youtube_no_ads': True},
        )

        assert order.user_id == 1
        assert order.status == DedicatedServerStatus.PENDING.value
        assert order.country_code == 'DE'
        assert order.cpu_cores == 1
        assert order.ram_gb == 1
        assert order.setup_token is not None

        # Test script generation
        script = DedicatedServerService.get_setup_script(order)
        assert 'curl' in script
        assert order.setup_token in script

    # Test assign server
    mock_subscription = Subscription(
        id=55,
        user_id=1,
        subscription_url='vless://test-dedicated-link',
    )
    with patch('app.services.dedicated_server_service.create_paid_subscription', new_callable=AsyncMock) as mock_create_sub, \
         patch('app.services.dedicated_server_service.SubscriptionService') as mock_sub_service_cls:

        mock_create_sub.return_value = mock_subscription
        mock_sub_service = MagicMock()
        mock_sub_service.sync_subscription_with_remnawave = AsyncMock()
        mock_sub_service_cls.return_value = mock_sub_service

        # Mock db.execute returning our order
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = order
        db.execute.return_value = mock_res

        assigned = await DedicatedServerService.assign_server(
            db=db,
            order_id=order.id,
            ip_address='195.201.55.99',
            squad_uuid='squad-dedicated-uuid-123',
            admin_notes='Setup on Hetzner node',
        )

        assert assigned.status == DedicatedServerStatus.ACTIVE.value
        assert assigned.ip_address == '195.201.55.99'
        assert assigned.squad_uuid == 'squad-dedicated-uuid-123'
        assert assigned.subscription_id == 55

        # Verify create_paid_subscription was called with unmetered traffic (0) and unlimited devices (999)
        mock_create_sub.assert_called_once()
        _, kwargs = mock_create_sub.call_args
        assert kwargs['traffic_limit_gb'] == 0
        assert kwargs['device_limit'] == 999
        assert kwargs['connected_squads'] == ['squad-dedicated-uuid-123']

    # Test reject order with refund
    with patch('app.services.dedicated_server_service.add_user_balance', new_callable=AsyncMock) as mock_refund:
        order.status = DedicatedServerStatus.PENDING.value
        rejected = await DedicatedServerService.reject_order(
            db=db,
            order_id=order.id,
            reason='No stock in this datacenter',
        )

        assert rejected.status == DedicatedServerStatus.REJECTED.value
        assert rejected.rejected_reason == 'No stock in this datacenter'
        mock_refund.assert_called_once()
        _, r_kwargs = mock_refund.call_args
        assert r_kwargs['transaction_type'] == TransactionType.REFUND
        assert r_kwargs['amount_kopeks'] == order.amount_kopeks


@pytest.mark.asyncio
async def test_dedicated_server_save_and_load_pricing():
    """Verify admin can save and reload custom dynamic pricing."""
    db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    db.execute.return_value = mock_res

    # Save custom pricing: 1 990 rub base price
    saved = await DedicatedServerService.save_pricing_config(
        db=db,
        base_monthly_price_kopeks=199000,
        period_discounts={'30': 0, '90': 12, '180': 20, '365': 25},
        country_prices_kopeks={'US': 249000},
    )

    assert saved['base_monthly_price_kopeks'] == 199000
    assert saved['period_discounts']['90'] == 12
    assert saved['country_prices_kopeks']['US'] == 249000

    # Calculate price with updated settings
    price_de = DedicatedServerService.calculate_order_price(30, 'DE')
    assert price_de == 199000

    price_us = DedicatedServerService.calculate_order_price(30, 'US')
    assert price_us == 249000


def test_dedicated_server_route_order_no_conflict():
    """Verify admin_dedicated_servers router takes precedence over admin_servers /{server_id}."""
    from fastapi import FastAPI
    from app.cabinet.routes import router as cabinet_router

    app = FastAPI()
    app.include_router(cabinet_router)

    prefixes = [getattr(r.original_router, 'prefix', '') for r in cabinet_router.routes if hasattr(r, 'original_router')]
    assert '/admin/servers/dedicated' in prefixes
    assert '/admin/servers' in prefixes
    dedicated_idx = prefixes.index('/admin/servers/dedicated')
    servers_idx = prefixes.index('/admin/servers')
    assert dedicated_idx < servers_idx, f'Dedicated router ({dedicated_idx}) must precede servers router ({servers_idx})'

    from app.cabinet.routes.admin_dedicated_servers import router as admin_dedicated_router
    route_paths = [r.path for r in admin_dedicated_router.routes]
    pricing_idx = route_paths.index('/admin/servers/dedicated/pricing/config')
    order_id_idx = route_paths.index('/admin/servers/dedicated/{order_id}')
    assert pricing_idx < order_id_idx, f'pricing/config ({pricing_idx}) must precede {{order_id}} ({order_id_idx}) to avoid 422 shadowing'


def test_dedicated_server_config_response_includes_pricing_and_periods():
    """Verify get_config_response returns base_price_rubles, periods, and country prices for the catalog."""
    config = DedicatedServerService.get_config_response()
    assert 'base_price_rubles' in config
    assert 'base_price_kopeks' in config
    assert 'periods' in config
    assert len(config['periods']) >= 4
    for p in config['periods']:
        assert 'period_days' in p
        assert 'discount_percent' in p
        assert 'label' in p


def test_dedicated_server_is_not_legacy_subscription(monkeypatch):
    """Verify dedicated server subscriptions are NOT flagged as legacy subscriptions requiring tariff selection."""
    from app.config import Settings
    from app.utils.legacy_subscription import is_legacy_subscription

    monkeypatch.setattr(Settings, 'is_tariffs_mode', lambda self: True)

    sub = MagicMock(spec=Subscription)
    sub.is_trial = False
    sub.tariff_id = None
    sub.is_dedicated_server = True
    sub.dedicated_server_orders = [MagicMock()]

    assert is_legacy_subscription(sub) is False


@pytest.mark.asyncio
async def test_dedicated_server_assign_notification_and_sync():
    """Verify assign_server calls sync_remnawave_user and sends rich notification without clunky text."""
    db = AsyncMock()
    order = DedicatedServerOrder(
        id=42,
        user_id=10,
        status=DedicatedServerStatus.PENDING.value,
        period_days=30,
        country_code='DE',
        country_name='Германия',
    )
    user = User(id=10, telegram_id=987654321)
    order.user = user

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = order
    db.execute.return_value = mock_res

    bot = AsyncMock()
    mock_sub = Subscription(id=100, user_id=10, subscription_url=None)
    mock_remna_user = MagicMock(subscription_url='https://example.com/sub/token123')

    with patch('app.services.dedicated_server_service.create_paid_subscription', AsyncMock(return_value=mock_sub)), \
         patch('app.services.dedicated_server_service.SubscriptionService') as mock_sub_service_cls:
        
        mock_sub_service = mock_sub_service_cls.return_value
        mock_sub_service.sync_remnawave_user = AsyncMock(return_value=mock_remna_user)

        updated_order = await DedicatedServerService.assign_server(
            db=db,
            order_id=42,
            ip_address='192.168.1.1',
            squad_uuid='squad-uuid-test',
            admin_notes='test note',
            bot=bot,
        )

        assert updated_order.status == DedicatedServerStatus.ACTIVE.value
        assert updated_order.ip_address == '192.168.1.1'
        mock_sub_service.sync_remnawave_user.assert_awaited_once()

        bot.send_message.assert_awaited_once()
        call_kwargs = bot.send_message.call_args.kwargs
        text = call_kwargs['text']

        # Rich format checks
        assert '🖥 <b>Персональный сервер готов к работе</b>' in text
        assert '192.168.1.1' in text
        assert 'https://example.com/sub/token123' in text
        # Clunky text removed
        assert 'Скопируйте ссылку и добавьте в приложение' not in text
        assert 'Доступно в личном кабинете' not in text
        # Keyboard has buttons
        assert call_kwargs['reply_markup'] is not None
        button_texts = [b.text for row in call_kwargs['reply_markup'].inline_keyboard for b in row]
        assert 'Подключить' in button_texts


