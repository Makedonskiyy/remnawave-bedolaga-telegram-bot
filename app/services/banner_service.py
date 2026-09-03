from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import urlparse

import structlog
from aiogram.types import FSInputFile, Message

from app.config import settings

logger = structlog.get_logger(__name__)

# Канонические имена баннеров
BANNER_WELCOME = 'welcome'
BANNER_MAIN = 'main'
BANNER_SUBSCRIPTION_EXPIRING = 'subscription_expiring'
BANNER_SUBSCRIPTION_EXPIRED = 'subscription_expired'
BANNER_TICKET = 'ticket'
BANNER_BALANCE = 'balance'
BANNER_REFERRAL = 'referral'

ALL_BANNERS = (
    BANNER_WELCOME,
    BANNER_MAIN,
    BANNER_SUBSCRIPTION_EXPIRING,
    BANNER_SUBSCRIPTION_EXPIRED,
    BANNER_TICKET,
    BANNER_BALANCE,
    BANNER_REFERRAL,
)

# Синонимы и алиасы
_ALIASES = {
    'start': BANNER_WELCOME,
    'reg': BANNER_WELCOME,
    'pay': BANNER_BALANCE,
    'deposit': BANNER_BALANCE,
    'topup': BANNER_BALANCE,
    'support': BANNER_TICKET,
    'tickets': BANNER_TICKET,
    'expiring': BANNER_SUBSCRIPTION_EXPIRING,
    'expired': BANNER_SUBSCRIPTION_EXPIRED,
    'ref': BANNER_REFERRAL,
    'partner': BANNER_REFERRAL,
}

_SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp')

# Telegram limits
_BANNER_MAX_DIMENSION = 1280
_BANNER_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BANNER_RESIZED_SUFFIX = '.bot_resized.png'

# Кеш file_id по имени баннера
_banner_file_ids: dict[str, str] = {}
# Кеш подготовленных путей файлов к отправке (после ресайза до 1280px)
_banner_send_paths: dict[str, Path] = {}

# ContextVar для неявной передачи текущего баннера внутри цепочки вызовов
_current_banner_ctx: ContextVar[str | None] = ContextVar('current_banner_ctx', default=None)


def normalize_banner_name(name: str | None) -> str:
    """Приводит имя баннера к каноническому виду."""
    if not name:
        return BANNER_MAIN
    clean = name.strip().lower()
    return _ALIASES.get(clean, clean)


def set_current_banner(name: str | None) -> None:
    """Устанавливает текущий баннер в ContextVar."""
    _current_banner_ctx.set(normalize_banner_name(name) if name else None)


def get_current_banner() -> str | None:
    """Возвращает текущий баннер из ContextVar."""
    return _current_banner_ctx.get()


@contextmanager
def banner_scope(name: str):
    """Контекстный менеджер для установки баннера на время выполнения блока."""
    token = _current_banner_ctx.set(normalize_banner_name(name))
    try:
        yield
    finally:
        _current_banner_ctx.reset(token)


def _get_banners_dirs() -> list[Path]:
    """Список возможных директорий с баннерами."""
    candidates = []
    if getattr(settings, 'BANNERS_DIR', None):
        candidates.append(Path(settings.BANNERS_DIR))
    candidates.append(Path('banners'))
    candidates.append(Path('/app/banners'))
    candidates.append(Path(__file__).resolve().parent.parent.parent / 'banners')
    return candidates


def get_banner_path(banner_name: str | None = None) -> Path | None:
    """Находит файл баннера на диске с фоллбэком на main или default logo."""
    target_name = normalize_banner_name(banner_name)
    dirs = _get_banners_dirs()

    # 1. Ищем точное совпадение: banner_{target_name}.ext или {target_name}.ext
    for d in dirs:
        if not d.exists() or not d.is_dir():
            continue
        for ext in _SUPPORTED_EXTENSIONS:
            p1 = d / f'banner_{target_name}{ext}'
            if p1.is_file():
                return p1
            p2 = d / f'{target_name}{ext}'
            if p2.is_file():
                return p2

    # 2. Фоллбэк: если запрашивался не main, пробуем banner_main
    if target_name != BANNER_MAIN:
        for d in dirs:
            if not d.exists() or not d.is_dir():
                continue
            for ext in _SUPPORTED_EXTENSIONS:
                p_main = d / f'banner_main{ext}'
                if p_main.is_file():
                    return p_main

    # 3. Фоллбэк на стандартный логотип бота (settings.LOGO_FILE)
    logo_path = Path(settings.LOGO_FILE)
    if logo_path.is_file():
        return logo_path

    return None


