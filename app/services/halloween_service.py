from datetime import UTC, datetime, timedelta
import random
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.subscription import (
    create_subscription,
    extend_subscription,
    get_subscription_by_user_id,
)
from app.database.crud.tariff import get_all_tariffs
from app.database.models import HalloweenUserState, Subscription, User
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
            # Ограничиваем окно 3 часами
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

        # 4. Кулдаун "Сладость или гадость"
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
        requested_taps = min(requested_taps, 50)  # лимит за один батч-запрос

        now = datetime.now(UTC)

        # Регенерация энергии перед списанием
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
        """Сыграть в 'Сладость или гадость' (выбор из 3 тыкв)."""
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

        if is_treat:
            # Награды сладости:
            # 1: +60 минут (45%)
            # 2: Буст x2 на 1 час (35%)
            # 3: Промокод на скидку 25% (20%)
            prize_roll = random.random()
            if prize_roll < 0.45:
                bonus = 60
                state.earned_minutes += bonus
                state.available_minutes += bonus
                result_data['reward_type'] = 'minutes'
                result_data['reward_title'] = f'+{bonus} минут к твоему балансу'
                result_data['reward_value'] = bonus
            elif prize_roll < 0.80:
                state.boost_active_until = now + timedelta(hours=1)
                result_data['reward_type'] = 'boost'
                result_data['reward_title'] = 'Буст дохода в тапалке x2 на 1 час'
                result_data['reward_value'] = 3600
            else:
                promo_code = f'HALLOWEEN25-{random.randint(1000, 9999)}'
                result_data['reward_type'] = 'promo'
                result_data['reward_title'] = 'Промокод на скидку 25%'
                result_data['reward_value'] = promo_code
        else:
            # Гадость: Проклятие! Замораживаем 30% доступных минут
            freeze = max(15, int(state.available_minutes * 0.30))
            freeze = min(freeze, state.available_minutes)
            state.available_minutes -= freeze
            state.cursed_frozen_minutes = freeze
            state.is_cursed = True

            result_data['reward_type'] = 'curse'
            result_data['reward_title'] = 'Бу! Тебя заколдовали! 🕷️'
            result_data['frozen_minutes'] = freeze

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
        state.earned_minutes += state.cursed_frozen_minutes  # бонус тоже плюсуется к общему заработку
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
        """Обмен нафармленных минут на дни реальной VPN подписки."""
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

        # Продлеваем существующую подписку или создаём новую
        sub = await get_subscription_by_user_id(db, user.id)
        if sub:
            await extend_subscription(db, sub, days)
            logger.info('🎃 Хэллоуин: продлена подписка на дни', user_id=user.id, days=days)
        else:
            # Если у пользователя нет подписки, берем базовый тариф
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
            # Синхронизация с RemnaWave
            try:
                sub_service = SubscriptionService()
                await sub_service.create_remnawave_user(db, sub)
            except Exception as e:
                logger.error('⚠️ Ошибка RemnaWave при создании хэллоуин-подписки', error=e, user_id=user.id)

        # Списываем минуты и обновляем счетчик
        state.available_minutes -= required_minutes
        state.claimed_days += days
        state.updated_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(state)

        return {
            'success': True,
            'days_added': days,
            'claimed_total': state.claimed_days,
            'message': f'Успешно начислено +{days} дн. подписки VPN!',
            'state': cls.sync_user_state(state),
        }


halloween_service = HalloweenService()
