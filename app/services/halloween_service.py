from datetime import UTC, datetime, timedelta
import random
import structlog
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.subscription import (
    create_subscription,
    extend_subscription,
    get_subscription_by_user_id,
)
from app.database.crud.tariff import get_all_tariffs
from app.database.models import (
    HalloweenConfig,
    HalloweenExchangeLog,
    HalloweenGameLog,
    HalloweenUserState,
    Subscription,
    User,
)
from app.services.subscription_service import SubscriptionService


logger = structlog.get_logger(__name__)

# Дата окончания ивента (3 ноября 2026, 23:59:59 MSK = 20:59:59 UTC)
HALLOWEEN_END_TIME = datetime(2026, 11, 3, 20, 59, 59, tzinfo=UTC)

# Максимум дней подписки, которые можно натапать за ивент
MAX_CLAIMABLE_DAYS = 14

# Стоимость 1 дня в минутах (24 часа * 60 минут)
MINUTES_PER_DAY = 1440

# Конфигурация уровней тапалки
HALLOWEEN_LEVELS = {
    1: {'name': 'Первый', 'profit_per_tap': 1, 'passive_per_hour': 15, 'upgrade_cost': 150},
    2: {'name': 'Второй', 'profit_per_tap': 2, 'passive_per_hour': 35, 'upgrade_cost': 450},
    3: {'name': 'Третий', 'profit_per_tap': 4, 'passive_per_hour': 75, 'upgrade_cost': 1200},
    4: {'name': 'Четвертый', 'profit_per_tap': 7, 'passive_per_hour': 150, 'upgrade_cost': 3000},
    5: {'name': 'Повелитель тыкв', 'profit_per_tap': 12, 'passive_per_hour': 300, 'upgrade_cost': None},
}

TRICK_OR_TREAT_COOLDOWN_SECONDS = 4 * 3600  # 4 часа


