"""Admin routes for managing dedicated / personal servers in cabinet."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot_factory import create_bot
from app.database.models import DedicatedServerOrder, DedicatedServerStatus, User
from app.services.dedicated_server_service import DedicatedServerService

from ..dependencies import get_cabinet_db, require_permission
from ..schemas.dedicated_servers import (
    DedicatedServerAdminAssignRequest,
    DedicatedServerAdminRejectRequest,
    DedicatedServerListResponse,
    DedicatedServerOrderItem,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/servers/dedicated', tags=['Cabinet Admin Dedicated Servers'])


def _order_to_item(order: DedicatedServerOrder) -> DedicatedServerOrderItem:
    sub_url = None
    if order.subscription:
        sub_url = order.subscription.subscription_url

    setup_script = None
    if order.setup_token:
        setup_script = DedicatedServerService.get_setup_script(order)

    return DedicatedServerOrderItem(
        id=order.id,
        user_id=order.user_id,
        status=order.status,
        country_code=order.country_code,
        country_name=order.country_name,
        continent=order.continent,
        deployment_type=order.deployment_type,
        cpu_cores=order.cpu_cores,
        ram_gb=order.ram_gb,
        period_days=order.period_days,
        amount_kopeks=order.amount_kopeks,
        amount_rubles=round(order.amount_kopeks / 100, 2),
        options=order.options or {},
        ip_address=order.ip_address,
        squad_uuid=order.squad_uuid,
        subscription_id=order.subscription_id,
        subscription_url=sub_url,
        setup_token=order.setup_token,
        setup_script=setup_script,
        admin_notes=order.admin_notes,
        rejected_reason=order.rejected_reason,
        created_at=order.created_at,
        expires_at=order.expires_at,
    )


@router.get('', response_model=DedicatedServerListResponse)
async def list_dedicated_server_orders(
    status_filter: str | None = Query(None, alias='status'),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_permission('servers:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """List dedicated server orders with optional status filter."""
    stmt = (
        select(DedicatedServerOrder)
        .options(selectinload(DedicatedServerOrder.subscription))
        .order_by(desc(DedicatedServerOrder.created_at))
    )
    count_stmt = select(func.count(DedicatedServerOrder.id))

    if status_filter:
        stmt = stmt.where(DedicatedServerOrder.status == status_filter)
        count_stmt = count_stmt.where(DedicatedServerOrder.status == status_filter)

    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    orders_res = await db.execute(stmt.offset(offset).limit(limit))
    orders = orders_res.scalars().all()

    items = [_order_to_item(o) for o in orders]
    return DedicatedServerListResponse(orders=items, total=total)


@router.get('/{order_id}', response_model=DedicatedServerOrderItem)
async def get_dedicated_server_order(
    order_id: int,
    admin: User = Depends(require_permission('servers:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get single dedicated server order details."""
    stmt = (
        select(DedicatedServerOrder)
        .where(DedicatedServerOrder.id == order_id)
        .options(selectinload(DedicatedServerOrder.subscription))
    )
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Заказ #{order_id} не найден',
        )

    return _order_to_item(order)


@router.post('/{order_id}/assign', response_model=DedicatedServerOrderItem)
async def assign_and_activate_server(
    order_id: int,
    request: DedicatedServerAdminAssignRequest,
    admin: User = Depends(require_permission('servers:update')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Assign RemnaWave squad, IP address, and activate dedicated server."""
    bot = create_bot()
    try:
        order = await DedicatedServerService.assign_server(
            db=db,
            order_id=order_id,
            ip_address=request.ip_address,
            squad_uuid=request.squad_uuid,
            admin_notes=request.admin_notes,
            bot=bot,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error('Failed to assign dedicated server', error=e, order_id=order_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to activate dedicated server',
        )

    return _order_to_item(order)


@router.post('/{order_id}/reject', response_model=DedicatedServerOrderItem)
async def reject_dedicated_server_order(
    order_id: int,
    request: DedicatedServerAdminRejectRequest,
    admin: User = Depends(require_permission('servers:update')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Reject dedicated server order and refund funds to user balance."""
    bot = create_bot()
    try:
        order = await DedicatedServerService.reject_order(
            db=db,
            order_id=order_id,
            reason=request.reason,
            bot=bot,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error('Failed to reject dedicated server order', error=e, order_id=order_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to reject dedicated server order',
        )

    return _order_to_item(order)


@router.post('/{order_id}/status/{new_status}', response_model=DedicatedServerOrderItem)
async def update_order_status(
    order_id: int,
    new_status: str,
    admin: User = Depends(require_permission('servers:update')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update order lifecycle status (e.g. setting_up)."""
    valid_statuses = [s.value for s in DedicatedServerStatus]
    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Invalid status. Allowed: {valid_statuses}',
        )

    stmt = select(DedicatedServerOrder).where(DedicatedServerOrder.id == order_id)
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Заказ #{order_id} не найден',
        )

    order.status = new_status
    await db.commit()
    await db.refresh(order)
    return _order_to_item(order)
