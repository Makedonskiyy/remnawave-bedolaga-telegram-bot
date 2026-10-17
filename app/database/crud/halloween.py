from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import HalloweenUserState


async def get_halloween_state(db: AsyncSession, user_id: int) -> HalloweenUserState | None:
    """Получить состояние ивента для пользователя."""
    query = select(HalloweenUserState).where(HalloweenUserState.user_id == user_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_or_create_halloween_state(db: AsyncSession, user_id: int) -> HalloweenUserState:
    """Получить или инициализировать состояние ивента для пользователя."""
    state = await get_halloween_state(db, user_id)
    if not state:
        now = datetime.now(UTC)
        state = HalloweenUserState(
            user_id=user_id,
            earned_minutes=0,
            available_minutes=0,
            level=1,
            energy=1000,
            max_energy=1000,
            last_energy_updated_at=now,
            last_passive_collected_at=now,
            boost_active_until=None,
            is_cursed=False,
            cursed_frozen_minutes=0,
            last_trick_or_treat_at=None,
            claimed_days=0,
            created_at=now,
            updated_at=now,
        )
        db.add(state)
        await db.commit()
        await db.refresh(state)
    return state
