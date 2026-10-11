"""Рассылка с медиа из кабинета: то, что Telegram отвергнет, отклоняем на входе.

Кабинет создаёт рассылки через POST /admin/broadcasts/send. У старого
POST /admin/broadcasts проверка «подпись к медиа не длиннее 1024» была, у
/send — нет, при этом текст разрешён до 4000. Каждому получателю уходил
MEDIA_CAPTION_TOO_LONG, и это была одна из причин «failed = total» без
объяснений. Второй зазор — file_id не проверялся на форму вовсе.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status
from pydantic import ValidationError

from app.cabinet.routes.admin_broadcasts import create_combined_broadcast
from app.cabinet.schemas.broadcasts import BroadcastMediaRequest, CombinedBroadcastCreateRequest


FILE_ID = 'A' * 32


def _db() -> AsyncMock:
    db = AsyncMock()
    tariffs = MagicMock()
    tariffs.all.return_value = []
    db.execute.return_value = tariffs
    return db


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id=1, username='root')


async def test_send_rejects_media_caption_over_1024() -> None:
    request = CombinedBroadcastCreateRequest(
        channel='telegram',
        target='all',
        message_text='коротко',
        media=BroadcastMediaRequest(type='photo', file_id=FILE_ID, caption='x' * 1025),
    )

    with pytest.raises(HTTPException) as exc:
        await create_combined_broadcast(request=request, admin=_admin(), db=_db())

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert '1024' in str(exc.value.detail)


async def test_send_allows_long_message_text_with_media(monkeypatch) -> None:
    """Текст сообщения до 4000 символов разрешён с медиафайлом (отправка сплитом)."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock
    monkeypatch.setattr('app.services.broadcast_service.broadcast_service.start_broadcast', AsyncMock())

    request = CombinedBroadcastCreateRequest(
        channel='telegram',
        target='all',
        message_text='x' * 1227,
        media=BroadcastMediaRequest(type='photo', file_id=FILE_ID),
    )

    db = _db()
    async def fake_refresh(obj):
        obj.id = 1
        obj.created_at = datetime.now(UTC)
    db.refresh = AsyncMock(side_effect=fake_refresh)
    response = await create_combined_broadcast(request=request, admin=_admin(), db=db)
    assert response.has_media is True
    assert len(response.message_text) == 1227


def test_media_file_id_must_look_like_a_telegram_file_id() -> None:
    BroadcastMediaRequest(type='photo', file_id=FILE_ID)
    with pytest.raises(ValidationError):
        BroadcastMediaRequest(type='photo', file_id='https://example.com/photo.png')
    with pytest.raises(ValidationError):
        BroadcastMediaRequest(type='photo', file_id='')
