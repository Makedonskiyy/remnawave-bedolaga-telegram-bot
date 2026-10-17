"""API роуты Хэллоуинского ивента (тапалка, сладость или гадость, награды)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.cabinet.dependencies import get_cabinet_db, get_current_cabinet_user
from app.database.crud.halloween import get_or_create_halloween_state
from app.database.models import User
from app.services.halloween_service import halloween_service


router = APIRouter(prefix='/halloween', tags=['Halloween Event'])


class TapRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=50)


class TrickOrTreatRequest(BaseModel):
    chosen_pumpkin: int = Field(default=1, ge=1, le=3)


class ClaimRewardRequest(BaseModel):
    days: int = Field(default=1, ge=1, le=14)


@router.get('/state')
async def get_state(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Получить текущее состояние игрока в Хэллоуинском ивенте."""
    state = await get_or_create_halloween_state(db, user.id)
    return halloween_service.sync_user_state(state)


@router.post('/tap')
async def process_tap(
    request: TapRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Тап по тыкве: списывает энергию и начисляет минуты."""
    state = await get_or_create_halloween_state(db, user.id)
    try:
        return await halloween_service.process_taps(db, state, request.count)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post('/upgrade')
async def upgrade_level(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Улучшить уровень тыквы за нафармленные минуты."""
    state = await get_or_create_halloween_state(db, user.id)
    try:
        return await halloween_service.upgrade_level(db, state)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post('/trick-or-treat')
async def play_trick_or_treat(
    request: TrickOrTreatRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Сыграть в 'Сладость или гадость' (выбрать одну из 3 тыкв)."""
    state = await get_or_create_halloween_state(db, user.id)
    try:
        return await halloween_service.play_trick_or_treat(db, state, request.chosen_pumpkin)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post('/uncurse')
async def uncurse_user(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Снять проклятие через шеринг с другом и вернуть замороженные минуты x2."""
    state = await get_or_create_halloween_state(db, user.id)
    return await halloween_service.uncurse(db, state)


@router.post('/claim')
async def claim_reward(
    request: ClaimRewardRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Обменять заработанные минуты на реальные дни подписки VPN (до 14 дней)."""
    state = await get_or_create_halloween_state(db, user.id)
    try:
        return await halloween_service.claim_reward_days(db, user, state, request.days)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
