"""Tests for rich article HTML processing and classic fallback in broadcasts."""

import pytest
from app.utils.rich_notify import (
    build_broadcast_rich_html,
    broadcast_html_to_classic,
)


def test_broadcast_article_to_rich():
    """Verify conversion of article-style HTML with h1-h4, hr, p into rich format."""
    input_text = """<article>
<h1>Выделенные серверы</h1>
<hr>
<p>Запуск личных серверов без ограничений!</p>
<blockquote>Чистый IP без капч</blockquote>
<h3>Преимущества:</h3>
<ul>
  <li>Безлимитный трафик</li>
  <li>Без ограничений на устройства</li>
</ul>
</article>"""

    rich_html = build_broadcast_rich_html(input_text)
    assert rich_html is not None

    # article tag should be unpacked
    assert '<article>' not in rich_html
    assert '</article>' not in rich_html

    # Headings should be converted to <h4>
    assert '<h4>Выделенные серверы</h4>' in rich_html
    assert '<h4>Преимущества:</h4>' in rich_html
    assert '<hr/>' in rich_html

    # List items should be bullet points
    assert '• Безлимитный трафик' in rich_html
    assert '• Без ограничений на устройства' in rich_html

    # Blockquote preserved
    assert '<blockquote>' in rich_html


def test_broadcast_article_to_classic_safe():
    """Verify that classic fallback strips/transforms all tags unsupported by Telegram parse_mode='HTML'."""
    input_text = """<article>
<h1>Выделенные серверы</h1>
<hr/>
<p>Персональный сервер специально для вас.</p>
<h4>VIP-опции:</h4>
<ul>
  <li>Google Gemini и ChatGPT</li>
  <li>YouTube без рекламы</li>
</ul>
<div class="note"><b>100% изоляция</b></div>
</article>"""

    classic_html = broadcast_html_to_classic(input_text)

    # Must NOT contain tags that cause TelegramBadRequest:
    # "can't parse entities: Unsupported start tag"
    unsupported_tags = ['<article', '</article>', '<h1', '</h1', '<h2', '</h2', '<h3', '</h3',
                        '<h4', '</h4', '<h5', '</h5', '<h6', '</h6', '<p', '</p>', '<hr',
                        '<ul', '</ul', '<ol', '</ol>', '<li', '</li', '<div', '</div']
    for tag in unsupported_tags:
        assert tag not in classic_html.lower(), f"Forbidden tag '{tag}' found in classic HTML output!"

    # Headings must be converted to bold
    assert '<b>Выделенные серверы</b>' in classic_html
    assert '<b>VIP-опции:</b>' in classic_html

    # HR converted to line separator
    assert '───────────────' in classic_html

    # Bullets present
    assert '• Google Gemini и ChatGPT' in classic_html
    assert '• YouTube без рекламы' in classic_html

    # Bold content preserved
    assert '<b>100% изоляция</b>' in classic_html


def test_broadcast_classic_with_existing_telegram_html():
    """Verify that valid Telegram tags like b, i, code, a, blockquote are safely preserved."""
    input_text = (
        "<b>Важное объявление!</b>\n"
        "<i>Курсивный текст</i> и <code>код</code>.\n"
        "<a href='https://example.com'>Ссылка</a>\n"
        "<blockquote>Цитата</blockquote>"
    )

    classic_html = broadcast_html_to_classic(input_text)
    assert '<b>Важное объявление!</b>' in classic_html
    assert '<i>Курсивный текст</i>' in classic_html
    assert '<code>код</code>' in classic_html
    assert '<a href=' in classic_html
    assert '<blockquote>Цитата</blockquote>' in classic_html
