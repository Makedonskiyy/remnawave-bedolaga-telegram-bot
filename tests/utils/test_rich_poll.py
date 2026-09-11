"""Тесты для форматирования и отправки опросов в виде rich-статей (Bot API 10.3)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNotFound
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.database.models import Poll, PollQuestion
from app.services.poll_service import _build_poll_invitation_text, build_start_keyboard
from app.utils import rich_menu, rich_poll
from app.utils.rich_admin import RICH_TEXT_LIMIT
from app.utils.rich_poll import (
    MONEY_CUSTOM_EMOJI_ID,
    _format_description_to_rich_blocks,
    build_poll_completed_rich_html,
    build_poll_invitation_rich_html,
    build_poll_question_rich_html,
    try_edit_rich_poll_message,
    try_send_rich_poll_invitation,
)


@pytest.fixture(autouse=True)
def _reset_rich_flags(monkeypatch):
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_INLINE_BUTTONS', False, raising=False)
    rich_menu._reset_rich_menu_availability()


class TestFormatDescriptionToRichBlocks:
    def test_basic_paragraphs_and_linebreaks(self):
        text = 'Первый абзац\nпродолжение\n\nВторой абзац'
        blocks = _format_description_to_rich_blocks(text)

        assert len(blocks) == 2
        assert blocks[0] == '<p>Первый абзац<br>продолжение</p>'
        assert blocks[1] == '<p>Второй абзац</p>'

    def test_user_requested_exact_example(self):
        """Проверяем форматирование примера от пользователя."""
        raw_text = (
            'Привет! Мы обновили маршруты и хотим проверить стабильность прямо сейчас.\n\n'
            'Пройди опрос из 1 вопроса — это займёт меньше минуты, а мы начислим:\n'
            '<b>100 ₽ на твой баланс</b>\n\n'
            '<blockquote>Выбери вариант, который лучше всего описывает твою ситуацию:</blockquote>'
        )
        blocks = _format_description_to_rich_blocks(raw_text)

        assert len(blocks) == 3
        assert blocks[0] == '<p>Привет! Мы обновили маршруты и хотим проверить стабильность прямо сейчас.</p>'
        assert blocks[1] == '<p>Пройди опрос из 1 вопроса — это займёт меньше минуты, а мы начислим:<br><b>100 ₽ на твой баланс</b></p>'
        assert blocks[2] == '<blockquote>Выбери вариант, который лучше всего описывает твою ситуацию:</blockquote>'

    def test_blockquote_with_internal_newlines(self):
        text = 'Текст до\n\n<blockquote>Строка 1\nСтрока 2</blockquote>\n\nТекст после'
        blocks = _format_description_to_rich_blocks(text)

        assert len(blocks) == 3
        assert blocks[0] == '<p>Текст до</p>'
        assert blocks[1] == '<blockquote>Строка 1<br>Строка 2</blockquote>'
        assert blocks[2] == '<p>Текст после</p>'

    def test_spoiler_and_inline_tags(self):
        text = '<b>Жирный</b> <i>курсив</i> <code>код</code> <span class="tg-spoiler">секрет</span>'
        blocks = _format_description_to_rich_blocks(text)

        assert len(blocks) == 1
        assert '<b>Жирный</b>' in blocks[0]
        assert '<i>курсив</i>' in blocks[0]
        assert '<code>код</code>' in blocks[0]
        assert '<tg-spoiler>секрет</tg-spoiler>' in blocks[0]
        assert 'span' not in blocks[0]

    def test_strips_dangerous_tags(self):
        text = 'Обычный <script>alert(1)</script> текст <iframe src="evil.com"></iframe>'
        blocks = _format_description_to_rich_blocks(text)

        assert len(blocks) == 1
        # script и iframe экранируются и не становятся активными тегами
        assert '<script>' not in blocks[0]
        assert '&lt;script&gt;' in blocks[0]

    def test_empty_or_whitespace_returns_empty_list(self):
        assert _format_description_to_rich_blocks('') == []
        assert _format_description_to_rich_blocks('   \n\n  ') == []


class TestBuildPollInvitationRichHtml:
    def test_builds_full_invitation_with_reward_and_logo(self):
        poll = Poll(
            id=1,
            title='Как у тебя связь?',
            description='Привет!\n\n<blockquote>Выбери вариант:</blockquote>',
            reward_enabled=True,
            reward_amount_kopeks=10000,
        )
        html_content = build_poll_invitation_rich_html(poll, 'ru', logo_url='https://example.com/logo.png')

        assert html_content is not None
        assert html_content.startswith('<img src="https://example.com/logo.png"/>')
        assert '<h4>🗳️ Как у тебя связь?</h4>' in html_content
        assert '<hr/>' in html_content
        assert '<p>Привет!</p>' in html_content
        assert '<blockquote>Выбери вариант:</blockquote>' in html_content
        assert f'<tg-emoji emoji-id="{MONEY_CUSTOM_EMOJI_ID}">💰</tg-emoji>' in html_content
        assert '100 ₽' in html_content
        assert '<footer>' in html_content

    def test_does_not_duplicate_ballot_box_emoji_in_title(self):
        poll = Poll(
            id=2,
            title='🗳️ Опрос о качестве',
            description='Тест',
            reward_enabled=False,
            reward_amount_kopeks=0,
        )
        html_content = build_poll_invitation_rich_html(poll, 'ru')
        assert '<h4>🗳️ Опрос о качестве</h4>' in html_content
        assert '🗳️ 🗳️' not in html_content

    def test_oversized_text_returns_none(self):
        poll = Poll(
            id=3,
            title='Тест',
            description='Длинно ' * (RICH_TEXT_LIMIT // 4),
            reward_enabled=False,
            reward_amount_kopeks=0,
        )
        html_content = build_poll_invitation_rich_html(poll, 'ru')
        assert html_content is None


class TestBuildPollQuestionAndCompletedHtml:
    def test_build_poll_question_rich_html(self):
        question = PollQuestion(
            id=10,
            text='Как оцениваете скорость работы <b>VPN</b>?',
            options=[],
        )
        html_content = build_poll_question_rich_html('Опрос', question, 1, 3, 'ru')

        assert '<h4>🗳️ Опрос</h4>' in html_content
        assert '<hr/>' in html_content
        assert '<h6>Вопрос 1/3</h6>' in html_content
        assert '<p>Как оцениваете скорость работы <b>VPN</b>?</p>' in html_content

    def test_build_poll_completed_rich_html(self):
        html_content = build_poll_completed_rich_html('Опрос', 5000, 'ru')

        assert '<h4>🗳️ Опрос</h4>' in html_content
        assert '<hr/>' in html_content
        assert 'Спасибо за участие' in html_content
        assert f'<tg-emoji emoji-id="{MONEY_CUSTOM_EMOJI_ID}">💰</tg-emoji>' in html_content
        assert '50 ₽' in html_content


class TestTrySendRichPollInvitation:
    async def test_sends_rich_message_successfully(self):
        bot = AsyncMock()
        poll = Poll(
            id=1,
            title='Тест',
            description='Описание',
            reward_enabled=False,
            reward_amount_kopeks=0,
        )
        keyboard = build_start_keyboard(123, 'ru')

        success = await try_send_rich_poll_invitation(
            bot=bot,
            chat_id=111,
            poll=poll,
            keyboard=keyboard,
            language='ru',
            with_logo=False,
        )

        assert success is True
        bot.send_rich_message.assert_called_once()
        call_kwargs = bot.send_rich_message.call_args.kwargs
        assert call_kwargs['chat_id'] == 111
        assert '<h4>🗳️ Тест</h4>' in call_kwargs['rich_message'].html

    async def test_falls_back_when_rich_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, 'MAIN_MENU_RICH_ENABLED', False)
        bot = AsyncMock()
        poll = Poll(id=1, title='Тест', description='Описание', reward_enabled=False, reward_amount_kopeks=0)
        keyboard = build_start_keyboard(123, 'ru')

        success = await try_send_rich_poll_invitation(
            bot=bot,
            chat_id=111,
            poll=poll,
            keyboard=keyboard,
            language='ru',
        )

        assert success is False
        bot.send_rich_message.assert_not_called()

    async def test_retries_without_logo_on_media_error(self):
        bot = AsyncMock()
        bot.send_rich_message.side_effect = [
            TelegramBadRequest(method=MagicMock(), message='wrong type of the web page'),
            None,
        ]
        poll = Poll(id=1, title='Тест', description='Описание', reward_enabled=False, reward_amount_kopeks=0)
        keyboard = build_start_keyboard(123, 'ru')

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, 'MAIN_MENU_RICH_LOGO_URL', 'https://example.com/bad_logo.png')
            success = await try_send_rich_poll_invitation(
                bot=bot,
                chat_id=111,
                poll=poll,
                keyboard=keyboard,
                language='ru',
                with_logo=True,
            )

        assert success is True
        assert bot.send_rich_message.call_count == 2
        # Второй вызов без <img>
        second_call = bot.send_rich_message.call_args_list[1]
        assert '<img src=' not in second_call.kwargs['rich_message'].html

    async def test_marks_unavailable_on_unsupported_server(self):
        bot = AsyncMock()
        bot.send_rich_message.side_effect = TelegramNotFound(method=MagicMock(), message='Not Found')
        poll = Poll(id=1, title='Тест', description='Описание', reward_enabled=False, reward_amount_kopeks=0)
        keyboard = build_start_keyboard(123, 'ru')

        success = await try_send_rich_poll_invitation(
            bot=bot,
            chat_id=111,
            poll=poll,
            keyboard=keyboard,
            language='ru',
        )

        assert success is False
        assert rich_menu.is_rich_menu_enabled() is False


class TestTryEditRichPollMessage:
    async def test_edits_message_as_rich(self):
        bot = AsyncMock()
        message = MagicMock()
        message.bot = bot
        message.chat.id = 111
        message.message_id = 222
        message.text = 'Исходный текст'
        message.photo = None

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Да', callback_data='1')]])
        success = await try_edit_rich_poll_message(
            message=message,
            rich_html='<h4>Новый заголовок</h4>',
            keyboard=keyboard,
            language='ru',
        )

        assert success is True
        bot.assert_called_once()

    async def test_message_not_modified_returns_true(self):
        bot = AsyncMock()
        bot.side_effect = TelegramBadRequest(method=MagicMock(), message='message is not modified')
        message = MagicMock()
        message.bot = bot
        message.chat.id = 111
        message.message_id = 222
        message.text = 'Исходный текст'
        message.photo = None

        success = await try_edit_rich_poll_message(
            message=message,
            rich_html='<h4>Заголовок</h4>',
            keyboard=None,
            language='ru',
        )

        assert success is True


class TestClassicPollTextSanitization:
    def test_preserves_html_tags_in_poll_description(self):
        """Проверяем, что классический текст приглашения не экранирует теги HTML в &lt;."""
        poll = Poll(
            id=1,
            title='🗳️ Опрос о связи',
            description=(
                'Привет! Мы обновили маршруты.\n\n'
                '<b>100 ₽ на твой баланс</b>\n\n'
                '<blockquote>Выбери вариант:</blockquote>'
            ),
            reward_enabled=True,
            reward_amount_kopeks=10000,
        )
        text = _build_poll_invitation_text(poll, 'ru')

        # Теги должны присутствовать в готовом тексте для parse_mode='HTML'
        assert '<b>🗳️ Опрос о связи</b>' in text
        assert '<b>100 ₽ на твой баланс</b>' in text
        assert '<blockquote>Выбери вариант:</blockquote>' in text
        # И никаких &lt;b&gt;
        assert '&lt;b&gt;' not in text
        assert '&lt;blockquote&gt;' not in text
