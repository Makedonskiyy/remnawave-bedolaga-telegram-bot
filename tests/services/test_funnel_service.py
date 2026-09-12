from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError

from app.database.models import Subscription, SubscriptionStatus, User, UserStatus
from app.services.funnel_service import FunnelNotificationType, FunnelService


@pytest.fixture
def mock_db() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def mock_bot() -> AsyncMock:
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_compatibility_methods(mock_db: AsyncMock) -> None:
    service = FunnelService(mock_db)
    # Ensure helper methods run without exception
    await service.register_trial(user_id=1, trial_expires_at=datetime.now(UTC))
    await service.mark_converted(user_id=1)
    await service.register_dormant(user_id=1, days_delay=14)
    await service.register_referral(user_id=1, days_delay=5)


@pytest.mark.asyncio
async def test_trial_step_not_connected_1h(mock_db: AsyncMock, mock_bot: AsyncMock) -> None:
    now = datetime.now(UTC)
    user = User(id=1, telegram_id=12345, status=UserStatus.ACTIVE.value, has_had_paid_subscription=False)
    sub = Subscription(
        id=10,
        user_id=1,
        is_trial=True,
        traffic_used_gb=0.0,
        created_at=now - timedelta(hours=1, minutes=10),
        end_date=now + timedelta(days=2),
        status=SubscriptionStatus.TRIAL.value,
    )
    sub.user = user

    exec_mock = AsyncMock()
    exec_mock.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[sub])))
    mock_db.execute = AsyncMock(return_value=exec_mock)

    service = FunnelService(mock_db, mock_bot)

    with patch('app.services.funnel_service.notification_sent', new_callable=AsyncMock) as mock_sent,          patch('app.services.funnel_service.record_notification', new_callable=AsyncMock) as mock_record,          patch('app.services.funnel_service.try_send_rich_notification', new_callable=AsyncMock) as mock_rich:
        mock_sent.return_value = False
        mock_rich.return_value = True

        sent = await service._process_trial_funnel()

        assert sent == 1
        mock_rich.assert_called_once()
        mock_record.assert_called_once_with(
            mock_db, 1, 10, FunnelNotificationType.TRIAL_NOT_CONNECTED_1H
        )


@pytest.mark.asyncio
async def test_trial_step_started_2h_when_connected(mock_db: AsyncMock, mock_bot: AsyncMock) -> None:
    now = datetime.now(UTC)
    user = User(id=1, telegram_id=12345, status=UserStatus.ACTIVE.value, has_had_paid_subscription=False)
    sub = Subscription(
        id=11,
        user_id=1,
        is_trial=True,
        traffic_used_gb=0.15,  # Connected and used 150 MB
        created_at=now - timedelta(hours=2, minutes=10),
        end_date=now + timedelta(days=2),
        status=SubscriptionStatus.TRIAL.value,
    )
    sub.user = user

    exec_mock = AsyncMock()
    exec_mock.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[sub])))
    mock_db.execute = AsyncMock(return_value=exec_mock)

    service = FunnelService(mock_db, mock_bot)

    with patch('app.services.funnel_service.notification_sent', new_callable=AsyncMock) as mock_sent,          patch('app.services.funnel_service.record_notification', new_callable=AsyncMock) as mock_record,          patch('app.services.funnel_service.try_send_rich_notification', new_callable=AsyncMock) as mock_rich:
        mock_sent.return_value = False
        mock_rich.return_value = True

        sent = await service._process_trial_funnel()

        assert sent == 1
        mock_rich.assert_called_once()
        mock_record.assert_called_once_with(
            mock_db, 1, 11, FunnelNotificationType.TRIAL_STARTED
        )


@pytest.mark.asyncio
async def test_trial_step_expiring_24h(mock_db: AsyncMock, mock_bot: AsyncMock) -> None:
    now = datetime.now(UTC)
    user = User(id=1, telegram_id=12345, status=UserStatus.ACTIVE.value, has_had_paid_subscription=False)
    sub = Subscription(
        id=12,
        user_id=1,
        is_trial=True,
        traffic_used_gb=0.5,
        created_at=now - timedelta(days=2),
        end_date=now + timedelta(hours=10),  # Less than 24h remaining
        status=SubscriptionStatus.TRIAL.value,
    )
    sub.user = user

    exec_mock = AsyncMock()
    exec_mock.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[sub])))
    mock_db.execute = AsyncMock(return_value=exec_mock)

    service = FunnelService(mock_db, mock_bot)

    with patch('app.services.funnel_service.notification_sent', new_callable=AsyncMock) as mock_sent,          patch('app.services.funnel_service.record_notification', new_callable=AsyncMock) as mock_record,          patch('app.services.funnel_service.try_send_rich_notification', new_callable=AsyncMock) as mock_rich:
        # Step 1 and 2 already sent
        async def side_effect(db, u_id, s_id, n_type):
            return n_type != FunnelNotificationType.TRIAL_EXPIRING_24H
        mock_sent.side_effect = side_effect
        mock_rich.return_value = True

        sent = await service._process_trial_funnel()

        assert sent == 1
        mock_record.assert_called_once_with(
            mock_db, 1, 12, FunnelNotificationType.TRIAL_EXPIRING_24H
        )


