"""Форматирование и отправка опросов в виде rich-статей (Bot API 10.3).

Повторяет визуальный язык главного меню и rich-уведомлений:
- Шапка с логотипом (если настроен LOGO_FILE / MAIN_MENU_RICH_LOGO_URL)
- Заголовок <h4>🗳️ Название опроса</h4>
- Тонкий разделитель <hr/>
- Блоки описания с поддержкой абзацев <p>, одинарных переносов <br>,
  выделения <b>, <i>, <code>, ссылок <a>, спойлеров <tg-spoiler>
  и нативных цитат <blockquote>
- Блок вознаграждения с премиум-эмодзи 💰 (5447285164428272967)
- Подвал <footer> с подсказкой к действию
- Инлайн-кнопки переносятся внутрь rich-полотна (<tg-button-row>) при
  MAIN_MENU_RICH_INLINE_BUTTONS или остаются под сообщением.

При любой ошибке или недоступности rich-режима вызывающий код прозрачно
откатывается на классические HTML-сообщения.
"""

from __future__ import annotations

import asyncio
import html
import re

import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
)
from aiogram.methods import EditMessageText
from aiogram.types import (
    InaccessibleMessage,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
)

from app.config import settings
from app.database.models import Poll, PollQuestion
from app.localization.texts import get_texts
from app.utils.rich_admin import RICH_TEXT_LIMIT
from app.utils.rich_menu import (
    _apply_inline_buttons,
    _input_rich_message,
    _is_media_fetch_error,
    _looks_like_unsupported,
    _mark_logo_unavailable_once,
    _mark_rich_unavailable,
    _resolve_rich_logo_url,
    is_rich_menu_enabled,
)
from app.utils.validators import sanitize_html

logger = structlog.get_logger(__name__)

# Кастомный премиум-эмодзи монеты/баланса
MONEY_CUSTOM_EMOJI_ID = '5447285164428272967'

