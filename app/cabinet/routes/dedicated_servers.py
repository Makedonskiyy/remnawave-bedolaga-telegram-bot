"""User routes for dedicated / personal servers in cabinet."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import DedicatedServerOrder, User
from app.services.dedicated_server_service import DedicatedServerService

from ..dependencies import get_cabinet_db, get_current_cabinet_user
from ..schemas.dedicated_servers import (
    DedicatedServerConfigResponse,
    DedicatedServerListResponse,
    DedicatedServerOrderItem,
    DedicatedServerOrderRequest,
    DedicatedServerSetupScriptResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/dedicated-servers', tags=['Cabinet Dedicated Servers'])


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


@router.get('/config', response_model=DedicatedServerConfigResponse)
async def get_dedicated_server_config(
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get configuration, pricing, supported countries and marketing highlights."""
    await DedicatedServerService.get_pricing_config(db)
    config_data = DedicatedServerService.get_config_response()
    return DedicatedServerConfigResponse(**config_data)


@router.get('/my', response_model=DedicatedServerListResponse)
async def list_my_dedicated_servers(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """List all dedicated server orders for the current user."""
    stmt = (
        select(DedicatedServerOrder)
        .where(DedicatedServerOrder.user_id == user.id)
        .options(selectinload(DedicatedServerOrder.subscription))
        .order_by(desc(DedicatedServerOrder.created_at))
    )
    result = await db.execute(stmt)
    orders = result.scalars().all()

    items = [_order_to_item(o) for o in orders]
    return DedicatedServerListResponse(orders=items, total=len(items))


@router.post('/order', response_model=DedicatedServerOrderItem)
async def create_dedicated_server_order(
    request: DedicatedServerOrderRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Create and pay for a new dedicated server order from user balance."""
    try:
        order = await DedicatedServerService.create_order(
            db=db,
            user=user,
            country_code=request.country_code,
            period_days=request.period_days,
            deployment_type=request.deployment_type,
            options=request.options,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error('Failed to create dedicated server order', error=e, user_id=user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to process dedicated server order',
        )

    return _order_to_item(order)


@router.get('/script/{order_id}', response_model=DedicatedServerSetupScriptResponse)
async def get_dedicated_server_script(
    order_id: int,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get the one-line deployment script for a personal server order."""
    stmt = select(DedicatedServerOrder).where(
        DedicatedServerOrder.id == order_id,
        DedicatedServerOrder.user_id == user.id,
    )
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Заказ не найден',
        )

    script_cmd = DedicatedServerService.get_setup_script(order)
    return DedicatedServerSetupScriptResponse(
        order_id=order.id,
        install_command=script_cmd,
        manual_instructions=(
            'Выполните команду на чистом сервере Ubuntu 22.04 / 24.04 или Debian 12 с правами root. '
            'Скрипт автоматически настроит необходимые сетевые правила, ядро и подготовит узел к подключению к панели.'
        ),
    )


@router.get('/install/{install_token}', response_class=PlainTextResponse)
async def get_raw_dedicated_server_script(
    install_token: str,
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Raw bash setup script downloaded directly via curl on the client's VPS."""
    stmt = select(DedicatedServerOrder).where(DedicatedServerOrder.setup_token == install_token)
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Invalid installation token')

    script_content = f"""#!/bin/bash
# Dedicated Node Auto-provisioning Script for Order #{order.id}
# Location: {order.country_name} ({order.country_code})
set -e

echo "=== [Bedolaga VPN] Initializing Dedicated Node Setup ==="
echo "Order ID: {order.id}"
echo "Country: {order.country_name} ({order.country_code})"

apt-get update -qq
apt-get install -y -qq curl wget ufw ca-certificates iptables

# BBR kernel optimization
echo "net.core.default_qdisc=fq" >> /etc/sysctl.conf
echo "net.ipv4.tcp_congestion_control=bbr" >> /etc/sysctl.conf
sysctl -p > /dev/null 2>&1 || true

echo "=== Node network preparation completed. Contact support or wait for admin activation. ==="
"""
    return PlainTextResponse(content=script_content, media_type='text/plain')