@pytest.mark.asyncio
async def test_trial_step_expired_survey(mock_db: AsyncMock, mock_bot: AsyncMock) -> None:
    now = datetime.now(UTC)
    user = User(id=1, telegram_id=12345, status=UserStatus.ACTIVE.value, has_had_paid_subscription=False)
    sub = Subscription(
        id=13,
        user_id=1,
        is_trial=True,
        traffic_used_gb=0.5,
        created_at=now - timedelta(days=3, hours=5),
        end_date=now - timedelta(hours=5),  # Expired 5h ago
        status=SubscriptionStatus.EXPIRED.value,
    )
    sub.user = user

    exec_mock = AsyncMock()
    exec_mock.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[sub])))
    mock_db.execute = AsyncMock(return_value=exec_mock)

    service = FunnelService(mock_db, mock_bot)

    with patch('app.services.funnel_service.notification_sent', new_callable=AsyncMock) as mock_sent,          patch('app.services.funnel_service.record_notification', new_callable=AsyncMock) as mock_record,          patch('app.services.funnel_service.try_send_rich_notification', new_callable=AsyncMock) as mock_rich:
        async def side_effect(db, u_id, s_id, n_type):
            return n_type != FunnelNotificationType.TRIAL_EXPIRED_SURVEY
        mock_sent.side_effect = side_effect
        mock_rich.return_value = True

        sent = await service._process_trial_funnel()

        assert sent == 1
        mock_record.assert_called_once_with(
            mock_db, 1, 13, FunnelNotificationType.TRIAL_EXPIRED_SURVEY
        )


@pytest.mark.asyncio
async def test_trial_step_discount_15(mock_db: AsyncMock, mock_bot: AsyncMock) -> None:
    now = datetime.now(UTC)
    user = User(id=1, telegram_id=12345, status=UserStatus.ACTIVE.value, has_had_paid_subscription=False)
    sub = Subscription(
        id=14,
        user_id=1,
        is_trial=True,
        traffic_used_gb=0.5,
        created_at=now - timedelta(days=4),
        end_date=now - timedelta(hours=26),  # Expired 26h ago
        status=SubscriptionStatus.EXPIRED.value,
    )
    sub.user = user

    exec_mock = AsyncMock()
    exec_mock.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[sub])))
    mock_db.execute = AsyncMock(return_value=exec_mock)

    service = FunnelService(mock_db, mock_bot)

    with patch('app.services.funnel_service.notification_sent', new_callable=AsyncMock) as mock_sent,          patch('app.services.funnel_service.record_notification', new_callable=AsyncMock) as mock_record,          patch('app.services.funnel_service.upsert_discount_offer', new_callable=AsyncMock) as mock_upsert,          patch('app.services.funnel_service.try_send_rich_notification', new_callable=AsyncMock) as mock_rich:
        async def side_effect(db, u_id, s_id, n_type):
            return n_type != FunnelNotificationType.TRIAL_DISCOUNT_15
        mock_sent.side_effect = side_effect
        mock_rich.return_value = True

        sent = await service._process_trial_funnel()

        assert sent == 1
        mock_upsert.assert_called_once_with(
            mock_db,
            user_id=1,
            subscription_id=14,
            notification_type=FunnelNotificationType.TRIAL_DISCOUNT_15,
            discount_percent=15,
            bonus_amount_kopeks=0,
            valid_hours=48,
            effect_type='percent_discount',
        )
        mock_record.assert_called_once_with(
            mock_db, 1, 14, FunnelNotificationType.TRIAL_DISCOUNT_15
        )