_SPOILER_SPAN_RE = re.compile(
    r'<span\s+class=(["\'])tg-spoiler\1[^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
_SPAN_TAG_RE = re.compile(r'</?span[^>]*>', re.IGNORECASE)
_IMG_TAG_RE = re.compile(r'<img[^>]*/?>', re.IGNORECASE)
_BLANK_LINE_RE = re.compile(r'\n\s*\n+')
_BLOCKQUOTE_RE = re.compile(r'(<blockquote\b[^>]*>.*?</blockquote>)', re.IGNORECASE | re.DOTALL)


def _format_description_to_rich_blocks(text: str) -> list[str]:
    """Разбивает текст описания/вопроса на rich-блоки (<p>, <blockquote>).

    Поддерживает:
    - Абзацы через пустые строки (двойной перенос -> <p>)
    - Одиночные переносы строк (одиночный перенос -> <br>)
    - Цитаты <blockquote>...</blockquote> (сохраняются цельным блоком с <br>)
    - Разрешённые inline-теги: <b>, <i>, <code>, <a>, <tg-spoiler>, <tg-emoji>
    """
    if not text or not text.strip():
        return []

    # 1. Безопасная санитизация HTML
    sanitized = sanitize_html(html.escape(text.strip()))

    # 2. Конвертация спойлеров в родной rich-тег и очистка лишних span/img
    sanitized = _SPOILER_SPAN_RE.sub(r'<tg-spoiler>\2</tg-spoiler>', sanitized)
    sanitized = _SPAN_TAG_RE.sub('', sanitized)
    sanitized = _IMG_TAG_RE.sub('', sanitized)

    blocks: list[str] = []

    # 3. Разделяем текст по тегам <blockquote>...</blockquote>
    parts = _BLOCKQUOTE_RE.split(sanitized)
    for part in parts:
        if not part:
            continue

        part_match = _BLOCKQUOTE_RE.match(part)
        if part_match:
            # Блок цитаты: переносим переносы строк в <br>
            quote_m = re.match(r'^(<blockquote\b[^>]*>)(.*?)(</blockquote>)$', part, re.IGNORECASE | re.DOTALL)
            if quote_m:
                open_tag, content, close_tag = quote_m.groups()
                # Убираем крайние \n и заменяем внутренние \n на <br>
                cleaned_content = content.strip('\n').replace('\n', '<br>')
                blocks.append(f'{open_tag}{cleaned_content}{close_tag}')
            else:
                blocks.append(part.strip())
        else:
            # Обычный текст: разделяем на абзацы по пустым строкам
            paragraphs = [chunk.strip('\n') for chunk in _BLANK_LINE_RE.split(part)]
            for p in paragraphs:
                cleaned_p = p.strip()
                if cleaned_p:
                    blocks.append(f'<p>{cleaned_p.replace(chr(10), "<br>")}</p>')

    return blocks


def build_poll_invitation_rich_html(
    poll: Poll,
    language: str,
    *,
    logo_url: str = '',
) -> str | None:
    """Собирает rich-HTML приглашения к участию в опросе."""
    texts = get_texts(language)
    blocks: list[str] = []

    # 1. Шапка с логотипом
    if logo_url:
        blocks.append(f'<img src="{html.escape(logo_url, quote=True)}"/>')

    # 2. Заголовок
    clean_title = html.escape(poll.title.strip())
    title_display = clean_title if clean_title.startswith('🗳') else f'🗳️ {clean_title}'
    blocks.append(f'<h4>{title_display}</h4>')
    blocks.append('<hr/>')

    # 3. Описание опроса
    if poll.description:
        desc_blocks = _format_description_to_rich_blocks(poll.description)
        blocks.extend(desc_blocks)

    # 4. Вознаграждение
    if poll.reward_enabled and poll.reward_amount_kopeks > 0:
        reward_formatted = f'<b>{html.escape(settings.format_price(poll.reward_amount_kopeks))}</b>'
        reward_template = texts.t(
            'POLL_INVITATION_REWARD',
            '🎁 За участие вы получите {amount}.',
        )
        reward_line = reward_template.format(amount=reward_formatted)
        # Заменяем обычный 🎁 на кастомный премиум-эмодзи 💰
        if reward_line.startswith('🎁'):
            reward_line = f'<tg-emoji emoji-id="{MONEY_CUSTOM_EMOJI_ID}">💰</tg-emoji>' + reward_line[1:]
        elif not reward_line.startswith('<tg-emoji'):
            reward_line = f'<tg-emoji emoji-id="{MONEY_CUSTOM_EMOJI_ID}">💰</tg-emoji> {reward_line}'
        blocks.append(f'<p>{reward_line}</p>')

    # 5. Подвал с призывом к действию
    action_prompt = texts.t(
        'POLL_INVITATION_START',
        'Нажмите кнопку ниже, чтобы пройти опрос.',
    )
    blocks.append('<hr/>')
    blocks.append(f'<footer>{html.escape(action_prompt)}</footer>')

    rich_html = ''.join(blocks)
    if len(rich_html) > RICH_TEXT_LIMIT:
        return None

    return rich_html


def build_poll_question_rich_html(
    poll_title: str,
    question: PollQuestion,
    current_index: int,
    total: int,
    language: str,
    *,
    logo_url: str = '',
) -> str:
    """Собирает rich-HTML экрана с вопросом опроса."""
    texts = get_texts(language)
    blocks: list[str] = []

    if logo_url:
        blocks.append(f'<img src="{html.escape(logo_url, quote=True)}"/>')

    clean_title = html.escape(poll_title.strip())
    title_display = clean_title if clean_title.startswith('🗳') else f'🗳️ {clean_title}'
    blocks.append(f'<h4>{title_display}</h4>')
    blocks.append('<hr/>')

    # Счётчик вопросов
    header_text = texts.t('POLL_QUESTION_HEADER', 'Вопрос {current}/{total}').format(
        current=current_index,
        total=total,
    )
    # Убираем теги <b> из локализованной строки счётчика, так как он идёт в <h6>
    header_clean = re.sub(r'</?b>', '', header_text)
    blocks.append(f'<h6>{html.escape(header_clean)}</h6>')

    # Текст вопроса
    question_blocks = _format_description_to_rich_blocks(question.text)
    blocks.extend(question_blocks)

    return ''.join(blocks)


def build_poll_completed_rich_html(
    poll_title: str,
    reward_amount: int | None,
    language: str,
    *,
    logo_url: str = '',
) -> str:
    """Собирает rich-HTML экрана завершения опроса."""
    texts = get_texts(language)
    blocks: list[str] = []

    if logo_url:
        blocks.append(f'<img src="{html.escape(logo_url, quote=True)}"/>')

    clean_title = html.escape(poll_title.strip())
    title_display = clean_title if clean_title.startswith('🗳') else f'🗳️ {clean_title}'
    blocks.append(f'<h4>{title_display}</h4>')
    blocks.append('<hr/>')

    thanks_text = texts.t('POLL_COMPLETED', '🙏 Спасибо за участие в опросе!')
    blocks.append(f'<h4>{html.escape(thanks_text)}</h4>')

    if reward_amount:
        reward_formatted = f'<b>{html.escape(settings.format_price(reward_amount))}</b>'
        reward_line = texts.t(
            'POLL_REWARD_GRANTED',
            'Награда {amount} зачислена на ваш баланс.',
        ).format(amount=reward_formatted)
        blocks.append(f'<p><tg-emoji emoji-id="{MONEY_CUSTOM_EMOJI_ID}">💰</tg-emoji> {reward_line}</p>')

    return ''.join(blocks)


async def try_send_rich_poll_invitation(
    bot: Bot,
    chat_id: int,
    poll: Poll,
    keyboard: InlineKeyboardMarkup,
    language: str,
    *,
    with_logo: bool = True,
    timeout: float | None = None,
) -> bool:
    """Шлёт приглашение к опросу в виде rich-сообщения.

    Возвращает False, если rich недоступен или произошла ошибка (вызывающий код
    должен отправить классическое сообщение).
    """
    if not is_rich_menu_enabled():
        return False

    logo_url = _resolve_rich_logo_url() if with_logo else ''
    rich_html = build_poll_invitation_rich_html(poll, language, logo_url=logo_url)
    if rich_html is None or len(rich_html) > RICH_TEXT_LIMIT:
        return False

    rich_html, reply_markup = _apply_inline_buttons(rich_html, keyboard)

    kwargs: dict = {
        'chat_id': chat_id,
        'rich_message': _input_rich_message(rich_html, language),
    }
    if reply_markup is not None:
        kwargs['reply_markup'] = reply_markup

    try:
        if timeout is not None:
            await asyncio.wait_for(bot.send_rich_message(**kwargs), timeout=timeout)
        else:
            await bot.send_rich_message(**kwargs)
        return True
    except TimeoutError:
        raise
    except TelegramForbiddenError:
        return False
    except (TelegramNotFound, TelegramBadRequest) as error:
        if logo_url and _is_media_fetch_error(error):
            _mark_logo_unavailable_once(error)
            return await try_send_rich_poll_invitation(
                bot, chat_id, poll, keyboard, language, with_logo=False, timeout=timeout
            )
        if _looks_like_unsupported(error):
            _mark_rich_unavailable(error)
            return False
        logger.warning('Rich-опрос не отправлен, фоллбек на классику', error=str(error), chat_id=chat_id)
        return False
    except TelegramNetworkError as error:
        logger.warning('Сетевая ошибка при отправке rich-опроса', error=str(error), chat_id=chat_id)
        return False
    except Exception as error:
        logger.warning('Непредвиденная ошибка rich-опроса', error=str(error), chat_id=chat_id)
        return False


async def try_edit_rich_poll_message(
    message: Message,
    rich_html: str,
    keyboard: InlineKeyboardMarkup | None,
    language: str | None,
) -> bool:
    """Редактирует сообщение опроса через rich_message (Bot API 10.1+).

    Возвращает False, если rich-редактирование не удалось (вызывающий код
    переходит на classic edit / delete+send).
    """
    if not is_rich_menu_enabled() or not rich_html or len(rich_html) > RICH_TEXT_LIMIT:
        return False

    bot = message.bot
    if bot is None:
        return False

    chat_id = message.chat.id

    if keyboard is not None:
        rich_html, reply_markup = _apply_inline_buttons(rich_html, keyboard, for_edit=True)
    else:
        reply_markup = None

    is_editable_as_rich = (
        not isinstance(message, InaccessibleMessage)
        and not getattr(message, 'photo', None)
        and (message.text is not None or getattr(message, 'rich_message', None) is not None)
    )

    try:
        if is_editable_as_rich:
            await bot(
                EditMessageText(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    rich_message=_input_rich_message(rich_html, language),
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
            )
        else:
            if not isinstance(message, InaccessibleMessage):
                try:
                    await message.delete()
                except Exception:
                    pass
            kwargs: dict = {
                'chat_id': chat_id,
                'rich_message': _input_rich_message(rich_html, language),
            }
            if reply_markup is not None:
                kwargs['reply_markup'] = reply_markup
            await bot.send_rich_message(**kwargs)
        return True
    except TelegramForbiddenError:
        return False
    except TelegramBadRequest as error:
        error_text = str(error).lower()
        if 'message is not modified' in error_text:
            return True
        if _looks_like_unsupported(error):
            _mark_rich_unavailable(error)
            return False
        logger.warning('Не удалось отредактировать rich-сообщение опроса', error=str(error), chat_id=chat_id)
        return False
    except Exception as error:
        logger.warning('Непредвиденная ошибка редактирования rich-опроса', error=str(error), chat_id=chat_id)
        return False
