"""Dedicated Personal Server Service (Услуга «Личные серверы»).

Manages orders for dedicated VPS nodes (1 vCPU, 1 GB RAM) connected
to our RemnaWave infrastructure with unmetered traffic, unlimited devices,
clean IPs, and exclusive addons (YouTube ad-blocking, global AI access).
"""

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.crud.subscription import create_paid_subscription
from app.database.crud.user import add_user_balance, subtract_user_balance
from app.database.models import (
    DedicatedServerOrder,
    DedicatedServerStatus,
    Subscription,
    SystemSetting,
    TransactionType,
    User,
)
from app.services.subscription_service import SubscriptionService


logger = structlog.get_logger(__name__)

# Каталог развитых стран (Европа, Северная Америка, развитая Азия)
DEDICATED_SERVER_COUNTRIES = [
    # Европа
    {'code': 'DE', 'name': 'Германия', 'flag': '🇩🇪', 'continent': 'europe', 'city': 'Франкфурт'},
    {'code': 'NL', 'name': 'Нидерланды', 'flag': '🇳🇱', 'continent': 'europe', 'city': 'Амстердам'},
    {'code': 'FI', 'name': 'Финляндия', 'flag': '🇫🇮', 'continent': 'europe', 'city': 'Хельсинки'},
    {'code': 'GB', 'name': 'Великобритания', 'flag': '🇬🇧', 'continent': 'europe', 'city': 'Лондон'},
    {'code': 'FR', 'name': 'Франция', 'flag': '🇫🇷', 'continent': 'europe', 'city': 'Париж'},
    {'code': 'SE', 'name': 'Швеция', 'flag': '🇸🇪', 'continent': 'europe', 'city': 'Стокгольм'},
    {'code': 'PL', 'name': 'Польша', 'flag': '🇵🇱', 'continent': 'europe', 'city': 'Варшава'},
    {'code': 'ES', 'name': 'Испания', 'flag': '🇪🇸', 'continent': 'europe', 'city': 'Мадрид'},
    {'code': 'CH', 'name': 'Швейцария', 'flag': '🇨🇭', 'continent': 'europe', 'city': 'Цюрих'},
    # Северная Америка
    {'code': 'US', 'name': 'США', 'flag': '🇺🇸', 'continent': 'north_america', 'city': 'Нью-Йорк / Силиконовая долина'},
    {'code': 'CA', 'name': 'Канада', 'flag': '🇨🇦', 'continent': 'north_america', 'city': 'Торонто'},
    # Азия (развитые страны)
    {'code': 'JP', 'name': 'Япония', 'flag': '🇯🇵', 'continent': 'asia', 'city': 'Токио'},
    {'code': 'SG', 'name': 'Сингапур', 'flag': '🇸🇬', 'continent': 'asia', 'city': 'Сингапур'},
    {'code': 'KR', 'name': 'Южная Корея', 'flag': '🇰🇷', 'continent': 'asia', 'city': 'Сеул'},
]

# Базовая цена за месяц (в копейках): 1 290 ₽
DEFAULT_BASE_MONTHLY_PRICE_KOPEKS = 129000

# Эксклюзивные опции, доступные клиентам персональных серверов
DEDICATED_SERVER_OPTIONS = [
    {
        'id': 'ai_access',
        'name': 'Доступ ко всем нейросетям',
        'description': 'Прямой доступ к Google Gemini, ChatGPT, Claude, Perplexity без банов региона и Cloudflare',
        'price_kopeks': 0,
        'icon': '✨',
    },
    {
        'id': 'youtube_no_ads',
        'name': 'YouTube без рекламы',
        'description': 'Специализированная маршрутизация и Smart DNS для просмотра YouTube без рекламных вставок',
        'price_kopeks': 0,
        'icon': '▶️',
    },
]

