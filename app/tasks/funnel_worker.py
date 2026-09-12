from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot

from app.database.database import AsyncSessionLocal
from app.services.funnel_service import FunnelService


logger = structlog.get_logger(__name__)

_worker_running: bool = False


def stop_funnel_worker() -> None:
    """Signals the funnel worker loop to stop gracefully."""
    global _worker_running
    _worker_running = False
    logger.info('Funnel worker stopping...')


async def start_funnel_worker(bot: Bot) -> None:
    """Background worker processing scheduled funnel actions every 60 seconds."""
    global _worker_running
    _worker_running = True
    logger.info('🚀 Фоновый воркер маркетинговых воронок запущен')

    while _worker_running:
        try:
            async with AsyncSessionLocal() as session:
                service = FunnelService(session, bot)
                processed = await service.process_pending_actions()
                if processed > 0:
                    logger.info('Обработаны действия в воронке продаж', count=processed)

        except asyncio.CancelledError:
            logger.info('Фоновый воркер воронки отменён')
            break
        except Exception as exc:
            logger.error('Ошибка в цикле фонового воркера воронки', exc_info=exc)

        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info('Фоновый воркер воронки завершил сон по сигналу отмены')
            break

    logger.info('Фоновый воркер маркетинговых воронок остановлен')