class HalloweenService:
    @staticmethod
    def get_event_remaining_seconds() -> int:
        now = datetime.now(UTC)
        remaining = (HALLOWEEN_END_TIME - now).total_seconds()
        return max(0, int(remaining))

    @classmethod
    async def is_event_enabled(cls, db: AsyncSession) -> bool:
        """Проверить, включен ли ивент глобально (для всех пользователей)."""
        res = await db.execute(select(HalloweenConfig.is_enabled).order_by(HalloweenConfig.id.asc()).limit(1))
        enabled = res.scalar_one_or_none()
        return bool(enabled)

    @classmethod
    async def set_event_enabled(cls, db: AsyncSession, enabled: bool) -> bool:
        """Включить или выключить хэллоуинский ивент."""
        res = await db.execute(select(HalloweenConfig).order_by(HalloweenConfig.id.asc()).limit(1))
        config = res.scalar_one_or_none()
        if not config:
            config = HalloweenConfig(is_enabled=enabled)
            db.add(config)
        else:
            config.is_enabled = enabled
            config.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(config)
        logger.info('🎃 Хэллоуин ивент переключен', is_enabled=config.is_enabled)
        return config.is_enabled

    @classmethod
    def sync_user_state(cls, state: HalloweenUserState) -> dict:
        """Синхронизирует энергию и начисляет накопленный пассивный доход."""
        now = datetime.now(UTC)
        lvl_cfg = HALLOWEEN_LEVELS.get(state.level, HALLOWEEN_LEVELS[1])

        # 1. Регенерация энергии (+1 единица в секунду)
        if state.last_energy_updated_at:
            elapsed_energy_seconds = (now - state.last_energy_updated_at).total_seconds()
            if elapsed_energy_seconds > 0 and state.energy < state.max_energy:
                regen = int(elapsed_energy_seconds)
                state.energy = min(state.max_energy, state.energy + regen)
                state.last_energy_updated_at = now
        else:
            state.last_energy_updated_at = now

        # 2. Пассивный доход (до 3 часов оффлайна)
        passive_collected = 0
        if state.last_passive_collected_at and not state.is_cursed:
            elapsed_passive_seconds = (now - state.last_passive_collected_at).total_seconds()
            capped_seconds = min(3 * 3600, max(0, elapsed_passive_seconds))
            hours = capped_seconds / 3600.0

            is_boosted = state.boost_active_until is not None and state.boost_active_until > now
            multiplier = 2 if is_boosted else 1

            passive_collected = int(hours * lvl_cfg['passive_per_hour'] * multiplier)
            if passive_collected > 0:
                state.earned_minutes += passive_collected
                state.available_minutes += passive_collected
                state.last_passive_collected_at = now

        # 3. Проверка истечения буста
        is_boosted = state.boost_active_until is not None and state.boost_active_until > now
        boost_remaining_seconds = 0
        if is_boosted:
            boost_remaining_seconds = max(0, int((state.boost_active_until - now).total_seconds()))

        # 4. Кулдаун 'Сладость или гадость'
        tot_cooldown_remaining = 0
        if state.last_trick_or_treat_at:
            elapsed_tot = (now - state.last_trick_or_treat_at).total_seconds()
            if elapsed_tot < TRICK_OR_TREAT_COOLDOWN_SECONDS:
                tot_cooldown_remaining = int(TRICK_OR_TREAT_COOLDOWN_SECONDS - elapsed_tot)

        state.updated_at = now

        return {
            'user_id': state.user_id,
            'earned_minutes': state.earned_minutes,
            'available_minutes': state.available_minutes,
            'level': state.level,
            'level_name': lvl_cfg['name'],
            'profit_per_tap': lvl_cfg['profit_per_tap'] * (2 if is_boosted else 1),
            'base_profit_per_tap': lvl_cfg['profit_per_tap'],
            'passive_per_hour': lvl_cfg['passive_per_hour'] * (2 if is_boosted else 1),
            'base_passive_per_hour': lvl_cfg['passive_per_hour'],
            'upgrade_cost': lvl_cfg['upgrade_cost'],
            'energy': state.energy,
            'max_energy': state.max_energy,
            'is_boosted': is_boosted,
            'boost_remaining_seconds': boost_remaining_seconds,
            'is_cursed': state.is_cursed,
            'cursed_frozen_minutes': state.cursed_frozen_minutes,
            'trick_or_treat_cooldown_seconds': tot_cooldown_remaining,
            'can_play_trick_or_treat': tot_cooldown_remaining == 0 and not state.is_cursed,
            'claimed_days': state.claimed_days,
            'max_claimable_days': MAX_CLAIMABLE_DAYS,
            'event_remaining_seconds': cls.get_event_remaining_seconds(),
            'passive_collected_just_now': passive_collected,
        }

    @classmethod
    async def process_taps(cls, db: AsyncSession, state: HalloweenUserState, requested_taps: int) -> dict:
        """Обработка тапов пользователя."""
        if state.is_cursed:
            raise ValueError('Вы заколдованы! Снимите проклятие, чтобы продолжить тапать.')

        if cls.get_event_remaining_seconds() <= 0:
            raise ValueError('Хэллоуинский ивент завершен!')

        if requested_taps <= 0:
            requested_taps = 1
        requested_taps = min(requested_taps, 50)

        now = datetime.now(UTC)

        if state.last_energy_updated_at:
            elapsed = (now - state.last_energy_updated_at).total_seconds()
            if elapsed > 0 and state.energy < state.max_energy:
                state.energy = min(state.max_energy, state.energy + int(elapsed))
        state.last_energy_updated_at = now

        if state.energy <= 0:
            raise ValueError('Энергия исчерпана! Подождите, пока она восстановится.')

        taps_applied = min(requested_taps, state.energy)
        state.energy -= taps_applied

        lvl_cfg = HALLOWEEN_LEVELS.get(state.level, HALLOWEEN_LEVELS[1])
        is_boosted = state.boost_active_until is not None and state.boost_active_until > now
        multiplier = 2 if is_boosted else 1

        added_minutes = taps_applied * lvl_cfg['profit_per_tap'] * multiplier
        state.earned_minutes += added_minutes
        state.available_minutes += added_minutes

        await db.commit()
        await db.refresh(state)

        return cls.sync_user_state(state)

    @classmethod
    async def upgrade_level(cls, db: AsyncSession, state: HalloweenUserState) -> dict:
        """Повышение уровня за накопленные минуты."""
        if state.is_cursed:
            raise ValueError('Вы заколдованы! Снимите проклятие перед улучшением.')

        current_lvl = state.level
        lvl_cfg = HALLOWEEN_LEVELS.get(current_lvl)
        if not lvl_cfg or lvl_cfg['upgrade_cost'] is None:
            raise ValueError('Вы уже достигли максимального уровня!')

        cost = lvl_cfg['upgrade_cost']
        if state.available_minutes < cost:
            raise ValueError(f'Недостаточно минут! Требуется {cost} минут, у вас {state.available_minutes}.')

        state.available_minutes -= cost
        state.level += 1
        state.updated_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(state)

        return cls.sync_user_state(state)

    @classmethod
    async def play_trick_or_treat(cls, db: AsyncSession, state: HalloweenUserState, chosen_pumpkin: int) -> dict:
        """Сыграть в 'Сладость или гадость' (выбор из 3 тыкв). Записывает лог в HalloweenGameLog."""
        now = datetime.now(UTC)

        if state.is_cursed:
            raise ValueError('Вы уже заколдованы! Поделитесь ссылкой, чтобы снять проклятие.')

        if state.last_trick_or_treat_at:
            elapsed = (now - state.last_trick_or_treat_at).total_seconds()
            if elapsed < TRICK_OR_TREAT_COOLDOWN_SECONDS:
                remaining = int(TRICK_OR_TREAT_COOLDOWN_SECONDS - elapsed)
                raise ValueError(f'Котлы ещё не закипели! Попробуйте снова через {remaining // 60} мин.')

        state.last_trick_or_treat_at = now

        # 70% Сладость (Treat), 30% Гадость (Trick)
        roll = random.random()
        is_treat = roll < 0.70

        result_data = {
            'is_treat': is_treat,
            'chosen_pumpkin': chosen_pumpkin,
        }

        reward_type = 'curse'
        reward_title = 'Бу! Тебя заколдовали! 🕷️'
        reward_value_num = 0

        if is_treat:
            prize_roll = random.random()
            if prize_roll < 0.45:
                bonus = 60
                state.earned_minutes += bonus
                state.available_minutes += bonus
                reward_type = 'minutes'
                reward_title = f'+{bonus} минут к балансу'
                reward_value_num = bonus
                result_data['reward_type'] = 'minutes'
                result_data['reward_title'] = f'+{bonus} минут к твоему балансу'
                result_data['reward_value'] = bonus
            elif prize_roll < 0.80:
                state.boost_active_until = now + timedelta(hours=1)
                reward_type = 'boost'
                reward_title = 'Буст x2 на 1 час'
                reward_value_num = 3600
                result_data['reward_type'] = 'boost'
                result_data['reward_title'] = 'Буст дохода в тапалке x2 на 1 час'
                result_data['reward_value'] = 3600
            else:
                promo_code = f'HALLOWEEN25-{random.randint(1000, 9999)}'
                reward_type = 'promo'
                reward_title = f'Промокод 25% ({promo_code})'
                reward_value_num = 25
                result_data['reward_type'] = 'promo'
                result_data['reward_title'] = 'Промокод на скидку 25%'
                result_data['reward_value'] = promo_code
        else:
            freeze = max(15, int(state.available_minutes * 0.30))
            freeze = min(freeze, state.available_minutes)
            state.available_minutes -= freeze
            state.cursed_frozen_minutes = freeze
            state.is_cursed = True
            reward_type = 'curse'
            reward_title = f'Проклятие (заморожено {freeze} мин)'
            reward_value_num = freeze
            result_data['reward_type'] = 'curse'
            result_data['reward_title'] = 'Бу! Тебя заколдовали! 🕷️'
            result_data['frozen_minutes'] = freeze

        # Запись лога игры
        game_log = HalloweenGameLog(
            user_id=state.user_id,
            is_treat=is_treat,
            chosen_pumpkin=chosen_pumpkin,
            reward_type=reward_type,
            reward_title=reward_title,
            reward_value=reward_value_num,
            created_at=now,
        )
        db.add(game_log)

        await db.commit()
        await db.refresh(state)

        user_state_data = cls.sync_user_state(state)
        return {**result_data, 'state': user_state_data}

    @classmethod
    async def uncurse(cls, db: AsyncSession, state: HalloweenUserState) -> dict:
        """Снять проклятие через шеринг: возвращает замороженные минуты с бонусом x2."""
        if not state.is_cursed:
            return cls.sync_user_state(state)

        returned_bonus = state.cursed_frozen_minutes * 2
        state.available_minutes += returned_bonus
        state.earned_minutes += state.cursed_frozen_minutes
        state.cursed_frozen_minutes = 0
        state.is_cursed = False
        state.updated_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(state)

        return {
            'success': True,
            'message': f'Колдовство спало! Вам возвращены минуты с бонусом x2 (+{returned_bonus} мин).',
            'state': cls.sync_user_state(state),
        }

    @classmethod
    async def claim_reward_days(
        cls, db: AsyncSession, user: User, state: HalloweenUserState, days: int
    ) -> dict:
        """Обмен нафармленных минут на дни реальной VPN подписки. Логирует обмен."""
        if state.is_cursed:
            raise ValueError('Вы заколдованы! Снимите проклятие перед обменом.')

        if days not in (1, 3, 7, 14):
            raise ValueError('Можно обменять на 1, 3, 7 или 14 дней.')

        if state.claimed_days + days > MAX_CLAIMABLE_DAYS:
            available_to_claim = MAX_CLAIMABLE_DAYS - state.claimed_days
            raise ValueError(
                f'Превышен лимит ивента! Вы можете получить еще максимум {available_to_claim} дн. подписки (всего до {MAX_CLAIMABLE_DAYS} дн.).'
            )

        required_minutes = days * MINUTES_PER_DAY
        if state.available_minutes < required_minutes:
            raise ValueError(
                f'Недостаточно минут! Требуется {required_minutes} мин. (у вас {state.available_minutes}).'
            )

        sub = await get_subscription_by_user_id(db, user.id)
        if sub:
            await extend_subscription(db, sub, days)
            logger.info('🎃 Хэллоуин: продлена подписка на дни', user_id=user.id, days=days)
        else:
            tariffs = await get_all_tariffs(db)
            tariff = tariffs[0] if tariffs else None
            if not tariff:
                raise ValueError('Тариф для создания подписки не найден.')

            sub = await create_subscription(
                db=db,
                user_id=user.id,
                tariff_id=tariff.id,
                period_days=days,
                is_trial=False,
            )
            try:
                sub_service = SubscriptionService()
                await sub_service.create_remnawave_user(db, sub)
            except Exception as e:
                logger.error('⚠️ Ошибка RemnaWave при создании хэллоуин-подписки', error=e, user_id=user.id)

        # Списываем минуты и обновляем счетчик
        state.available_minutes -= required_minutes
        state.claimed_days += days
        state.updated_at = datetime.now(UTC)

        # Лог обмена
        exchange_log = HalloweenExchangeLog(
            user_id=user.id,
            days_claimed=days,
            minutes_spent=required_minutes,
            created_at=datetime.now(UTC),
        )
        db.add(exchange_log)

        await db.commit()
        await db.refresh(state)

        return {
            'success': True,
            'days_added': days,
            'claimed_total': state.claimed_days,
            'message': f'Успешно начислено +{days} дн. подписки VPN!',
            'state': cls.sync_user_state(state),
        }

    @classmethod
    async def get_admin_statistics(cls, db: AsyncSession) -> dict:
        """Собирает полную статистику по ивенту: игроки, минуты, игры, награды, обмены, топ игроков."""
        # 1. Общие показатели по игрокам и минутам
        totals_q = await db.execute(
            select(
                func.count(HalloweenUserState.id),
                func.coalesce(func.sum(HalloweenUserState.earned_minutes), 0),
                func.coalesce(func.sum(HalloweenUserState.available_minutes), 0),
                func.coalesce(func.sum(HalloweenUserState.claimed_days), 0),
            )
        )
        total_players, total_earned_minutes, total_available_minutes, total_claimed_days = totals_q.one()

        # 2. Статистика по мини-играм
        games_q = await db.execute(
            select(
                func.count(HalloweenGameLog.id),
                func.coalesce(func.sum(case((HalloweenGameLog.is_treat == True, 1), else_=0)), 0),
                func.coalesce(func.sum(case((HalloweenGameLog.is_treat == False, 1), else_=0)), 0),
            )
        )
        total_games, total_treats, total_tricks = games_q.one()

        # 3. Разбивка наград по типам (minutes, boost, promo, curse)
        rewards_breakdown_q = await db.execute(
            select(
                HalloweenGameLog.reward_type,
                func.count(HalloweenGameLog.id),
                func.coalesce(func.sum(HalloweenGameLog.reward_value), 0),
            ).group_by(HalloweenGameLog.reward_type)
        )
        rewards_breakdown = [
            {'reward_type': row[0], 'count': int(row[1]), 'total_value': int(row[2])}
            for row in rewards_breakdown_q.all()
        ]

        # 4. Статистика за сегодня (UTC)
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today_games_q = await db.execute(
            select(
                func.count(HalloweenGameLog.id),
                func.coalesce(func.sum(case((HalloweenGameLog.is_treat == True, 1), else_=0)), 0),
                func.coalesce(func.sum(case((HalloweenGameLog.is_treat == False, 1), else_=0)), 0),
            ).where(HalloweenGameLog.created_at >= today_start)
        )
        today_games, today_treats, today_tricks = today_games_q.one()

        today_claims_q = await db.execute(
            select(
                func.count(HalloweenExchangeLog.id),
                func.coalesce(func.sum(HalloweenExchangeLog.days_claimed), 0),
                func.coalesce(func.sum(HalloweenExchangeLog.minutes_spent), 0),
            ).where(HalloweenExchangeLog.created_at >= today_start)
        )
        today_claims_count, today_claims_days, today_claims_minutes = today_claims_q.one()

        # 5. Топ 10 игроков по заработанным минутам
        top_players_q = await db.execute(
            select(HalloweenUserState, User)
            .join(User, HalloweenUserState.user_id == User.id)
            .order_by(HalloweenUserState.earned_minutes.desc())
            .limit(10)
        )
        top_players = []
        for state_row, user_row in top_players_q.all():
            name = (
                f'@{user_row.username}'
                if user_row.username
                else (user_row.first_name or f'ID {user_row.telegram_id or user_row.id}')
            )
            top_players.append({
                'user_id': user_row.id,
                'name': name,
                'earned_minutes': state_row.earned_minutes,
                'level': state_row.level,
                'claimed_days': state_row.claimed_days,
            })

        # 6. Последние 15 игр
        recent_games_q = await db.execute(
            select(HalloweenGameLog, User)
            .join(User, HalloweenGameLog.user_id == User.id)
            .order_by(HalloweenGameLog.created_at.desc())
            .limit(15)
        )
        recent_games = []
        for log_row, user_row in recent_games_q.all():
            name = (
                f'@{user_row.username}'
                if user_row.username
                else (user_row.first_name or f'ID {user_row.telegram_id or user_row.id}')
            )
            recent_games.append({
                'id': log_row.id,
                'user_id': user_row.id,
                'user_name': name,
                'is_treat': log_row.is_treat,
                'chosen_pumpkin': log_row.chosen_pumpkin,
                'reward_type': log_row.reward_type,
                'reward_title': log_row.reward_title,
                'reward_value': log_row.reward_value,
                'created_at': log_row.created_at.isoformat() if log_row.created_at else None,
            })

        is_enabled = await cls.is_event_enabled(db)

        return {
            'is_enabled': is_enabled,
            'total_players': int(total_players or 0),
            'total_earned_minutes': int(total_earned_minutes or 0),
            'total_available_minutes': int(total_available_minutes or 0),
            'total_claimed_days': int(total_claimed_days or 0),
            'total_games': int(total_games or 0),
            'total_treats': int(total_treats or 0),
            'total_tricks': int(total_tricks or 0),
            'today_games': int(today_games or 0),
            'today_treats': int(today_treats or 0),
            'today_tricks': int(today_tricks or 0),
            'today_claims_days': int(today_claims_days or 0),
            'rewards_breakdown': rewards_breakdown,
            'top_players': top_players,
            'recent_games': recent_games,
        }

    @classmethod
    async def get_period_stats(cls, db: AsyncSession, start_utc: datetime, end_utc: datetime) -> dict:
        """Статистика за произвольный период для ежедневного отчета."""
        games_q = await db.execute(
            select(
                func.count(HalloweenGameLog.id),
                func.coalesce(func.sum(case((HalloweenGameLog.is_treat == True, 1), else_=0)), 0),
                func.coalesce(func.sum(case((HalloweenGameLog.is_treat == False, 1), else_=0)), 0),
            ).where(HalloweenGameLog.created_at >= start_utc, HalloweenGameLog.created_at < end_utc)
        )
        games_count, treats_count, tricks_count = games_q.one()

        # Минуты по начислениям от игр
        minutes_from_treats_q = await db.execute(
            select(func.coalesce(func.sum(HalloweenGameLog.reward_value), 0))
            .where(
                HalloweenGameLog.created_at >= start_utc,
                HalloweenGameLog.created_at < end_utc,
                HalloweenGameLog.reward_type == 'minutes',
            )
        )
        minutes_from_treats = int(minutes_from_treats_q.scalar() or 0)

        # Обмены на подписку
        claims_q = await db.execute(
            select(
                func.count(HalloweenExchangeLog.id),
                func.coalesce(func.sum(HalloweenExchangeLog.days_claimed), 0),
                func.coalesce(func.sum(HalloweenExchangeLog.minutes_spent), 0),
            ).where(HalloweenExchangeLog.created_at >= start_utc, HalloweenExchangeLog.created_at < end_utc)
        )
        claims_count, days_claimed, minutes_spent = claims_q.one()

        # Суммарно натапано за всё время
        total_earned_q = await db.execute(
            select(func.coalesce(func.sum(HalloweenUserState.earned_minutes), 0))
        )
        total_earned_all = int(total_earned_q.scalar() or 0)

        # Активных игроков за всё время
        total_players_q = await db.execute(select(func.count(HalloweenUserState.id)))
        total_players = int(total_players_q.scalar() or 0)

        return {
            'period_games': int(games_count or 0),
            'period_treats': int(treats_count or 0),
            'period_tricks': int(tricks_count or 0),
            'period_minutes_from_treats': minutes_from_treats,
            'period_claims_count': int(claims_count or 0),
            'period_days_claimed': int(days_claimed or 0),
            'total_earned_all': total_earned_all,
            'total_players': total_players,
        }


halloween_service = HalloweenService()