@pytest.mark.asyncio
async def test_dormant_funnel(mock_db: AsyncMock, mock_bot: AsyncMock) -> None:
    now = datetime.now(UTC)
    user = User(id=2, telegram_id=55555, status=UserStatus.ACTIVE.value)
    sub = Subscription(
        id=20,
        user_id=2,
        is_trial=False,
        end_date=now - timedelta(days=16),
        status=SubscriptionStatus.EXPIRED.value,
    )
    sub.user = user

    exec_sub = AsyncMock()
    exec_sub.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[sub])))

    exec_active = AsyncMock()
    exec_active.scalar_one_or_none = MagicMock(return_value=None)

    # First call returns subs in window, second call returns active subs (None)
    mock_db.execute = AsyncMock(side_effect=[exec_sub, exec_active])

    service = FunnelService(mock_db, mock_bot)

    with patch('app.services.funnel_service.notification_sent', new_callable=AsyncMock) as mock_sent,          patch('app.services.funnel_service.record_notification', new_callable=AsyncMock) as mock_record,          patch('app.services.funnel_service.upsert_discount_offer', new_callable=AsyncMock) as mock_upsert,          patch('app.services.funnel_service.try_send_rich_notification', new_callable=AsyncMock) as mock_rich:
        mock_sent.return_value = False
        mock_rich.return_value = True

        sent = await service._process_dormant_funnel()

        assert sent == 1
        mock_upsert.assert_called_once_with(
            mock_db,
            user_id=2,
            subscription_id=20,
            notification_type=FunnelNotificationType.DORMANT_14D,
            discount_percent=20,
            bonus_amount_kopeks=0,
            valid_hours=72,
            effect_type='percent_discount',
        )
        mock_record.assert_called_once_with(
            mock_db, 2, 20, FunnelNotificationType.DORMANT_14D
        )


@pytest.mark.asyncio
async def test_referral_funnel(mock_db: AsyncMock, mock_bot: AsyncMock) -> None:
    now = datetime.now(UTC)
    user = User(id=3, telegram_id=77777, status=UserStatus.ACTIVE.value)
    sub = Subscription(
        id=30,
        user_id=3,
        is_trial=False,
        status=SubscriptionStatus.ACTIVE.value,
        created_at=now - timedelta(days=6),
        end_date=now + timedelta(days=24),
    )
    sub.user = user

    exec_sub = AsyncMock()
    exec_sub.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[sub])))
    mock_db.execute = AsyncMock(return_value=exec_sub)

    service = FunnelService(mock_db, mock_bot)

    with patch('app.services.funnel_service.notification_sent', new_callable=AsyncMock) as mock_sent,          patch('app.services.funnel_service.record_notification', new_callable=AsyncMock) as mock_record,          patch('app.services.funnel_service.try_send_rich_notification', new_callable=AsyncMock) as mock_rich:
        mock_sent.return_value = False
        mock_rich.return_value = True

        sent = await service._process_referral_funnel()

        assert sent == 1
        mock_record.assert_called_once_with(
            mock_db, 3, 30, FunnelNotificationType.REFERRAL_NPS_5D
        )


@pytest.mark.asyncio
async def test_send_notification_fallback_to_classic(mock_db: AsyncMock, mock_bot: AsyncMock) -> None:
    user = User(id=1, telegram_id=12345)
    service = FunnelService(mock_db, mock_bot)
    kb = MagicMock()

    with patch('app.services.funnel_service.try_send_rich_notification', new_callable=AsyncMock) as mock_rich:
        mock_rich.return_value = False  # Rich failed or disabled

        success = await service._send_notification(user, '<b>Test</b>', kb)

        assert success is True
        mock_bot.send_message.assert_called_once_with(
            chat_id=12345,
            text='<b>Test</b>',
            parse_mode='HTML',
            reply_markup=kb,
        )


@pytest.mark.asyncio
async def test_send_notification_handles_blocked_user(mock_db: AsyncMock, mock_bot: AsyncMock) -> None:
    user = User(id=1, telegram_id=12345)
    service = FunnelService(mock_db, mock_bot)
    kb = MagicMock()

    with patch('app.services.funnel_service.try_send_rich_notification', new_callable=AsyncMock) as mock_rich:
        mock_rich.side_effect = TelegramForbiddenError(message='bot blocked', method=MagicMock())

        success = await service._send_notification(user, '<b>Test</b>', kb)

        assert success is True  # Counted as handled to avoid retry spam


@pytest.mark.asyncio
async def test_process_pending_actions_no_bot(mock_db: AsyncMock) -> None:
    service = FunnelService(mock_db, bot=None)
    sent = await service.process_pending_actions()
    assert sent == 0


@pytest.mark.asyncio
async def test_process_pending_actions_full(mock_db: AsyncMock, mock_bot: AsyncMock) -> None:
    service = FunnelService(mock_db, mock_bot)
    with patch.object(service, '_process_trial_funnel', new_callable=AsyncMock) as m_trial, \
         patch.object(service, '_process_dormant_funnel', new_callable=AsyncMock) as m_dormant, \
         patch.object(service, '_process_referral_funnel', new_callable=AsyncMock) as m_referral:
        m_trial.return_value = 2
        m_dormant.return_value = 1
        m_referral.return_value = 3

        total = await service.process_pending_actions()
        assert total == 6