DEDICATED_MARKETING_CONTENT = {
    'title': 'Персональный выделенный VPN-сервер',
    'badge': 'VIP / Dedicated VPS',
    'description': (
        'Личный сервер 1 vCPU, 1 GB RAM только для вас. Подключаем к нашей защищённой инфраструктуре '
        'RemnaWave со всеми протоколами (VLESS Reality, Shadowsocks, Trojan) и выдаём персональную ссылку. '
        'Никаких соседей, замедлений и ограничений.'
    ),
    'features': [
        {
            'title': 'Выделенный чистый IP',
            'desc': 'Репутация чистого IP-адреса: никаких капч Cloudflare/Google и блокировок от чужого спама.',
            'icon': '🛡️',
        },
        {
            'title': 'Безлимитный трафик',
            'desc': 'Честный безлимит 1 Гбит/с — качайте, смотрите 4K видео и стримьте без подсчёта гигабайт.',
            'icon': '⚡',
        },
        {
            'title': 'Без ограничений на устройства',
            'desc': 'Подключайте смартфоны, ПК, Smart TV, роутеры всей семьи и делитесь с друзьями.',
            'icon': '📱',
        },
        {
            'title': 'Настройка за 1 клик',
            'desc': 'Мы настроим сервер под ключ за вас, либо предоставим скрипт для подключения вашего VPS за 30 секунд.',
            'icon': '⚙️',
        },
    ],
    'exclusive_benefits': [
        {
            'title': 'Все заблокированные нейросети',
            'desc': 'Гарантированный доступ к Google Gemini, OpenAI ChatGPT, Claude, Cursor и Perplexity.',
            'icon': '🧠',
        },
        {
            'title': 'YouTube без рекламы',
            'desc': 'Умный DNS-спуфинг и белые роуты — чистый просмотр контента на любых устройствах.',
            'icon': '🎬',
        },
    ],
    'terms_of_service': [
        'Сервер предоставляется в аренду на оплаченный период с возможностью автоматического продления.',
        'Строго запрещены: массовые спам-рассылки, DDoS-атаки, сканирование портов, фишинг, кардинг и любая нелегальная деятельность.',
        'На локациях Германии и США торрент-трафик защищённо фильтруется для предотвращения жалоб правообладателей (DMCA).',
        'При нарушении правил сервис оставляет за собой право приостановить доступ без возврата средств.',
    ],
}


