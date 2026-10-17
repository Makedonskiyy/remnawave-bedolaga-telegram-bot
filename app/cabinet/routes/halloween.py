"""API роуты Хэллоуинского ивента (тапалка, сладость или гадость, награды, админка и статистика)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.cabinet.dependencies import (
    get_cabinet_db,
    get_current_admin_user,
    get_current_cabinet_user,
    get_optional_cabinet_user,
)
from app.database.crud.halloween import get_or_create_halloween_state
from app.database.models import User
from app.services.halloween_service import halloween_service
from app.services.rbac_bootstrap_service import is_user_admin_by_env


router = APIRouter(prefix='/halloween', tags=['Halloween Event'])


class TapRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=50)


class TrickOrTreatRequest(BaseModel):
    chosen_pumpkin: int = Field(default=1, ge=1, le=3)


class ClaimRewardRequest(BaseModel):
    days: int = Field(default=1, ge=1, le=14)


class ToggleEventRequest(BaseModel):
    enabled: bool


async def _check_user_is_admin(user: User | None, db: AsyncSession) -> bool:
    if not user:
        return False
    if is_user_admin_by_env(user).is_admin:
        return True
    try:
        from app.database.crud.rbac import UserRoleCRUD

        _permissions, _role_names, max_level = await UserRoleCRUD.get_user_permissions(db, user.id)
        return max_level > 0
    except Exception:
        return False


async def _ensure_event_accessible(user: User, db: AsyncSession) -> None:
    is_enabled = await halloween_service.is_event_enabled(db)
    if is_enabled:
        return
    is_admin = await _check_user_is_admin(user, db)
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Хэллоуинский ивент еще не запущен! Ожидайте официального старта.',
        )


# ============================================================================
# Публичные / Пользовательские эндпоинты
# ============================================================================


@router.get('/config')
async def get_event_config(
    user: User | None = Depends(get_optional_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Возвращает статус доступности ивента и права админа для текущего пользователя."""
    is_enabled = await halloween_service.is_event_enabled(db)
    is_admin = await _check_user_is_admin(user, db)
    remaining_seconds = halloween_service.get_event_remaining_seconds()

    return {
        'is_enabled': is_enabled,
        'is_admin': is_admin,
        'can_access': is_enabled or is_admin,
        'remaining_seconds': remaining_seconds,
    }


@router.get('/state')
async def get_state(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Получить текущее состояние игрока в Хэллоуинском ивенте."""
    await _ensure_event_accessible(user, db)
    state = await get_or_create_halloween_state(db, user.id)
    return halloween_service.sync_user_state(state)


@router.post('/tap')
async def process_tap(
    request: TapRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Тап по тыкве: списывает энергию и начисляет минуты."""
    await _ensure_event_accessible(user, db)
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
    await _ensure_event_accessible(user, db)
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
    await _ensure_event_accessible(user, db)
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
    await _ensure_event_accessible(user, db)
    state = await get_or_create_halloween_state(db, user.id)
    return await halloween_service.uncurse(db, state)


@router.post('/claim')
async def claim_reward(
    request: ClaimRewardRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Обменять заработанные минуты на реальные дни подписки VPN (до 14 дней)."""
    await _ensure_event_accessible(user, db)
    state = await get_or_create_halloween_state(db, user.id)
    try:
        return await halloween_service.claim_reward_days(db, user, state, request.days)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


# ============================================================================
# Админские эндпоинты управления и детальной аналитики
# ============================================================================


@router.get('/admin/stats')
async def get_admin_stats(
    _admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Получить сводную статистику по Хэллоуинскому ивенту (для админ-панели)."""
    return await halloween_service.get_admin_statistics(db)


@router.post('/admin/toggle')
async def toggle_event(
    request: ToggleEventRequest,
    _admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Включить или выключить видимость ивента для обычных пользователей."""
    new_status = await halloween_service.set_event_enabled(db, request.enabled)
    return {
        'success': True,
        'is_enabled': new_status,
        'message': 'Ивент включен для всех пользователей' if new_status else 'Ивент скрыт (доступен только администраторам)',
    }


@router.post('/admin/send-report')
async def send_admin_report(
    _admin: User = Depends(get_current_admin_user),
    _db: AsyncSession = Depends(get_cabinet_db),
):
    """Принудительно отправить ежедневный отчет по Хэллоуин-ивенту в Telegram админ-чат."""
    success = await halloween_service.send_daily_halloween_report()
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Не удалось отправить отчёт в Telegram (проверьте настройки ADMIN_NOTIFICATIONS_CHAT_ID)',
        )
    return {'success': True, 'message': 'Ежедневный отчёт по ивенту успешно отправлен в админ-чат!'}