def _prepare_banner_for_send(path: Path) -> Path:
    """Оптимизирует и ресайзит баннер до 1280px для мгновенной отправки в Telegram."""
    try:
        size = path.stat().st_size
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
            needs_resize = (
                size > _BANNER_MAX_BYTES or max(width, height) > _BANNER_MAX_DIMENSION or (width + height) > 10000
            )
            if not needs_resize:
                return path

            cache_key = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:10]
            resized_path = Path(tempfile.gettempdir()) / f'{path.stem}.{cache_key}{_BANNER_RESIZED_SUFFIX}'
            if (
                resized_path.exists()
                and resized_path.stat().st_mtime >= path.stat().st_mtime
                and resized_path.stat().st_size <= _BANNER_MAX_BYTES
            ):
                return resized_path

            resized = img.copy()
            if resized.mode in ('RGBA', 'LA', 'P'):
                if resized.mode == 'P':
                    resized = resized.convert('RGBA')
            else:
                resized = resized.convert('RGB')
            resized.thumbnail((_BANNER_MAX_DIMENSION, _BANNER_MAX_DIMENSION), Image.Resampling.LANCZOS)
            resized.save(resized_path, format='PNG', optimize=True)
            logger.info(
                'Banner resized for Telegram',
                src=str(path),
                dst=str(resized_path),
                dimensions=resized.size,
            )
            return resized_path
    except Exception as exc:
        logger.warning('Banner resize preflight skipped', path=str(path), error=str(exc))
        return path


def get_banner_media(banner_name: str | None = None) -> FSInputFile | str | None:
    """Возвращает кешированный file_id или FSInputFile для баннера."""
    canonical = normalize_banner_name(banner_name or get_current_banner())

    # Быстрый возврат по file_id
    if canonical in _banner_file_ids:
        return _banner_file_ids[canonical]

    path = get_banner_path(canonical)
    if not path:
        return None

    global _banner_send_paths
    if canonical not in _banner_send_paths:
        _banner_send_paths[canonical] = _prepare_banner_for_send(path)

    return FSInputFile(_banner_send_paths[canonical])


def cache_banner_file_id(banner_name: str | None, result: Message | None) -> None:
    """Кеширует file_id баннера после успешной отправки Telegram."""
    if not result or not hasattr(result, 'photo') or not result.photo:
        return
    canonical = normalize_banner_name(banner_name or get_current_banner())
    if canonical not in _banner_file_ids:
        _banner_file_ids[canonical] = result.photo[-1].file_id


def get_banner_url(banner_name: str | None = None) -> str:
    """Публичный HTTP(S) URL баннера для rich-сообщений (FastAPI эндпоинт)."""
    canonical = normalize_banner_name(banner_name or get_current_banner())
    webhook_url = (settings.WEBHOOK_URL or '').strip()
    if not webhook_url:
        return ''
    parsed = urlparse(webhook_url)
    if not parsed.scheme or not parsed.netloc:
        return ''
    return f'{parsed.scheme}://{parsed.netloc}/cabinet/branding/banner/{canonical}'


def detect_banner_from_callback(callback_data: str | None, default: str = BANNER_MAIN) -> str:
    """Определяет подходящий баннер по callback_data."""
    if not callback_data:
        return default
    cb = callback_data.lower()

    if any(k in cb for k in ('ref', 'partner', 'invite', 'withdraw')):
        return BANNER_REFERRAL
    if any(k in cb for k in ('ticket', 'support', 'help')):
        return BANNER_TICKET
    if any(k in cb for k in ('balance', 'topup', 'deposit', 'pay_', 'crypto', 'kassa', 'yoo', 'heleket')):
        return BANNER_BALANCE
    if any(k in cb for k in ('renew', 'extend', 'expire')):
        return BANNER_SUBSCRIPTION_EXPIRING
    if any(k in cb for k in ('start', 'welcome', 'lang_', 'rules')):
        return BANNER_WELCOME
    if any(k in cb for k in ('menu', 'main', 'back_to_menu')):
        return BANNER_MAIN

    return default
