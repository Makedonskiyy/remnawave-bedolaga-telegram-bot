from pathlib import Path
import pytest

from app.services.banner_service import (
    ALL_BANNERS,
    BANNER_BALANCE,
    BANNER_MAIN,
    BANNER_REFERRAL,
    BANNER_SUBSCRIPTION_EXPIRED,
    BANNER_SUBSCRIPTION_EXPIRING,
    BANNER_TICKET,
    BANNER_WELCOME,
    detect_banner_from_callback,
    get_banner_media,
    get_banner_path,
    get_current_banner,
    normalize_banner_name,
    set_current_banner,
    banner_scope,
)


def test_banner_normalization():
    assert normalize_banner_name('start') == BANNER_WELCOME
    assert normalize_banner_name('welcome') == BANNER_WELCOME
    assert normalize_banner_name('reg') == BANNER_WELCOME
    assert normalize_banner_name('pay') == BANNER_BALANCE
    assert normalize_banner_name('deposit') == BANNER_BALANCE
    assert normalize_banner_name('support') == BANNER_TICKET
    assert normalize_banner_name('tickets') == BANNER_TICKET
    assert normalize_banner_name('expiring') == BANNER_SUBSCRIPTION_EXPIRING
    assert normalize_banner_name('expired') == BANNER_SUBSCRIPTION_EXPIRED
    assert normalize_banner_name('ref') == BANNER_REFERRAL
    assert normalize_banner_name(None) == BANNER_MAIN


def test_banner_detection_from_callback():
    assert detect_banner_from_callback('menu_referral') == BANNER_REFERRAL
    assert detect_banner_from_callback('ref_stats') == BANNER_REFERRAL
    assert detect_banner_from_callback('menu_support') == BANNER_TICKET
    assert detect_banner_from_callback('my_tickets') == BANNER_TICKET
    assert detect_banner_from_callback('balance_topup') == BANNER_BALANCE
    assert detect_banner_from_callback('deposit_method_sbp') == BANNER_BALANCE
    assert detect_banner_from_callback('sub_renew_30') == BANNER_SUBSCRIPTION_EXPIRING
    assert detect_banner_from_callback('back_to_menu') == BANNER_MAIN
    assert detect_banner_from_callback('lang_ru') == BANNER_WELCOME


def test_all_seven_banners_exist():
    for name in ALL_BANNERS:
        path = get_banner_path(name)
        assert path is not None, f"Banner {name} not found"
        assert path.is_file(), f"Banner {name} file does not exist: {path}"


def test_banner_scope():
    assert get_current_banner() is None
    with banner_scope(BANNER_TICKET):
        assert get_current_banner() == BANNER_TICKET
    assert get_current_banner() is None


def test_get_banner_media():
    media = get_banner_media(BANNER_MAIN)
    assert media is not None


def test_banner_path_traversal_safety():
    # Attempting directory traversal should be neutralized
    assert normalize_banner_name('../../etc/passwd') == 'etcpasswd'
    assert normalize_banner_name('..\\..\\windows\\win.ini') == 'windowswinini'
    # Resolving path with malicious input should either return fallback or None, never escape
    traversal_path = get_banner_path('../../../.env')
    if traversal_path:
        # If it returns fallback, it must be main or default logo
        assert traversal_path.name in ('banner_main.png', 'vpn_logo.png')


def test_banner_invalid_characters_sanitization():
    assert normalize_banner_name('welcome!@#$%^&*()') == 'welcome'
    assert normalize_banner_name('<script>alert(1)</script>') == 'scriptalert1script'

