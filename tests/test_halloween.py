import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.database.models import HalloweenUserState, User
from app.services.halloween_service import (
    HALLOWEEN_LEVELS,
    MAX_CLAIMABLE_DAYS,
    MINUTES_PER_DAY,
    halloween_service,
)


@pytest.fixture
def mock_user():
    return User(id=42, username='ghost_hunter', telegram_id=123456789)


@pytest.fixture
def mock_state(mock_user):
    now = datetime.now(UTC)
    return HalloweenUserState(
        id=1,
        user_id=mock_user.id,
        earned_minutes=0,
        available_minutes=0,
        level=1,
        energy=1000,
        max_energy=1000,
        last_energy_updated_at=now,
        last_passive_collected_at=now,
        boost_active_until=None,
        is_cursed=False,
        cursed_frozen_minutes=0,
        last_trick_or_treat_at=None,
        claimed_days=0,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_halloween_initial_state(mock_state):
    synced = halloween_service.sync_user_state(mock_state)
    assert synced['earned_minutes'] == 0
    assert synced['available_minutes'] == 0
    assert synced['level'] == 1
    assert synced['profit_per_tap'] == 1
    assert synced['passive_per_hour'] == 15
    assert synced['energy'] == 1000
    assert synced['is_cursed'] is False
    assert synced['claimed_days'] == 0
    assert synced['max_claimable_days'] == 14


@pytest.mark.asyncio
async def test_halloween_taps(mock_state):
    db = AsyncMock()
    result = await halloween_service.process_taps(db, mock_state, requested_taps=10)
    assert result['earned_minutes'] == 10
    assert result['available_minutes'] == 10
    assert result['energy'] == 990


@pytest.mark.asyncio
async def test_halloween_upgrade(mock_state):
    db = AsyncMock()
    # Level 1 upgrade costs 150 minutes
    mock_state.available_minutes = 200
    mock_state.earned_minutes = 200
    result = await halloween_service.upgrade_level(db, mock_state)
    assert result['level'] == 2
    assert result['available_minutes'] == 50
    assert result['profit_per_tap'] == 2
    assert result['passive_per_hour'] == 35


@pytest.mark.asyncio
async def test_halloween_trick_or_treat_curse_and_uncurse(mock_state):
    db = AsyncMock()
    mock_state.available_minutes = 100
    mock_state.earned_minutes = 100

    # Force trick/curse
    with patch('random.random', return_value=0.99):
        result = await halloween_service.play_trick_or_treat(db, mock_state, chosen_pumpkin=2)
        assert result['is_treat'] is False
        assert result['reward_type'] == 'curse'
        assert mock_state.is_cursed is True
        assert mock_state.cursed_frozen_minutes == 30
        assert mock_state.available_minutes == 70

    # While cursed, tapping should fail
    with pytest.raises(ValueError, match='заколдованы'):
        await halloween_service.process_taps(db, mock_state, requested_taps=5)

    # Uncurse
    uncurse_res = await halloween_service.uncurse(db, mock_state)
    assert uncurse_res['success'] is True
    assert mock_state.is_cursed is False
    assert mock_state.cursed_frozen_minutes == 0
    # 70 + (30 * 2) = 130
    assert mock_state.available_minutes == 130


@pytest.mark.asyncio
async def test_halloween_claim_reward_limit(mock_user, mock_state):
    db = AsyncMock()
    mock_state.available_minutes = 25000

    with patch('app.services.halloween_service.get_subscription_by_user_id', return_value=None), \
         patch('app.services.halloween_service.get_all_tariffs', return_value=[AsyncMock(id=1)]), \
         patch('app.services.halloween_service.create_subscription', return_value=AsyncMock(id=10)), \
         patch('app.services.halloween_service.SubscriptionService.create_remnawave_user', return_value=None):

        # Claim 7 days (cost: 7 * 1440 = 10080)
        res = await halloween_service.claim_reward_days(db, mock_user, mock_state, days=7)
        assert res['success'] is True
        assert res['days_added'] == 7
        assert mock_state.claimed_days == 7

        # Claim another 7 days (total 14)
        res2 = await halloween_service.claim_reward_days(db, mock_user, mock_state, days=7)
        assert res2['claimed_total'] == 14

        # Trying to claim more should fail due to 14 days limit
        with pytest.raises(ValueError, match='Превышен лимит'):
            await halloween_service.claim_reward_days(db, mock_user, mock_state, days=1)
