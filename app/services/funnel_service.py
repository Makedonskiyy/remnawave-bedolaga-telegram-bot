from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
)
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.crud.discount_offer import upsert_discount_offer
from app.database.crud.notification import notification_sent, record_notification
from app.database.models import Subscription, SubscriptionStatus, Transaction, TransactionType, User, UserStatus
from app.utils.miniapp_buttons import build_miniapp_or_callback_button
from app.utils.rich_notify import try_send_rich_notification


logger = structlog.get_logger(__name__)


class FunnelNotificationType:
    TRIAL_NOT_CONNECTED_1H = 'funnel_trial_not_connected_1h'
    TRIAL_NOT_CONNECTED_24H = 'funnel_trial_not_connected_24h'
    TRIAL_STARTED = 'funnel_trial_started'
    TRIAL_EXPIRING_24H = 'funnel_trial_expiring_24h'
    TRIAL_EXPIRED_SURVEY = 'funnel_trial_expired_survey'
    TRIAL_DISCOUNT_15 = 'funnel_trial_discount_15'
    DORMANT_14D = 'funnel_dormant_14d'
    REFERRAL_NPS_5D = 'funnel_referral_nps_5d'
    FAILED_PAYMENT_1H = 'funnel_failed_payment_1h'


class FunnelService:
    def __init__(self, db: AsyncSession, bot: Bot | None = None) -> None:
        self.db = db
        self.bot = bot

    async def register_trial(self, user_id: int, trial_expires_at: datetime | None = None) -> None:
        """Регистрация триала (для совместимости вызовов).

        Фоновый воркер автоматически отслеживает триалы через таблицу subscriptions.
        """
        logger.debug('FunnelService: register_trial called', user_id=user_id, expires_at=trial_expires_at)

    async def mark_converted(self, user_id: int) -> None:
        """Помечает пользователя как сконвертированного (для совместимости вызовов)."""
        logger.debug('FunnelService: mark_converted called', user_id=user_id)

    async def register_dormant(self, user_id: int, days_delay: int = 14) -> None:
        """Регистрация спящего (для совместимости вызовов)."""
        logger.debug('FunnelService: register_dormant called', user_id=user_id, days_delay=days_delay)

    async def register_referral(self, user_id: int, days_delay: int = 5) -> None:
        """Регистрация реферального триггера (для совместимости вызовов)."""
        logger.debug('FunnelService: register_referral called', user_id=user_id, days_delay=days_delay)

    async def process_pending_actions(self) -> int:
        """Запускает проверку всех этапов воронки и отправляет созревшие уведомления."""
        if not self.bot:
            logger.warning('FunnelService: bot instance not set, skipping process_pending_actions')
            return 0

        sent_count = 0
        sent_count += await self._process_trial_funnel()
        sent_count += await self._process_dormant_funnel()
        sent_count += await self._process_referral_funnel()
        sent_count += await self._process_failed_payment_funnel()
        return sent_count

    async def _process_trial_funnel(self) -> int:
        """Воронка тестового периода (триала):

        1. Через 1 час после старта: если не подключился (traffic_used_gb < 0.005) — помощь с настройкой.
        2. Через 2 часа после старта: если подключился — предложение зафиксировать тариф.
        3. Через 24 часа после старта: если всё ещё не подключился — повторное напоминание с инструкцией.
        4. За 24 часа до окончания: напоминание о завершении завтра.
        5. Момент окончания: опрос о качестве сервиса + призыв возобновить доступ.
        6. Через 24 часа после окончания: персональная скидка 15% на 48 часов.
        """
        now = datetime.now(UTC)
        stmt = (
            select(Subscription)
            .join(User, Subscription.user_id == User.id)
            .options(
                selectinload(Subscription.user),
                selectinload(Subscription.tariff),
            )
            .where(
                Subscription.is_trial == True,
                Subscription.created_at >= now - timedelta(days=30),
                Subscription.end_date >= now - timedelta(days=7),
                User.status == UserStatus.ACTIVE.value,
                User.telegram_id.isnot(None),
            )
        )
        result = await self.db.execute(stmt)
        subscriptions = result.scalars().all()

        sent = 0
        seen_user_ids: set[int] = set()
        for sub in subscriptions:
            user = sub.user
            if not user or user.status != UserStatus.ACTIVE.value:
                continue
            if user.id in seen_user_ids:
                continue
            seen_user_ids.add(user.id)

            # Если пользователь уже оплачивал постоянную подписку — воронку триала завершаем
            if getattr(user, 'has_had_paid_subscription', False):
                continue

            # Проверяем, нет ли другой активной платной подписки у пользователя
            if settings.is_multi_tariff_enabled():
                other_active = await self.db.execute(
                    select(Subscription.id)
                    .where(
                        Subscription.user_id == user.id,
                        Subscription.id != sub.id,
                        Subscription.is_trial == False,
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.end_date > now,
                    )
                    .limit(1)
                )
                if other_active.scalar_one_or_none() is not None:
                    continue

            sub_created_at = sub.created_at or now
            sub_end_date = sub.end_date or now
            traffic_used = float(sub.traffic_used_gb or 0.0)

            # ------------------------------------------------------------------
            # Шаг 1: Через 1 час, если не подключился (трафик < 5 МБ)
            # ------------------------------------------------------------------
            if now >= sub_created_at + timedelta(hours=1) and now < sub_end_date and traffic_used < 0.005:
                if not await notification_sent(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_NOT_CONNECTED_1H):
                    text = (
                        '<b>Помощь с подключением</b>\n\n'
                        'Мы заметили, что вы ещё не подключились к VPN. Если возникли сложности с установкой приложения '
                        'или добавлением ключа, откройте инструкцию или напишите нам в поддержку. Мы поможем всё быстро настроить.'
                    )
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [build_miniapp_or_callback_button(text='Инструкция', callback_data='menu_info', cabinet_path='/info')],
                            [build_miniapp_or_callback_button(text='Поддержка', callback_data='menu_support', cabinet_path='/support')],
                        ]
                    )
                    success = await self._send_notification(user, text, keyboard)
                    if success:
                        await record_notification(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_NOT_CONNECTED_1H)
                        sent += 1
                        continue

            # ------------------------------------------------------------------
            # Шаг 2: Через 2 часа после запуска триала (если подключился и пользуется)
            # ------------------------------------------------------------------
            if now >= sub_created_at + timedelta(hours=2) and now < sub_end_date and traffic_used >= 0.005:
                if not await notification_sent(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_STARTED):
                    text = (
                        '<b>Тестовый период активен</b>\n\n'
                        'Прошло два часа с момента запуска пробного доступа. Если у вас появятся вопросы '
                        'по скорости работы или выбору локаций, напишите в поддержку.\n\n'
                        'Если всё устраивает, вы можете выбрать постоянный тариф заранее, чтобы соединение не прерывалось.'
                    )
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [build_miniapp_or_callback_button(text='Выбрать тариф', callback_data='menu_buy', cabinet_path='/subscription')],
                            [build_miniapp_or_callback_button(text='Поддержка', callback_data='menu_support', cabinet_path='/support')],
                        ]
                    )
                    success = await self._send_notification(user, text, keyboard)
                    if success:
                        await record_notification(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_STARTED)
                        sent += 1
                        continue

            # ------------------------------------------------------------------
            # Шаг 3: Через 24 часа после старта, если всё ещё не подключился
            # ------------------------------------------------------------------
            if now >= sub_created_at + timedelta(hours=24) and now < sub_end_date and traffic_used < 0.005:
                if not await notification_sent(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_NOT_CONNECTED_24H):
                    text = (
                        '<b>Напоминание о пробном периоде</b>\n\n'
                        'Прошли сутки с момента активации, но вы пока не подключились к сети. '
                        'Напоминаем, что у вас действует бесплатный доступ. '
                        'Если не получается запустить приложение на телефоне или компьютере, напишите нам, мы подскажем решение.'
                    )
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [build_miniapp_or_callback_button(text='Инструкция', callback_data='menu_info', cabinet_path='/info')],
                            [build_miniapp_or_callback_button(text='Поддержка', callback_data='menu_support', cabinet_path='/support')],
                        ]
                    )
                    success = await self._send_notification(user, text, keyboard)
                    if success:
                        await record_notification(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_NOT_CONNECTED_24H)
                        sent += 1
                        continue

            # ------------------------------------------------------------------
            # Шаг 4: За 24 часа до конца триала
            # ------------------------------------------------------------------
            if sub_end_date - now <= timedelta(hours=24) and now < sub_end_date:
                if not await notification_sent(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_EXPIRING_24H):
                    text = (
                        '<b>Остался один день пробного доступа</b>\n\n'
                        'Завтра бесплатный тестовый период завершится.\n\n'
                        'Чтобы сохранить стабильное подключение без повторной настройки приложений, '
                        'выберите удобный тариф в меню.'
                    )
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [build_miniapp_or_callback_button(text='Продлить подписку', callback_data='menu_buy', cabinet_path='/subscription')],
                            [build_miniapp_or_callback_button(text='Поддержка', callback_data='menu_support', cabinet_path='/support')],
                        ]
                    )
                    success = await self._send_notification(user, text, keyboard)
                    if success:
                        await record_notification(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_EXPIRING_24H)
                        sent += 1
                        continue

            # ------------------------------------------------------------------
            # Шаг 5: Момент окончания триала (в течение первых суток после истечения)
            # ------------------------------------------------------------------
            if now >= sub_end_date and now < sub_end_date + timedelta(hours=24):
                if not await notification_sent(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_EXPIRED_SURVEY):
                    text = (
                        '<b>Тестовый период завершён</b>\n\n'
                        'Срок действия пробного доступа истёк, и подключение приостановлено.\n\n'
                        'Поделитесь вашим впечатлением, всё ли работало стабильно? Если возникли любые трудности, '
                        'напишите нам в поддержку, мы обязательно разберёмся.\n\n'
                        'Для продолжения работы вы можете оформить подписку в меню в любое время.'
                    )
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [build_miniapp_or_callback_button(text='Вернуть доступ', callback_data='menu_buy', cabinet_path='/subscription')],
                            [build_miniapp_or_callback_button(text='Поддержка', callback_data='menu_support', cabinet_path='/support')],
                        ]
                    )
                    success = await self._send_notification(user, text, keyboard)
                    if success:
                        await record_notification(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_EXPIRED_SURVEY)
                        sent += 1
                        continue

            # ------------------------------------------------------------------
            # Шаг 6: Через 24 часа после окончания триала — персональная скидка 15%
            # ------------------------------------------------------------------
            if now >= sub_end_date + timedelta(hours=24) and now <= sub_end_date + timedelta(days=5):
                if not await notification_sent(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_DISCOUNT_15):
                    await upsert_discount_offer(
                        self.db,
                        user_id=user.id,
                        subscription_id=sub.id,
                        notification_type=FunnelNotificationType.TRIAL_DISCOUNT_15,
                        discount_percent=15,
                        bonus_amount_kopeks=0,
                        valid_hours=48,
                        effect_type='percent_discount',
                    )
                    text = (
                        '<b>Скидка 15% на первую подписку</b>\n\n'
                        'Мы подготовили для вас персональную скидку 15% на любой тариф.\n\n'
                        'Скидка действует 48 часов и применится автоматически при переходе к оплате.'
                    )
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [build_miniapp_or_callback_button(text='Активировать скидку', callback_data='menu_buy', cabinet_path='/subscription')],
                        ]
                    )
                    success = await self._send_notification(user, text, keyboard)
                    if success:
                        await record_notification(self.db, user.id, sub.id, FunnelNotificationType.TRIAL_DISCOUNT_15)
                        sent += 1

        return sent

    async def _process_dormant_funnel(self) -> int:
        """Реанимация спящих: пользователи, чья подписка истекла 14 дней назад."""
        now = datetime.now(UTC)
        lookback_start = now - timedelta(days=21)
        lookback_end = now - timedelta(days=14)

        stmt = (
            select(Subscription)
            .join(User, Subscription.user_id == User.id)
            .options(
                selectinload(Subscription.user),
                selectinload(Subscription.tariff),
            )
            .where(
                Subscription.end_date <= lookback_end,
                Subscription.end_date >= lookback_start,
                User.status == UserStatus.ACTIVE.value,
                User.telegram_id.isnot(None),
            )
        )
        result = await self.db.execute(stmt)
        subscriptions = result.scalars().all()

        sent = 0
        seen_user_ids: set[int] = set()
        for sub in subscriptions:
            user = sub.user
            if not user or user.status != UserStatus.ACTIVE.value:
                continue
            if user.id in seen_user_ids:
                continue
            seen_user_ids.add(user.id)

            # Проверяем, нет ли другой активной подписки
            has_active = await self.db.execute(
                select(Subscription.id)
                .where(
                    Subscription.user_id == user.id,
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                    Subscription.end_date > now,
                )
                .limit(1)
            )
            if has_active.scalar_one_or_none() is not None:
                continue

            if not await notification_sent(self.db, user.id, sub.id, FunnelNotificationType.DORMANT_14D):
                await upsert_discount_offer(
                    self.db,
                    user_id=user.id,
                    subscription_id=sub.id,
                    notification_type=FunnelNotificationType.DORMANT_14D,
                    discount_percent=20,
                    bonus_amount_kopeks=0,
                    valid_hours=72,
                    effect_type='percent_discount',
                )
                text = (
                    '<b>Новости сервиса и специальное предложение</b>\n\n'
                    'Мы провели оптимизацию серверной инфраструктуры и улучшили скорость зарубежных сервисов и видео.\n\n'
                    'Приглашаем вас снова оценить работу сети. В вашем профиле активирована скидка 20% на возвращение.'
                )
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [build_miniapp_or_callback_button(text='Посмотреть тарифы', callback_data='menu_buy', cabinet_path='/subscription')],
                    ]
                )
                success = await self._send_notification(user, text, keyboard)
                if success:
                    await record_notification(self.db, user.id, sub.id, FunnelNotificationType.DORMANT_14D)
                    sent += 1

        return sent

    async def _process_referral_funnel(self) -> int:
        """Реферальный триггер: пользователи с активной платной подпиской на 5-й день."""
        now = datetime.now(UTC)
        threshold_start = now - timedelta(days=7)
        threshold_end = now - timedelta(days=5)

        stmt = (
            select(Subscription)
            .join(User, Subscription.user_id == User.id)
            .options(
                selectinload(Subscription.user),
            )
            .where(
                Subscription.is_trial == False,
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.created_at <= threshold_end,
                Subscription.created_at >= threshold_start,
                User.status == UserStatus.ACTIVE.value,
                User.telegram_id.isnot(None),
            )
        )
        result = await self.db.execute(stmt)
        subscriptions = result.scalars().all()

        sent = 0
        for sub in subscriptions:
            user = sub.user
            if not user or user.status != UserStatus.ACTIVE.value:
                continue

            if not await notification_sent(self.db, user.id, sub.id, FunnelNotificationType.REFERRAL_NPS_5D):
                text = (
                    '<b>Пользуйтесь VPN бесплатно</b>\n\n'
                    'Вы пользуетесь нашей подпиской уже несколько дней. Напоминаем о возможности делиться сервисом с близкими.\n\n'
                    'Отправьте другу вашу персональную ссылку из раздела рефералов. Друг получит приятный бонус, '
                    'а вы будете получать процент с каждого его пополнения на баланс. Эти средства можно тратить на оплату своих тарифов.'
                )
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [build_miniapp_or_callback_button(text='Пригласить друга', callback_data='menu_referrals', cabinet_path='/referral')],
                    ]
                )
                success = await self._send_notification(user, text, keyboard)
                if success:
                    await record_notification(self.db, user.id, sub.id, FunnelNotificationType.REFERRAL_NPS_5D)
                    sent += 1

        return sent

    async def _process_failed_payment_funnel(self) -> int:
        """Напоминание о незавершённом пополнении баланса.

        Срабатывает через 1 час после создания транзакции DEPOSIT с is_completed=False,
        если с тех пор пользователь не совершил ни одного успешного DEPOSIT.
        Окно поиска — от 1 до 24 часов назад (старше суток не напоминаем).
        Для дедупликации используется последняя подписка пользователя.
        Пользователи без подписок пропускаются (они вряд ли успели начать оплату).
        """
        now = datetime.now(UTC)
        window_start = now - timedelta(hours=24)
        window_end = now - timedelta(hours=1)

        stmt = (
            select(Transaction)
            .options(selectinload(Transaction.user))
            .where(
                Transaction.type == TransactionType.DEPOSIT.value,
                Transaction.is_completed == False,
                Transaction.created_at >= window_start,
                Transaction.created_at <= window_end,
            )
            .order_by(Transaction.created_at)
        )
        result = await self.db.execute(stmt)
        transactions = result.scalars().all()

        sent = 0
        seen_user_ids: set[int] = set()

        for tx in transactions:
            user = tx.user
            if not user or not user.telegram_id:
                continue
            if user.id in seen_user_ids:
                continue

            # Пропускаем, если пользователь уже успешно оплатил после этой транзакции
            has_completed = await self.db.execute(
                select(Transaction.id)
                .where(
                    Transaction.user_id == user.id,
                    Transaction.type == TransactionType.DEPOSIT.value,
                    Transaction.is_completed == True,
                    Transaction.created_at > tx.created_at,
                )
                .limit(1)
            )
            if has_completed.scalar_one_or_none() is not None:
                seen_user_ids.add(user.id)
                continue

            # Берём последнюю подписку для записи дедупа (NOT NULL FK constraint)
            last_sub_row = await self.db.execute(
                select(Subscription.id)
                .where(Subscription.user_id == user.id)
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
            last_sub_id = last_sub_row.scalar_one_or_none()
            if last_sub_id is None:
                # У пользователя нет ни одной подписки — пропускаем
                seen_user_ids.add(user.id)
                continue

            if not await notification_sent(self.db, user.id, last_sub_id, FunnelNotificationType.FAILED_PAYMENT_1H):
                text = (
                    '<b>Пополнение баланса не завершено</b>\n\n'
                    'Похоже, платёж не прошёл до конца. Попробуйте повторить пополнение '
                    'или напишите нам в поддержку, мы разберёмся и поможем.'
                )
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [build_miniapp_or_callback_button(text='Пополнить баланс', callback_data='menu_balance', cabinet_path='/balance/top-up')],
                        [build_miniapp_or_callback_button(text='Поддержка', callback_data='menu_support', cabinet_path='/support')],
                    ]
                )
                success = await self._send_notification(user, text, keyboard)
                if success:
                    await record_notification(self.db, user.id, last_sub_id, FunnelNotificationType.FAILED_PAYMENT_1H)
                    sent += 1

            seen_user_ids.add(user.id)

        return sent

    async def _send_notification(self, user: User, text: str, keyboard: InlineKeyboardMarkup) -> bool:
        """Безопасная отправка уведомления с Rich-форматированием и fallback-логикой."""
        if not user.telegram_id or not self.bot:
            return False

        try:
            sent_rich = await try_send_rich_notification(
                self.bot,
                user.telegram_id,
                text,
                keyboard=keyboard,
                with_logo=False,
            )
            if not sent_rich:
                await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode='HTML',
                    reply_markup=keyboard,
                )
            return True

        except TelegramForbiddenError:
            logger.info('Пользователь заблокировал бота, уведомление воронки пропущено', user_id=user.id)
            return True

        except (TelegramBadRequest, TelegramNotFound) as exc:
            logger.warning('Ошибка Telegram API при отправке воронки', user_id=user.id, error=str(exc))
            return True

        except TelegramNetworkError as net_err:
            logger.warning('Таймаут сети при отправке воронки, повтор на следующем цикле', user_id=user.id, error=net_err)
            return False

        except Exception as exc:
            logger.error('Непредвиденная ошибка отправки воронки', user_id=user.id, exc_info=exc)
            return False