class DedicatedServerService:
    @staticmethod
    def get_countries() -> list[dict[str, Any]]:
        return DEDICATED_SERVER_COUNTRIES

    @staticmethod
    def get_country_by_code(code: str) -> dict[str, Any] | None:
        code_upper = (code or '').strip().upper()
        for item in DEDICATED_SERVER_COUNTRIES:
            if item['code'] == code_upper:
                return item
        return None

    DEDICATED_SERVERS_PRICING_KEY = 'DEDICATED_SERVERS_PRICING'
    _cached_pricing: dict[str, Any] = {
        'base_monthly_price_kopeks': DEFAULT_BASE_MONTHLY_PRICE_KOPEKS,
        'period_discounts': {'30': 0, '90': 10, '180': 15, '365': 22},
        'country_prices_kopeks': {},
    }

    @classmethod
    def get_pricing_sync(cls) -> dict[str, Any]:
        return dict(cls._cached_pricing)

    @classmethod
    async def get_pricing_config(cls, db: AsyncSession | None = None) -> dict[str, Any]:
        """Получить текущие настройки цен личных серверов из БД или кэша."""
        if db is not None:
            try:
                res = await db.execute(
                    select(SystemSetting).where(SystemSetting.key == cls.DEDICATED_SERVERS_PRICING_KEY)
                )
                setting = res.scalar_one_or_none()
                if setting and setting.value:
                    loaded = json.loads(setting.value)
                    if isinstance(loaded, dict):
                        cls._cached_pricing.update(loaded)
            except Exception as e:
                logger.warning('Failed to load dedicated server pricing from DB, using defaults', error=str(e))
        return dict(cls._cached_pricing)

    @classmethod
    async def save_pricing_config(
        cls,
        db: AsyncSession,
        base_monthly_price_kopeks: int,
        period_discounts: dict[str, int] | None = None,
        country_prices_kopeks: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Сохранить настройки цен личных серверов в БД."""
        cls._cached_pricing['base_monthly_price_kopeks'] = base_monthly_price_kopeks
        if period_discounts is not None:
            cls._cached_pricing['period_discounts'] = period_discounts
        if country_prices_kopeks is not None:
            cls._cached_pricing['country_prices_kopeks'] = country_prices_kopeks

        res = await db.execute(
            select(SystemSetting).where(SystemSetting.key == cls.DEDICATED_SERVERS_PRICING_KEY)
        )
        setting = res.scalar_one_or_none()
        if not setting:
            setting = SystemSetting(
                key=cls.DEDICATED_SERVERS_PRICING_KEY,
                value=json.dumps(cls._cached_pricing, ensure_ascii=False),
            )
            db.add(setting)
        else:
            setting.value = json.dumps(cls._cached_pricing, ensure_ascii=False)

        await db.commit()
        return dict(cls._cached_pricing)

    @staticmethod
    def get_period_prices(
        base_monthly_kopeks: int = DEFAULT_BASE_MONTHLY_PRICE_KOPEKS,
        discounts: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Расчёт тарифов с накопительными скидками за срок."""
        disc_map = discounts or {'30': 0, '90': 10, '180': 15, '365': 22}
        period_defs = [
            (30, int(disc_map.get('30', 0))),
            (90, int(disc_map.get('90', 10))),
            (180, int(disc_map.get('180', 15))),
            (365, int(disc_map.get('365', 22))),
        ]
        result = []
        for days, disc_pct in period_defs:
            months = days / 30.0
            raw_price = int(base_monthly_kopeks * months)
            discount_amount = int(raw_price * (disc_pct / 100.0))
            price_kopeks = max(100, raw_price - discount_amount)
            result.append({
                'days': days,
                'price_kopeks': price_kopeks,
                'price_rubles': round(price_kopeks / 100, 2),
                'discount_percent': disc_pct,
            })
        return result

    @classmethod
    def calculate_order_price(
        cls,
        period_days: int,
        country_code: str | None = None,
        base_monthly_kopeks: int | None = None,
    ) -> int:
        pricing = cls.get_pricing_sync()
        base_price = base_monthly_kopeks if base_monthly_kopeks is not None else pricing.get('base_monthly_price_kopeks', DEFAULT_BASE_MONTHLY_PRICE_KOPEKS)
        if country_code:
            code_upper = country_code.strip().upper()
            country_overrides = pricing.get('country_prices_kopeks', {})
            if code_upper in country_overrides:
                base_price = country_overrides[code_upper]

        discounts = pricing.get('period_discounts', {})
        for p in cls.get_period_prices(base_price, discounts):
            if p['days'] == period_days:
                return p['price_kopeks']
        months = max(1, period_days // 30)
        return months * base_price

    @classmethod
    def get_config_response(cls) -> dict[str, Any]:
        pricing = cls.get_pricing_sync()
        base_price = pricing.get('base_monthly_price_kopeks', DEFAULT_BASE_MONTHLY_PRICE_KOPEKS)
        discounts = pricing.get('period_discounts', {})
        return {
            'countries': DEDICATED_SERVER_COUNTRIES,
            'period_prices': cls.get_period_prices(base_price, discounts),
            'options': DEDICATED_SERVER_OPTIONS,
            'marketing': DEDICATED_MARKETING_CONTENT,
            'byos_supported': True,
        }

    @staticmethod
    async def create_order(
        db: AsyncSession,
        user: User,
        country_code: str,
        period_days: int = 30,
        deployment_type: str = 'turnkey',
        options: dict[str, Any] | None = None,
        server_ip: str | None = None,
        server_ssh_port: int | None = 22,
        server_ssh_password: str | None = None,
        server_notes: str | None = None,
    ) -> DedicatedServerOrder:
        """Создать и оплатить заказ выделенного сервера с баланса пользователя."""
        country = DedicatedServerService.get_country_by_code(country_code)
        if not country:
            raise ValueError(f"Выбранная страна '{country_code}' не поддерживается для выделенных серверов")

        amount_kopeks = DedicatedServerService.calculate_order_price(period_days, country_code)
        if user.balance_kopeks < amount_kopeks:
            missing_rubles = round((amount_kopeks - user.balance_kopeks) / 100, 2)
            raise ValueError(f'Недостаточно средств на балансе. Пополните баланс на {missing_rubles} ₽')

        # Списываем средства
        reason = (
            f"Заказ личного сервера ({country['flag']} {country['name']}, {period_days} дн.)"
        )
        success = await subtract_user_balance(
            db=db,
            user=user,
            amount_kopeks=amount_kopeks,
            description=reason,
            create_transaction=True,
            transaction_type=TransactionType.DEDICATED_SERVER,
            commit=False,
        )
        if not success:
            raise ValueError('Не удалось списать средства с баланса')

        setup_token = secrets.token_urlsafe(32)
        order_options = dict(options or {'ai_access': True, 'youtube_no_ads': True})
        if deployment_type == 'byos' or server_ip or server_ssh_password or server_notes:
            order_options['byos'] = {
                'ip': server_ip,
                'ssh_port': server_ssh_port or 22,
                'ssh_password': server_ssh_password,
                'notes': server_notes,
            }

        order = DedicatedServerOrder(
            user_id=user.id,
            status=DedicatedServerStatus.PENDING.value,
            country_code=country['code'],
            country_name=country['name'],
            continent=country.get('continent'),
            deployment_type=deployment_type,
            cpu_cores=1,
            ram_gb=1,
            period_days=period_days,
            amount_kopeks=amount_kopeks,
            options=order_options,
            ip_address=server_ip.strip() if server_ip else None,
            setup_token=setup_token,
            expires_at=datetime.now(UTC) + timedelta(days=period_days),
        )
        db.add(order)
        await db.commit()
        await db.refresh(order)

        logger.info(
            'Заказ личного сервера создан',
            order_id=order.id,
            user_id=user.id,
            country=country['code'],
            amount_kopeks=amount_kopeks,
        )

        # Отправка уведомлений администраторам
        try:
            from app.bot_factory import create_bot
            from app.services.admin_notification_service import AdminNotificationService

            bot = create_bot()
            notification_service = AdminNotificationService(bot)
            await notification_service.send_dedicated_server_order_notification(db, order, user)
        except Exception as exc:
            logger.warning('Не удалось отправить уведомление о заказе личного сервера админам', error=str(exc))

        return order

    @staticmethod
    async def assign_server(
        db: AsyncSession,
        order_id: int,
        ip_address: str,
        squad_uuid: str,
        admin_notes: str | None = None,
        bot: Bot | None = None,
    ) -> DedicatedServerOrder:
        """Администратор привязывает настроенный сквад и активирует сервер."""
        result = await db.execute(
            select(DedicatedServerOrder)
            .where(DedicatedServerOrder.id == order_id)
            .options(selectinload(DedicatedServerOrder.user))
        )
        order = result.scalar_one_or_none()
        if not order:
            raise ValueError(f'Заказ #{order_id} не найден')

        order.ip_address = ip_address.strip()
        order.squad_uuid = squad_uuid.strip()
        order.admin_notes = admin_notes
        order.status = DedicatedServerStatus.ACTIVE.value
        order.expires_at = datetime.now(UTC) + timedelta(days=order.period_days)

        # Создаем персональную подписку (unlimited traffic = 0, unlimited devices = 999)
        subscription = await create_paid_subscription(
            db=db,
            user_id=order.user_id,
            duration_days=order.period_days,
            traffic_limit_gb=0,  # unmetered
            device_limit=999,    # unlimited
            connected_squads=[squad_uuid.strip()],
            commit=True,
        )
        order.subscription_id = subscription.id

        # Синхронизируем с RemnaWave для получения ссылки
        sub_service = SubscriptionService()
        try:
            await sub_service.sync_subscription_with_remnawave(db, subscription)
        except Exception as sync_err:
            logger.warning('Синхронизация подписки личного сервера с RemnaWave вернула ошибку', error=str(sync_err))

        await db.commit()
        await db.refresh(order)

        # Уведомление пользователю
        if bot and order.user and order.user.telegram_id:
            try:
                sub_url = subscription.subscription_url or 'Доступно в личном кабинете'
                country = DedicatedServerService.get_country_by_code(order.country_code)
                flag = country['flag'] if country else '🌐'
                user_text = (
                    f'🚀 <b>Ваш персональный сервер готов к работе!</b>\n\n'
                    f'{flag} <b>Локация:</b> {order.country_name} ({order.country_code})\n'
                    f'🌐 <b>IP-адрес:</b> <code>{order.ip_address}</code>\n'
                    f'📅 <b>Срок аренды:</b> {order.period_days} дн. (до {order.expires_at.strftime("%d.%m.%Y")})\n'
                    f'♾️ <b>Лимиты:</b> Трафик без ограничений, устройства без ограничений\n\n'
                    f'🔗 <b>Ваша персональная ссылка подписки:</b>\n'
                    f'<code>{sub_url}</code>\n\n'
                    f'<i>Скопируйте ссылку и добавьте в приложение Happ / v2rayNG / Hiddify / Streisand.</i>'
                )
                await bot.send_message(chat_id=order.user.telegram_id, text=user_text, parse_mode='HTML')
            except Exception as notify_err:
                logger.warning('Не удалось отправить уведомление пользователю о готовности сервера', error=str(notify_err))

        return order

    @staticmethod
    async def reject_order(
        db: AsyncSession,
        order_id: int,
        reason: str,
        bot: Bot | None = None,
    ) -> DedicatedServerOrder:
        """Отклонить заказ и вернуть средства пользователю."""
        result = await db.execute(
            select(DedicatedServerOrder)
            .where(DedicatedServerOrder.id == order_id)
            .options(selectinload(DedicatedServerOrder.user))
        )
        order = result.scalar_one_or_none()
        if not order:
            raise ValueError(f'Заказ #{order_id} не найден')

        if order.status == DedicatedServerStatus.REJECTED.value:
            return order

        order.status = DedicatedServerStatus.REJECTED.value
        order.rejected_reason = reason.strip()

        # Возвращаем средства на баланс
        refund_reason = f'Возврат за отклонённый заказ личного сервера #{order.id}: {reason}'
        await add_user_balance(
            db=db,
            user=order.user,
            amount_kopeks=order.amount_kopeks,
            description=refund_reason,
            create_transaction=True,
            transaction_type=TransactionType.REFUND,
        )

        await db.commit()
        await db.refresh(order)

        if bot and order.user and order.user.telegram_id:
            try:
                refund_rub = round(order.amount_kopeks / 100, 2)
                user_text = (
                    f'❌ <b>Заказ персонального сервера #{order.id} отклонён</b>\n\n'
                    f'Причина: {reason}\n'
                    f'💰 <b>{refund_rub} ₽ возвращены на ваш баланс.</b>'
                )
                await bot.send_message(chat_id=order.user.telegram_id, text=user_text, parse_mode='HTML')
            except Exception as notify_err:
                logger.warning('Не удалось уведомить пользователя об отмене заказа', error=str(notify_err))

        return order

    @staticmethod
    def get_setup_script(order: DedicatedServerOrder) -> str:
        """Скрипт для быстрой привязки своего VPS (BYOS)."""
        base_url = (
            getattr(settings, 'CABINET_URL', None)
            or getattr(settings, 'WEBHOOK_URL', None)
            or 'https://api.vpn.example.com'
        ).rstrip('/')
        token = order.setup_token or 'token'
        return f'curl -sSL {base_url}/cabinet/dedicated-servers/install/{token} | bash'

