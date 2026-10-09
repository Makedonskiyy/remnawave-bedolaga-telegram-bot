"""Tests for voice message support in tickets and cabinet."""

import pytest
from pydantic import ValidationError

from app.cabinet.routes.media import (
    ALLOWED_MEDIA_TYPES as MEDIA_ALLOWED_TYPES,
    _content_response_params,
)
from app.cabinet.routes.support_ws import _guess_media_type
from app.cabinet.schemas.tickets import (
    ALLOWED_MEDIA_TYPES as SCHEMA_ALLOWED_TYPES,
    TicketCreateRequest,
    TicketMediaItem,
    TicketMessageCreateRequest,
)
from app.cabinet.routes.admin_tickets import AdminReplyRequest


def test_allowed_media_types_include_voice():
    assert 'voice' in MEDIA_ALLOWED_TYPES
    assert 'voice' in SCHEMA_ALLOWED_TYPES


def test_ticket_create_with_voice_media():
    req = TicketCreateRequest(
        title='Help with voice',
        message='',
        media_type='voice',
        media_file_id='AwADBAADVoiceFileId123',
    )
    assert req.media_type == 'voice'
    assert req.media_file_id == 'AwADBAADVoiceFileId123'
    assert req.message == ''


def test_ticket_message_create_with_voice_media():
    req = TicketMessageCreateRequest(
        message='',
        media_type='voice',
        media_file_id='AwADBAADVoiceFileId123',
    )
    assert req.media_type == 'voice'
    assert req.media_file_id == 'AwADBAADVoiceFileId123'


def test_ticket_message_create_with_voice_items():
    req = TicketMessageCreateRequest(
        message='',
        media_items=[
            TicketMediaItem(type='voice', file_id='AwADBAADVoiceFileId123', caption='My voice note')
        ],
    )
    assert req.media_items[0].type == 'voice'


def test_admin_reply_with_voice():
    req = AdminReplyRequest(
        message='',
        media_type='voice',
        media_file_id='AwADBAADAdminVoice123',
    )
    assert req.media_type == 'voice'
    assert req.media_file_id == 'AwADBAADAdminVoice123'


def test_media_content_response_params_for_audio():
    media_type, headers = _content_response_params('voice_note.oga')
    assert media_type == 'audio/ogg'
    assert 'inline' in headers['Content-Disposition']
    assert "media-src 'self' data:" in headers['Content-Security-Policy']
    assert headers['Accept-Ranges'] == 'bytes'

    media_type_ogg, headers_ogg = _content_response_params('recording.ogg')
    assert media_type_ogg == 'audio/ogg'
    assert 'inline' in headers_ogg['Content-Disposition']

    media_type_mp3, headers_mp3 = _content_response_params('audio.mp3')
    assert media_type_mp3 == 'audio/mpeg'
    assert 'inline' in headers_mp3['Content-Disposition']


def test_support_ws_guess_media_type_audio():
    assert _guess_media_type('voice.ogg', 'audio/ogg', None) == 'voice'
    assert _guess_media_type('voice.webm', 'audio/webm', None) == 'voice'
    assert _guess_media_type(None, None, 'voice') == 'voice'
